"""采集汇总：一条命令看全局。

用法：
    python scripts/status_summary.py            # 人类可读汇总
    python scripts/status_summary.py --json     # 机器可读

只读，不改任何东西。采集照常跑，本脚本不碰 collect.lock。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 本脚本会 spawn wmic / ps / powershell 查进程状态，而自迭代每 20 分钟调它一次
# ——不装 winflags 就是桌面上一小时闪几次的黑框（2026-10-03 实测）。
# winflags 给 subprocess.Popen 打补丁，「没传 creationflags 就补
# CREATE_NO_WINDOW」，所以下面那几处 run() 不用逐个写参数。
from lighthouse.winflags import install as _install_no_window  # noqa: E402
_install_no_window()

RUNTIME = ROOT / "data" / "runtime" / "lighthouse"
DB = ROOT / "data" / "beacon.db"
EVENTS = RUNTIME / "events.jsonl"
BACKOFF = RUNTIME / "source_backoff.json"
STATUS = RUNTIME / "status.json"


def _proc_alive() -> dict[str, object]:
    """采集进程是否活着。

    读 status.json 的更新时间比列进程更可靠：watchdog 每轮都会写它，
    而进程列表在「watchdog 活着但正处在轮间节流睡眠」时会看起来像没在跑
    （那其实是正常的）。两者都给，交叉看最准。
    """
    out: dict[str, object] = {}
    try:
        if os.name == "nt":
            import subprocess

            # tasklist 只能给进程名，给不了命令行。要判断「watchdog 还在不在」
            # 必须看命令行，所以走 WMIC。
            # 两个坑（都踩过）：
            #  1. tasklist 输出是本地代码页（GBK），必须按 mbcs 解码，
            #     用默认 utf-8 会 UnicodeDecodeError。
            #  2. WMIC 的 /format:csv 里**命令行本身含逗号**（如
            #     `--directory C:\a,b`），不能简单 split(',') 取末段。
            #     正确做法：表头是 `Node,CommandLine,ProcessId`，命令行在
            #     **第一个逗号之后到倒数第二个逗号之前**。
            res = subprocess.run(
                # name like 'python%' —— 必须连 pythonw.exe 一起数。
                # 2026-10-06：无人值守链（watchdog/overnight/run.py）全由计划任务
                # 用 pythonw 拉起，按 name 精确等值 python.exe 查会一个都看不见，
                # 于是「采集明明在跑」却报「watchdog 进程不在」，--heal 还会再拉
                # 一个 watchdog 出来（双调度抢锁）。
                ["wmic", "process", "where", "name like 'python%'",
                 "get", "ProcessId,CommandLine", "/format:csv"],
                capture_output=True,
                encoding="mbcs" if sys.platform.startswith("win") else "utf-8",
                errors="replace", timeout=30)
            cmds: list[str] = []
            for ln in (res.stdout or "").splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("Node,"):
                    continue
                # 去掉尾部 ",<pid>"
                cut = ln.rfind(",")
                if cut <= 0:
                    continue
                cmds.append(ln[:cut].split(",", 1)[-1].strip('"'))
            out["python_processes"] = len(cmds)
            out["watchdog_alive"] = any("watchdog.py" in c for c in cmds)
            out["scheduler_alive"] = any(
                ("overnight.py" in c or "lighthouse" in c and "run.py" in c)
                for c in cmds)
        else:
            lines = [p for p in subprocess.run(
        ["ps", "-eo", "args"], capture_output=True, encoding="utf-8",
        errors="replace").stdout.splitlines()
                     if "python" in p]
            out["python_processes"] = len(lines)
            out["watchdog_alive"] = any("watchdog.py" in p for p in lines)
            out["scheduler_alive"] = any(
                ("overnight.py" in p or "lighthouse/run.py" in p) for p in lines)
    except Exception as exc:                            # noqa: BLE001
        out["python_processes"] = None
        out["watchdog_alive"] = None
        out["proc_error"] = "%s: %s" % (type(exc).__name__, exc)

    try:
        st = json.loads(STATUS.read_text(encoding="utf-8"))
        ts = str(st.get("updated_at") or "")
        out["status_age_min"] = (
            round((time.time() - __import__("datetime").datetime.fromisoformat(ts)
                   .timestamp()) / 60, 1) if ts else None)
    except Exception:                                   # noqa: BLE001
        out["status_age_min"] = None
    return out


def _db_counts() -> dict:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        total = con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        today = con.execute(
            "SELECT COUNT(*) FROM announcements WHERE "
            # 「今日」按**本地**日界切。列里存的是 UTC（storage/models.py 的
            # utcnow()），而 date('now') 也是 UTC —— 两者一致，但给人看的
            # 「今日」不是 UTC：2026-10-06 实测本地早上 07:11 入的 40 条被算到
            # 昨天，「今日入库」报 0，每天有 8 小时是错的。
            # localtime 把当前时刻转本地，start of day 落到本地零点，
            # 末尾 'utc' 再转回 UTC 才能和列里的 UTC 戳比（SQLite 3.38+）。
            "fetched_at >= datetime('now','localtime','start of day','utc')"
        ).fetchone()[0]
        last = con.execute("SELECT MAX(fetched_at) FROM announcements").fetchone()[0]
        # 静默停摆的独立判据：不看进程、不看 status.json，只问「库最后一次
        # 被写入是什么时候」。进程活着 + 日志正常 + 但数据不涨，是最难发现的
        # 那种停摆（2026-10-01 整夜就是这样：34 源全被退避闸挡住，一夜零入库，
        # 而 watchdog 每轮都报「正常完成（无源可采）」）。
        last_ingest = con.execute(
            "SELECT MAX(created_at) FROM announcements").fetchone()[0]
        # 退避闸里有多少源——「可采源为 0」是零入库最常见的原因，值得单独报。
        con.close()
        return {"total": total, "today": today, "last_fetch": last,
                "last_ingest": last_ingest}
    except Exception:                                   # noqa: BLE001
        return {"total": None, "today": None, "last_fetch": None,
                "last_ingest": None}


def _hours_since(stamp: Any) -> float | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600.0


def _rounds(n: int = 8) -> list[dict]:
    """最近 n 轮真实采集（只取跑过 >= 5 个源的那些）。"""
    out: list[dict] = []
    for p in sorted(RUNTIME.glob("shadow-*.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                               # noqa: BLE001
            continue
        srcs = d.get("sources") or []
        if len(srcs) < 5:
            continue
        secs = sum(float(s.get("seconds") or 0) for s in srcs)
        zero = sum(float(s.get("seconds") or 0) for s in srcs
                   if not s.get("records"))
        rec = int(d.get("records") or 0)
        out.append({
            "at": p.stem.replace("shadow-", ""),
            "sources": len(srcs),
            "records": rec,
            "dropped": int(d.get("dropped") or 0),
            "seconds": round(secs),
            "wall_guess": round(secs / 6.0),          # 6 并发下的墙钟估计
            "zero_pct": round(100 * zero / secs, 1) if secs else 0.0,
            "sec_per_record": round(secs / rec, 1) if rec else None,
            "buckets": d.get("buckets") or {},
            "next_due": d.get("next_due_epoch") or 0,
        })
        if len(out) >= n:
            break
    return out


def _backoff() -> dict:
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_r", str(ROOT / "lighthouse" / "run.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["_r"] = m
        spec.loader.exec_module(m)
        b = m.SourceBackoff.load()
        sleeping = [s for s in b.data if b.is_sleeping_for(s)]
        robots = [s for s in b.data if b.is_terminal_blocked(s)]
        return {
            "runs": b.runs, "tracked": len(b.data),
            "sleeping": len(sleeping), "terminal_blocked": len(robots),
            "robots_ids": sorted(robots),
            "top_dry": sorted(((v[0], k) for k, v in b.data.items()),
                              reverse=True)[:8],
        }
    except Exception as exc:                            # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def _events() -> dict:
    try:
        src = Counter()
        if EVENTS.exists():
            for line in EVENTS.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    src[json.loads(line).get("source", "?")] += 1
        return {"total": sum(src.values()), "by_source": dict(src)}
    except Exception as exc:                            # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def _status() -> dict:
    try:
        d = json.loads(STATUS.read_text(encoding="utf-8"))
        return {k: d.get(k) for k in
                ("phase", "round", "rounds_done", "rounds_planned", "db_now",
                 "depth", "updated_at", "last_error")}
    except Exception:                                   # noqa: BLE001
        return {}


def _alarms(d: dict) -> list[str]:
    """停摆/异常告警。**任何一条都必须先处理**，其余都是次要的。

    2026-09-30 20:55 的教训：watchdog 撞满 max_restarts=200 后静默退出，
    采集停摆 10 小时没人发现 —— 因为「没崩溃、没 traceback、日志正常」，
    只有一行不起眼的「重启次数用尽」。这类静默停摆必须能被一眼看出来，
    所以告警放在汇总的最顶端。
    """
    out: list[str] = []
    pr = d["process"]
    age = pr.get("status_age_min")

    # 1. watchdog 进程还在不在
    #
    # 不能用「python 进程数」判断：streamlit / http.server / 各种工具链都是
    # python，机器上常年有好几个。必须按命令行匹配 watchdog 本身。
    wd = pr.get("watchdog_alive")
    if pr.get("proc_error"):
        out.append("进程列表读取失败：%s" % pr["proc_error"])
    elif wd is False:
        out.append("watchdog 进程不在 —— 采集已停！"
                   "重启：python scripts/status_summary.py --heal")
    elif wd is None:
        out.append("判断不了 watchdog 是否存活（进程列表读不到）")

    # 2. 状态文件多久没更新（真正的停摆信号）
    if age is None:
        out.append("读不到 status.json 的更新时间，无法判断是否停摆")
    elif age > 180:
        out.append("状态文件已 %.0f 分钟（%.1f 小时）无更新 —— 采集极可能已停！"
                   "最后 phase=%s" % (age, age / 60.0,
                                      d["status"].get("phase")))
    elif age > 90:
        out.append("状态文件 %.0f 分钟无更新（正常上限 90 分钟，可能在轮间节流）"
                   % age)

    # 3. watchdog 最近有没有「放弃」—— 只看**最近一次启动之后**的日志。
    #
    # 扫全文会一直报历史事故：2026-09-30 20:55 那次「重启次数用尽」已写死在
    # 日志文件里，watchdog 早就被重新拉起了，但告警每次都复现，变成噪音 ——
    # 告警一旦变成背景噪音，真出事时反而看不见了。
    try:
        wl_path = RUNTIME / "watchdog.log"
        if wl_path.exists():
            lines = wl_path.read_text(encoding="utf-8").splitlines()
            # 最后一次「看门狗启动」之后的日志才是当前这次的表现
            start = 0
            for i in range(len(lines) - 1, -1, -1):
                if "看门狗启动" in lines[i]:
                    start = i
                    break
            recent = lines[start:]
            for line in recent:
                if "重启次数用尽" in line or "已达退避上限" in line:
                    out.append("watchdog 已放弃：%s —— 手动拉起："
                               "python scripts/status_summary.py --heal"
                               % line.strip()[-40:])
                    break
    except OSError:
        pass

    # 4. last_error
    err = d["status"].get("last_error")
    if err:
        out.append("调度器 last_error: %s" % str(err)[:120])

    # 5. ★ 静默停摆：进程活着、日志正常，但库很久没长
    #
    # 2026-10-01 整夜实测：34 个源全被退避闸挡住，watchdog 每轮都报
    # 「正常完成（无源可采）」，退出码 0，日志里没有一行报错——上面 1~4 条
    # 全部判为健康。而实际上**一夜零入库、12 小时只完成 1 轮**。
    #
    # 所以这里不看进程、不看 status.json，只问「库最后一次被写入是什么时候」。
    # 阈值 6 小时：正常一轮几十分钟到两小时，6 小时连一轮都没产出就说明
    # 有什么在挡住。
    db = d.get("db") or {}
    idle_h = _hours_since(db.get("last_ingest"))
    if idle_h is not None and idle_h >= 6:
        out.append(
            "★ 静默停摆：库已 %.0f 小时（%.1f 天）没有新记录 —— 采集在跑但没产出！"
            "（进程活着、日志正常，最难发现的一种停摆）"
            "先查退避闸：可采源是否为 0" % (idle_h, idle_h / 24))

    # 6. ★ 可采源为 0：零入库最常见的原因，值得单独报而不是混进「被闸挡住」
    bo = d.get("backoff") or {}
    tracked, sleeping = int(bo.get("tracked") or 0), int(bo.get("sleeping") or 0)
    if tracked and sleeping >= tracked and "error" not in bo:
        out.append(
            "★ 全部 %d 个源都在退避闸内，可采源为 0 —— 采集等于停摆。"
            "查 SourceBackoff.BACKOFF_LADDER（零产出源等多久才回访，"
            "按连续零产出轮数查这张绝对秒数的表）" % tracked)
    return out


def collect() -> dict:
    data = {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "process": _proc_alive(),
        "db": _db_counts(),
        "status": _status(),
        "backoff": _backoff(),
        "events": _events(),
        "rounds": _rounds(),
    }
    data["alarms"] = _alarms(data)
    return data


def render(d: dict) -> str:
    L: list[str] = []
    add = L.append
    add("=" * 66)
    add(" 采集汇总  %s" % d["checked_at"])
    add("=" * 66)

    # ── 停摆告警放最前面 ──────────────────────────────────────────────
    # 2026-09-30 20:55 的教训：watchdog 撞满 max_restarts=200 后静默退出，
    # 采集停摆 10 小时没人发现 —— 没有崩溃、没有 traceback，日志里只有一行
    # 不起眼的「重启次数用尽」。所以先看这个，再看别的。
    for alarm in d.get("alarms") or []:
        add("!! %s" % alarm)
    if d.get("alarms"):
        add("")

    db, st = d["db"], d["status"]
    add("")
    add("【数据】")
    add("  库内总量   %s" % db["total"])
    add("  今日入库   %s" % db["today"])
    add("  最后入库   %s" % db["last_fetch"])

    add("")
    add("【调度】")
    pr = d["process"]
    age = pr.get("status_age_min")
    if age is not None:
        state = ("正常" if age < 90 else
                 "偏慢（>90 分钟无更新，可能在轮间节流睡眠）" if age < 12 * 60
                 else "!! 疑似卡住（>12 小时无更新）")
        add("  状态文件更新于 %.0f 分钟前 — %s" % (age, state))
    add("  python 进程数: %s" % pr.get("python_processes"))
    add("  phase=%s  轮 %s/%s  翻页深度=%s" % (
        st.get("phase"), st.get("rounds_done"), st.get("rounds_planned"),
        st.get("depth")))
    if st.get("last_error"):
        add("  ! last_error: %s" % st["last_error"])

    b = d["backoff"]
    add("")
    add("【退避】")
    if "error" in b:
        add("  读取失败: %s" % b["error"])
    else:
        add("  轮次=%s  跟踪源=%s  被闸挡住=%s  robots终态=%s" % (
            b["runs"], b["tracked"], b["sleeping"], b["terminal_blocked"]))
        if b.get("robots_ids"):
            add("    robots终态源: %s" % ", ".join(b["robots_ids"]))
        if b["top_dry"]:
            add("    连续零产出最多: %s" % ", ".join(
                "%s(%d)" % (sid, n) for n, sid in b["top_dry"][:5]))

    add("")
    add("【最近各轮】")
    add("  %-7s %-6s %-6s %-7s %-8s %-9s %s" % (
        "时间", "源数", "入库", "丢弃", "源耗时", "每条秒", "零产出%"))
    for r in d["rounds"]:
        add("  %-7s %-6s %-6s %-7s %-8s %-9s %s" % (
            r["at"][-6:], r["sources"], r["records"], r["dropped"],
            "%ds" % r["seconds"],
            r["sec_per_record"] if r["sec_per_record"] is not None else "-",
            "%.0f%%" % r["zero_pct"]))

    good = [r for r in d["rounds"] if r["sec_per_record"]]
    if good:
        avg = sum(r["sec_per_record"] for r in good) / len(good)
        add("")
        add("  平均每条成本: %.0f 秒（有产出的轮次）" % avg)

    e = d["events"]
    add("")
    add("【自迭代事件流】")
    if "error" in e:
        add("  读取失败: %s" % e["error"])
    else:
        add("  共 %s 条  %s" % (e["total"], e["by_source"]))

    add("")
    add("=" * 66)
    return "\n".join(L)


if __name__ == "__main__":
    data = collect()
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render(data))

    # --heal：检测到停摆就把 watchdog 拉起来。告警只让人看见，自愈才让
    # 采集真的不停 —— 2026-09-30 停了 10 小时就是没人看见也没人管。
    if "--heal" in sys.argv:
        pr = data["process"]
        broken = (pr.get("watchdog_alive") is False
                  or (data["process"].get("status_age_min") or 0) > 180)
        if not broken:
            print("\n无需自愈：watchdog 在跑（%s）。"
                  % ("存活" if pr.get("watchdog_alive") else "未知"))
            raise SystemExit(0)
        print("\n[自愈] 采集已停，正在拉起 watchdog ...")
        import subprocess

        # 窗口标志别搞错（踩过）：
        #   0x00000010 = CREATE_NEW_CONSOLE  → 会弹出一个黑色控制台窗口
        #   0x00000008 = DETACHED_PROCESS     → 想要的
        #   0x08000000 = CREATE_NO_WINDOW     → 也想要
        # 两个一起给就是「完全无窗口地在后台跑」。
        flags = 0
        if os.name == "nt":
            flags = 0x00000008 | 0x08000000
        subprocess.Popen(
            [sys.executable, "lighthouse/watchdog.py",
             "--rounds", "40", "--minutes", "600"],
            cwd=str(ROOT), creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL)
        time.sleep(10)
        after = _proc_alive()
        print("[自愈] 已拉起：watchdog_alive=%s  python 进程数=%s"
              % (after.get("watchdog_alive"), after.get("python_processes")))
