"""通宵采集调度器：采集 → 自迭代 → 采集 → …，无人值守。

为什么用 Python 而不是 .bat / .ps1
-----------------------------------
踩过两次：``.bat`` 里的 UTF-8 中文注释会被 cmd.exe 按 GBK 读、当成命令执行，
脚本连日志都建不出来；无 BOM 的 ``.ps1`` 同样被 PowerShell 5.1 按 ANSI 读，
把 ``[int]$Rounds = 12`` 报成「值不是有效数值」。逻辑放在 Python 里就没这些问题，
而且能测。

自迭代做什么
------------
一轮跑完不直接下一轮，而是先看这一轮谁没采到、为什么，然后：

    no_links 的源 → repair.py 从真实链接反推 detail_pattern（只改有证据的）
    反推成功且能采到的 → promote.py 把它恢复成 enabled
    然后再跑下一轮

三道闸，防止自迭代把配置改坏：

    * repair 只在**推导出的形态与列表页不同形、>=5 条链接、至少两段路径**时才写
    * 显式配了 detail_pattern 的源**优先于**通用启发式
    * promote 只根据**实测采到数据**的影子报告，不猜
    * 每一步都先备份 sources.yaml，且只做行级手术（保住 145 个人工登记字段）

    python lighthouse/overnight.py --rounds 14 --minutes 45
    python lighthouse/overnight.py --daemon          # 前台，看着它跑
    python lighthouse/overnight.py --stop            # 停
    python lighthouse/status.py                      # 随时查（出差用这个）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import time

from lighthouse.atomic_write import atomic_write_json  # noqa: E402
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 本进程 spawn 的任何子进程都不带控制台窗口。
#
# 2026-10-03 实测：桌面上窗口一秒一闪。根因是这一层**没装 winflags**——
# run.py 和 playwright_worker 都装了，但补丁只对**当前进程及其后代**生效，
# overnight ���己没装，它 spawn 出来的孙进程就漏掉了。
# 于是 CREATE_NEW_CONSOLE 提供的那个「隐藏控制台」在渲染时又被
# playwright 的 node 驱动重新分配（实测每秒一次 conhost）。
from lighthouse.winflags import install as _install_no_window  # noqa: E402
from lighthouse.winflags import ensure_stdio as _ensure_stdio  # noqa: E402
_install_no_window()

RUNTIME = _ROOT / "data" / "runtime" / "lighthouse"
LOG = RUNTIME / "overnight.log"
STATUS = RUNTIME / "status.json"
STOP_FLAG = RUNTIME / "STOP"

# ★ 排在 LOG 之后、任何 log() 之前。理由见 watchdog.py 里的同一段注释：
# 计划任务用 pythonw.exe 拉起整条链且不给控制台，sys.stdout 是 None，
# 第一个 print() 就抛 AttributeError，进程在写第一行日志之前就退出。
# 每一层都要自己装一次：补丁只对当前进程及其后代有效。
_ensure_stdio(LOG.with_suffix(".out"))
# 采集锁目录（run.py 用的那把）。起 run.py 之前先等它空出来，
# 别拿不到锁就放弃整轮——锁被占只说明另一轮正在正常写库。
COLLECT_LOCK_DIR = _ROOT / "data" / "runtime" / "collect.lock"
OVERNIGHT_LOCK = RUNTIME / "overnight.lock"
PY = sys.executable

# run.py 跨过自身 deadline 之后的收尾窗口 = 单源时间上限 + 落盘余量。
# 取自同一份 config，run.py 就用这个键算单源预算（collect_source 的 time_budget），
# 宽限期跟着它走，两个数字不会各自漂移。
DRAIN_GRACE_SECONDS = 300 + 180

_stop = False


def read_round_skipped() -> int:
    """最新一轮有多少源因为到时限压根没跑（读 shadow 报告的 buckets.skipped）。"""
    reports = sorted(glob.glob(str(RUNTIME / "shadow-*.json")), key=os.path.getmtime)
    if not reports:
        return 0
    try:
        data = json.loads(Path(reports[-1]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return int(((data.get("buckets") or {}).get("skipped")) or 0)


def read_ingest_receipt(round_started: datetime) -> dict[str, Any] | None:
    """读本轮入库回执；回执比本轮开始还旧就当没有（说明这轮没走到入库）。"""
    path = RUNTIME / "last_ingest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    stamp = str(data.get("at") or "")
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.replace(tzinfo=None)
    if when < round_started.replace(tzinfo=None):
        return None
    return data


def _handle(signum: int, _frame: Any) -> None:
    global _stop
    _stop = True
    log(f"收到信号 {signum}，本轮结束后停止")


def log(text: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}"
    print(line, flush=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_status(state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    # 原子替换，status.py 读它时不会读到半个文件。带重试：Windows 上
    # 指挥中心每 20 秒读一次 status.json，硬替换会撞锁。
    atomic_write_json(STATUS, state)


def claim_singleton() -> bool:
    """抢调度器的独占权。抢到返回 True。

    ★ 这里用**目录锁**而不是「pid 文件 + 进程存活检查」，因为 pid 方案在
    2026-10-03 实测中反复失效：

    * 先后起两个 overnight，第一个把 pid 写进文件，第二个检查时读到的就是它，
      于是也启动 —— 两个实例同时跑。
    * 第二个实例退出时 `_release_pid` 判断「文件里是我」成立，**把正在跑的
      那个实例的记录删了**，单例随即彻底失效。

    pid 文件的问题是「检查」和「写入」之间有窗口，而且清理逻辑会和真正的
    所有者抢同一份文件。`collectors.lock.acquire` 用的是 `os.mkdir` ——
    原子操作、只可能成功一个，而且自带僵尸锁检测（进程没了会自动清掉）。
    run.py 已经在用它拿采集锁，同一套原语，不必另立一套。

    拿到之后锁目录一直留着（整个进程生命周期都持有），进程退出时锁目录会被
    判定为僵尸并在下次启动时自动清理，不需要手动删。
    """
    from collectors.lock import CollectLockBusy, acquire, release

    try:
        acquire("overnight", lock_dir=OVERNIGHT_LOCK)
    except CollectLockBusy:
        holder = OVERNIGHT_LOCK / "holder.json"
        who = holder.read_text(encoding="utf-8").strip() if holder.exists() else "?"
        log(f"已有调度器在跑（{who}），本次不重复启动")
        return False
    return True


def db_count() -> int:
    import sqlite3

    path = _ROOT / "data" / "beacon.db"
    if not path.exists():
        return 0
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        n = con.execute("select count(*) from announcements").fetchone()[0]
        con.close()
        return int(n)
    except Exception:                                          # noqa: BLE001
        return 0


# 子进程一律不弹窗。CREATE_NO_WINDOW = 0x08000000。
#
# 实测踩过：自迭代里 repair/promote/probe 三处 subprocess.run 没带这个标志，
# 它们继承了调度器的控制台，于是每次自迭代都在桌面上弹一个
# 「C:\Python310\python.exe」的窗口。用户看到的就是这个。
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _quiet_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """静默执行子进程：不弹窗、不继承控制台。"""
    kwargs.setdefault("cwd", str(_ROOT))
    kwargs.setdefault("creationflags", _NO_WINDOW)
    return subprocess.run(cmd, **kwargs)


def _wait_for_collect_lock(max_wait: float, poll: float = 15.0,
                           lock_dir: Path | None = None) -> bool:
    """等采集锁空出来，而不是拿不到就立刻放弃。

    ★ 2026-10-03 通宵实测：整夜 1823 轮里 **888 轮（49%）退出码 2**，
    日志反复是「主项目采集正在运行（collect.lock 被持有），本轮不启动」。
    那一轮 0.2 分钟就结束、净增 0，近一半的守夜时间花在「发现锁被占、
    放弃、退出、watchdog 重启、再发现锁被占」这个循环里。

    锁被占本身不是故障——那说明**另一轮采集正在正常写库**。锁存在的意义
    就是防止并发写库，把它当失败信号是把正确行为读成了错误。
    「等一会儿就能轮到自己」不是「本轮失败」。

    返回 True = 锁已空出，可以采；False = 等超时了，本轮跳过。

    ``lock_dir`` 可注入是为了可测：原来锁目录写死成模块常量，于是测试
    没法在临时目录里制造竞争——只能去动生产锁目录，测一次污染一次。
    竞争行为必须能在隔离目录里重演，否则这个函数的「等」根本没被验证过。
    """
    import time as _time
    from collectors.lock import CollectLockBusy, acquire, release

    waited = 0.0
    announced = False
    while waited < max_wait:
        try:
            target = lock_dir or COLLECT_LOCK_DIR
            acquire("precheck", lock_dir=target)
            release(target)
            return True
        except CollectLockBusy:
            if not announced:
                log("另一轮采集正在写库，等它结束（本轮不放弃）"
                    f"，最长等 {max_wait // 60:.0f} 分钟")
                announced = True
            _time.sleep(poll)
            waited += poll
    log(f"等了 {waited // 60:.0f} 分钟仍未拿到采集锁，本轮跳过")
    return False


def run_collect(args: argparse.Namespace) -> int:
    if not _wait_for_collect_lock(args.lock_wait_minutes * 60):
        # 等不到就不硬抢——并发写库会把库搞坏。返回 0 表示「本轮无事可做」
        # 而不是失败：watchdog 不该因为排队而重启整个调度器。
        return 0

    cmd = [PY, "-u", "lighthouse/run.py", "--all",
           "--limit", str(args.limit), "--max-minutes", str(args.minutes),
           "--max-stale", str(args.stale), "--workers", str(args.workers),
           "--archive-pages", str(args.depth), "--ingest"]
    if args.fresh:
        cmd.append("--fresh")
    before = db_count()
    # 采集进程同样要脱离进程组。理由同 watchdog._spawn_detached：
    # 每一级都脱离，父进程被清理时才不会把子进程一起带走。
    # 输出直接追加到同一份日志（run.py 是 -u 无缓冲的，进度行会实时落盘）。
    log_handle = open(LOG, "a", encoding="utf-8")
    popen_kwargs: dict = {"cwd": str(_ROOT), "stdout": log_handle,
                          "stderr": subprocess.STDOUT}
    if os.name == "nt":
        # 隐藏控制台，理由见 watchdog._spawn_detached 的注释。
        # run.py 是真正会渲染的进程（会拉起 playwright 的 node 驱动），
        # 所以它这一级必须给隐藏控制台，否则就是桌面上一秒一闪的窗口。
        _info = subprocess.STARTUPINFO()
        _info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        _info.wShowWindow = 0
        popen_kwargs["creationflags"] = 0x00000010 | 0x00000400
        popen_kwargs["startupinfo"] = _info
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kwargs)
    # 有限地等。实测一轮跑完（用了渲染）后 run.py 卡在 atexit 关浏览器，
    # 20 多分钟不退出；proc.wait() 无超时地等下去，这一轮在调度器眼里
    # 就永远没结束，而 status.json 一直显示「采集中」——通宵任务就这么静默地
    # 死掉了。
    #
    # 宽限期必须覆盖 run.py「跨过自身 deadline 之后的收尾窗口」，否则这一轮
    # 采到的东西全丢。run.py 的 deadline 是 --max-minutes（=args.minutes），
    # 而它在源与源之间检查 deadline：跨过之后还要等**在飞**的源各自跑完
    # （单源上限 rate_limit.source_budget_seconds=300s，6 并发），最后才调
    # _finish() 落盘并入库。原来的 +180s 宽限期小于单源上限，于是每一轮都在
    # 收尾途中被强杀，_finish() 从没执行过 —— 状态里 gained 全是 0，
    # 而日志里明明写着「发现 9 条 / 写入 6 条」。实测 round 5~9 五轮零入库、
    # 库停在 6391 条不动，根因就是这个常数。
    grace = args.minutes * 60 + DRAIN_GRACE_SECONDS
    try:
        return proc.wait(timeout=grace), before
    except subprocess.TimeoutExpired:
        log(f"  采集进程 {proc.pid} 超出宽限 {grace // 60} 分未退出，强杀后继续下一轮"
            f"（本轮已采数据可能未落盘，见 DRAIN_GRACE_SECONDS 的说明）")
        proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        return -9, before



def source_health() -> list[dict[str, Any]]:
    """逐源健康快照（访问次数 / 零产出次数 / 是否熔断）。

    直接复用 run.py 的 SourceBackoff，不另造一份统计 —— 两份统计一定会漂移。
    """
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_lhrun_health", _ROOT / "lighthouse" / "run.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.SourceBackoff.load().health()
    except Exception:                                          # noqa: BLE001
        return []


def read_next_due() -> int:
    """读最新 shadow 报告里的「下一个源最早可访问时刻」（epoch 秒，0 = 无）。

    这是轮间节流的依据。run.py 的回访间隔闸会按 cadence_tier 拦掉还没到点
    的源，但**轮次本身原来还是按固定节奏起** —— 于是源在 13/21 个之间来回荡，
    每轮 5~7 分钟、产出 0 条，一天白跑 40 轮（实测 2026-09-30）。
    """
    reports = sorted(glob.glob(str(RUNTIME / "shadow-*.json")), key=os.path.getmtime)
    if not reports:
        return 0
    try:
        data = json.loads(Path(reports[-1]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return int(data.get("next_due_epoch") or 0)


def wait_for_next_due(max_wait: int = 1800) -> int:
    """轮间节流：没有源到期就等，而不是立刻空转下一轮。

    这是自迭代里少数**真的能改变结果**的动作（其它只报证据）：源按
    cadence_tier 有回访间隔，轮次比间隔更密就必然空转。等着比空跑便宜，
    对站点也更礼貌（AGENTS.md §3.2 频率红线）。

    返回实际等待秒数；0 表示不用等。
    """
    deadline_at = read_next_due()
    if not deadline_at:
        return 0
    import time as _time

    wait = min(int(deadline_at - _time.time()), max_wait)
    if wait <= 30:
        return 0                      # 已到期或马上到期，直接开下一轮
    log(f"轮间节流：本轮无源到期，等 {wait // 60} 分钟再起下一轮"
        f"（{datetime.fromtimestamp(deadline_at):%H:%M} 有源可访问）")
    slept = 0
    while slept < wait and not _stop:
        _time.sleep(min(60, wait - slept))
        slept += 60
        if STOP_FLAG.exists():
            break
    return min(slept, wait)


def publish_editions(receipt: dict[str, Any] | None) -> dict[str, str]:
    """出刊：当日日报必出，周报逢周一、月报逢 1 号。

    只在**本轮真有新入库**时才出。采集一晚 14 轮，没入库的轮次重出同一份刊
    只会把同一个文件改 14 次的时间戳，没意义。

    出刊失败不抛：它是交付物，不是数据来源，不该因为写不出 markdown 就
    把整轮采集的记为失败。
    """
    from lighthouse import publish

    gained = int((receipt or {}).get("gained") or 0)
    if gained <= 0:
        return {"skipped": "本轮零入库"}
    today = datetime.now().astimezone().date()
    wanted = ["daily"]
    if today.weekday() == 0:            # 周一
        wanted.append("weekly")
    if today.day == 1:                  # 每月 1 号
        wanted.append("monthly")
    written: dict[str, str] = {}
    for edition in wanted:
        try:
            payload = publish.build_edition(edition, today)
            paths = publish.write_edition(payload)
            stats = payload["stats"]
            log(f"  {payload['edition_label']} {payload['period']['label']}："
                f"收录 {stats['total']} 条 / 事件 {stats['events']} / "
                f"交叉 {stats['corroborated']} / 分类 {stats['categories']}")
            written[edition] = paths["markdown"]
        except Exception as exc:                     # noqa: BLE001
            log(f"  出{edition}失败：{type(exc).__name__}: {exc}")
            written[edition] = f"error: {exc}"
    return written


def refresh_source_tiers() -> None:
    """重算采集源等级并落盘。

    为什么每轮都要算：等级全部建立在**实测信号**上（几天没产出、多少比例
    靠渲染、正文重复率），这些信号每天都在变。一次算完就冻结的话，
    刚被修好的源仍然按旧等级降频。

    失败只记不抛：等级是**优化**，不是采集的前置条件。
    """
    import importlib
    import sys
    scripts = str(_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        mod = importlib.import_module("source_tiers")
        mod.save(mod.classify_all())
    except Exception as exc:                                # noqa: BLE001
        log(f"等级刷新失败（不影响采集）：{type(exc).__name__}: {exc}")


def tune_round(round_no: int, receipt: dict[str, Any] | None) -> dict[str, Any]:
    """全链路诊断：采集 / 抽取 / 摘要 / 分析 四段。

    只诊断和报告，**不改配置**。改配置的动作要么证据不足（如门禁阈值经实测
    本来就是对的），要么需要人确认（换选择器会改变采到的东西）。把它和
    self_iterate 分开：后者是「已知某类坏了就修」，这里是「先看清这一轮
    四段各自怎么样」。
    """
    from lighthouse import self_tune

    reports = sorted(glob.glob(str(RUNTIME / "shadow-*.json")), key=os.path.getmtime)
    if not reports:
        return {"findings": [], "detail": "还没有 shadow 报告"}
    shadow = json.loads(Path(reports[-1]).read_text(encoding="utf-8"))

    collected = int(shadow.get("records") or 0)
    ingested = int((receipt or {}).get("inserted") or 0)
    # 摘要空缺 = 采集到了但没写出摘要。collected 是采集数，ingested 是入库数，
    # 两者不等（重采/门禁），所以这里只用采集数做分母。
    summary_gap = max(0, collected - ingested) if collected else 0

    report = self_tune.analyse(shadow, ingested=ingested,
                               collected=collected, summary_gap=summary_gap,
                               source_health=source_health())
    self_tune.save(report, RUNTIME / "tuning", round_no)
    log("全链路诊断：")
    for line in self_tune.summarise(report).splitlines():
        log(line)
    # 注意：**不要把 Finding 对象放进返回值**。
    #
    # 这个 dict 会被 `state["self_iteration"].append({**tune_result})`
    # 并进 status.json，而 status.json 是 json.dumps 出来的 ——
    # Finding 是 dataclass，序列化直接抛
    # 「TypeError: Object of type Finding is not JSON serializable」。
    #
    # 2026-10-03 实测：自迭代诊断一开始有 finding，每轮 round 结束写状态就崩，
    # 退出码 2；watchdog 把它当异常重启，于是**每 20 秒崩溃一次、无限重启**，
    # 而真正的采集（run.py）一次都没被调度起来。日志里只有
    # 「全链路诊断」后面一行 traceback，别的什么都没有。
    #
    # 对象只通过下面的 _finding_objects 单独传给 self_evolve（同一个进程内用），
    # 不经过任何持久化路径。要跨进程用就走 self_tune.save() 写的
    # tuning/tune-*.json，那份是纯数据。
    return {
        "findings": [f.kind for f in report.findings],
        "stages": sorted({f.stage for f in report.findings}),
        "auto_actions": [f.kind for f in report.findings if f.auto],
        "buckets": shadow.get("buckets") or {},
        "report": f"data/runtime/lighthouse/tuning/tune-{round_no:03d}.json",
        "_finding_objects": report.findings,
    }


def self_iterate(round_no: int) -> dict[str, Any]:
    """一轮之后：把没采到的源试着修好，把采到数据的源恢复启用。

    只做有证据的动作。repair 推不出来的源保持原样，promote 只认实测。
    """
    summary: dict[str, Any] = {"repaired": [], "promoted": [], "skipped": []}

    reports = sorted(glob.glob(str(RUNTIME / "shadow-*.json")), key=os.path.getmtime)
    if not reports:
        return summary
    latest = reports[-1]
    data = json.loads(Path(latest).read_text(encoding="utf-8"))
    dead = [s["source_id"] for s in data.get("sources", [])
            if not s.get("records") and s.get("notes")
            and any("no links" in str(n) or "list_selector" in str(n)
                    for n in s["notes"])]
    if not dead:
        log("自迭代：没有 no_links 的源，跳过")
        return summary

    log(f"自迭代：{len(dead)} 个源取不到链接，尝试反推 detail_pattern")
    ids = ",".join(dead[:args_repair_batch[0]])
    repair = _quiet_run([PY, "lighthouse/repair.py", "--source", ids, "--apply"],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    for line in (repair.stdout or "").splitlines():
        if "已改" in line:
            summary["repaired"].append(line.strip())
            log("  " + line.strip())
    if repair.returncode != 0 and repair.stderr:
        log("  repair 失败：" + (repair.stderr.strip().splitlines() or [""])[-1][:160])

    # 一个 pattern 都没改出来就别复采了。
    # 实测：repair 对 9 个源推出 0 个（正确地拒绝了猜测），但代码还是去复采了一遍，
    # 白花 4 分钟，结论还是「仍然失败」。16 轮就是 1 小时。
    # 复采只在「配置真的改了」时才有意义——那是用来验证改动有没有用的。
    if not summary["repaired"]:
        log("  没推出任何新 pattern，跳过复采验证（改了才需要验）")
        return summary

    # 改完选择器立刻复采一次，用实测结果决定要不要恢复 enabled
    probe = _quiet_run(
        [PY, "lighthouse/run.py", "--source", ids, "--limit", "3",
         "--dry-run", "--fresh", "--max-minutes", "8", "--max-stale", "40",
         "--tag", "probe"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    # 找探针报告要认 tag：自迭代的探针只覆盖几个源，别把整轮的当最新一轮
    probes = sorted(glob.glob(str(RUNTIME / "shadow-probe-*.json")), key=os.path.getmtime)
    if not probes:
        return summary
    probed = json.loads(Path(probes[-1]).read_text(encoding="utf-8"))
    winners = [s["source_id"] for s in probed.get("sources", []) if s.get("records")]
    losers = [s["source_id"] for s in probed.get("sources", [])
              if not s.get("records") and s.get("records") == 0]
    if winners:
        promote = _quiet_run([PY, "lighthouse/promote.py",
                              "--ids", ",".join(winners), "--apply"],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        for line in (promote.stdout or "").splitlines():
            if "status=" in line or "已写入" in line:
                summary["promoted"].append(line.strip())
                log("  " + line.strip())
    summary["skipped"] = losers
    log(f"自迭代：修好 {len(summary['repaired'])} 个，"
        f"恢复启用 {len(summary['promoted'])} 个，"
        f"仍然失败 {len(losers)} 个")
    return summary


args_repair_batch = [6]         # 每轮最多试修 6 个源，防止一轮吃掉整轮时间


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通宵采集调度器（含自迭代）")
    parser.add_argument("--rounds", type=int, default=14)
    parser.add_argument("--minutes", type=int, default=45, help="每轮硬时限")
    parser.add_argument("--limit", type=int, default=600, help="每源上限")
    parser.add_argument("--stale", type=int, default=200, help="连续多少个已知 URL 才早停")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--repair-batch", type=int, default=6,
                        help="每轮最多试修几个 no_links 的源")
    parser.add_argument("--deadline", default="none",
                        help="到点停止，HH:MM。默认 none = 一直跑到被显式停掉")
    parser.add_argument("--depth", type=int, default=30,
                        help="起始翻页深度。产出衰减时自动加大——源被采浅了"
                             "就往更深的年份挖，而不是停下来。"
                             "默认 4（2026-09-30 从 12 下调）：12 页配 "
                             "playwright 渲染时 6 个源烧满 300 秒预算全部零产出，"
                             "实测一轮 46%% 的时间花在零产出源上。这个值会覆盖"
                             "config 里的 collect_defaults.archive_pages")
    parser.add_argument("--depth-max", type=int, default=60,
                        help="翻页深度上限。再深下去多数站没有更多历史页")
    # ★ 默认 0（不限），不是 3。
    #
    # 「连续 3 轮零入库就停」对「排空模式」是对的——源采干后深页全是已知 URL，
    # 继续跑只是空转。但**通宵场景不适用**：政府源按天更新，白天到夜里连续
    # 3 轮没新内容是正常状态。2026-10-03 实测：日志「全部结束：3 轮，净增 0 条
    # （10621 → 10621）」，一次通宵被这个默认值提前掐断。
    # 轮间节流保证没源可采时它会睡到下一个到期时间再试，不限轮数不会空转。
    parser.add_argument("--lock-wait-minutes", type=int, default=20,
                        help="起 run.py 前等采集锁的最大分钟数。0=不等。"
                             "锁被占说明另一轮正在正常写库（锁在防并发写库），"
                             "等它结束即可，不该当成失败放弃整轮")
    parser.add_argument("--max-zero-rounds", type=int, default=0,
                        help="连续多少轮零入库就停。源采干后深页全是更老的已知 URL，"
                             "继续跑只是空转。0 = 不限（跑满全部轮数）")
    parser.add_argument("--no-self-iterate", action="store_true")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--daemon", action="store_true",
                        help="留在前台（默认也是前台，daemon 只是显式声明）")
    parser.add_argument("--stop", action="store_true", help="写停止标记，让正在跑的实例退出")
    args = parser.parse_args(argv)
    args_repair_batch[0] = args.repair_batch

    RUNTIME.mkdir(parents=True, exist_ok=True)
    if args.stop:
        STOP_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
        log("已写停止标记，正在跑的实例会在本轮结束后退出")
        return 0
    if STOP_FLAG.exists():
        STOP_FLAG.unlink()
        log("清除旧的停止标记")

    # ── 单例：只允许一个调度器 ──────────────────────────────────
    #
    # 放在 --stop 之后是必须的：--stop 的语义是「让正在跑的那个退出」，
    # 这里先拦下来就永远碰不到真正的实例。
    #
    # 2026-10-03 实测：watchdog 有单例、overnight 没有。计划任务登录自启一个、
    # watchdog 拉一个、手工再跑一个 —— 同一时刻 6 个 overnight 在跑，抢同一个
    # collect.lock、同时写 status.json，每个都把自己那轮记成「112 个源到时限
    # 没被采到」；watchdog 按「异常退出」不停重启，2000 次额度半小时用尽就弃守。
    # 症状和「采集本身不行」几乎一样：日志刷满、库不增长。
    if not claim_singleton():
        return 0

    signal.signal(signal.SIGINT, _handle)
    try:
        signal.signal(signal.SIGTERM, _handle)
    except (AttributeError, ValueError):
        pass

    # 截止时刻算成绝对时间。直接比小时是错的：19:39 配 07:00 时
    # 「19 >= 7」为真，任务会立刻「停止，0 轮」。
    # 默认 none = 不设截止，一直跑到被显式停掉。理由：政府网站的历史公告
    # 每天都在增加，停在某个钟点就等于白扔已经建好的采集能力；衰减的问题
    # 该用「往深里挖」解决，不是用「到点躺下」解决。
    now = datetime.now()
    no_deadline = args.deadline.strip().lower() in {"none", "", "-", "0"}
    if no_deadline:
        deadline = now + timedelta(days=365)
    else:
        hour, _, minute = args.deadline.partition(":")
        deadline = now.replace(hour=int(hour), minute=int(minute or 0),
                               second=0, microsecond=0)
        if deadline <= now:
            deadline += timedelta(days=1)

    state: dict[str, Any] = {
        "pid": os.getpid(), "started_at": now.isoformat(timespec="seconds"),
        "deadline": deadline.isoformat(timespec="seconds"),
        "rounds_planned": args.rounds, "rounds_done": 0,
        "db_start": db_count(), "db_now": db_count(),
        "round_log": [], "last_error": "", "self_iteration": [],
        "depth": args.depth, "depth_changes": [],
    }
    write_status(state)

    log("=" * 74)
    log(f"通宵调度启动 pid={os.getpid()} 轮数={args.rounds} 每轮={args.minutes}分 "
        f"stale={args.stale} limit={args.limit} 并发={args.workers}")
    log(f"截止 {deadline.strftime('%m-%d %H:%M')}"
        f"{'（无截止，跑到被停掉）' if no_deadline else f'（{_hours(deadline)} 小时后）'}"
        f"  起始翻页深度 {args.depth}（上限 {args.depth_max}）"
        f"  起始库 {state['db_start']} 条")
    log(f"自迭代：{'关' if args.no_self_iterate else '开，每轮最多修 ' + str(args.repair_batch) + ' 个源'}")

    for index in range(1, args.rounds + 1):
        if _stop or STOP_FLAG.exists():
            log("收到停止请求")
            break
        if datetime.now() >= deadline:
            log(f"到达截止时间 {deadline.strftime('%H:%M')}，停止")
            break

        log(f"--- 第 {index}/{args.rounds} 轮 开始 ---")
        # 每轮先重算等级：等级建立在「几天没产出 /多少比例靠渲染/正文重复率」
        # 这些每天都在变的信号上，冻结就等于用过期信息分配预算。
        refresh_source_tiers()
        started = datetime.now()
        write_status({**state, "phase": f"collect round {index}"})
        try:
            code, before = run_collect(args)
        except Exception as exc:                              # noqa: BLE001
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            log(f"  采集进程起不来：{exc}")
            code = -1
            before = db_count()
        after = db_count()
        minutes = round((datetime.now() - started).total_seconds() / 60, 1)

        # 记账用入库自己的数字，不用「全库 after - 全库 before」。
        # 有第二个进程写同一个库时（scripts/dedup.py、prune_section_pages.py、
        # 审计脚本都会），那个差值就不属于这一轮：实测第 2 轮记成 -44，
        # 而那一轮实际入库 +9 —— 中间被别人删了 53 行。跑半个月必然撞上。
        receipt = read_ingest_receipt(started)
        if receipt is not None:
            gained = int(receipt.get("gained") or 0)
            note = f"入库写入 {receipt.get('inserted')} 条"
            if gained < 0:
                note += f"（库同时被其他进程减少 {abs(gained)} 条）"
        else:
            gained = after - before
            note = "无入库回执，按全库差值计"

        # 本轮有多少源因为到时限压根没被跑。这是判断"要不要加深翻页"的唯一
        # 可靠依据：没跑完时加深是负反馈（见下面 depth_changes 那段注释）。
        skipped = read_round_skipped()
        log(f"--- 第 {index}/{args.rounds} 轮 结束  用时 {minutes} 分  "
            f"退出码 {code}  净增 {gained:+d}（库 {after} 条，{note}）---")

        state["rounds_done"] = index
        state["db_now"] = after
        state["round_log"].append({"round": index, "minutes": minutes,
                                   "exit": code, "db_before": before,
                                   "db_after": after, "gained": gained,
                                   "ingested": (receipt or {}).get("inserted"),
                                   "skipped": skipped,
                                   "depth": args.depth,
                                   "at": datetime.now().isoformat(timespec="seconds")})
        if skipped:
            log(f"  本轮有 {skipped} 个源到时限没被采到（下轮继续），"
                f"不改翻页深度")
        write_status({**state, "phase": "self-iterate"})

        # 连续零入库早停：源采干后深页全是更老的已知 URL，继续跑只是空转。
        # 加深翻页在前几轮有意义（源可能被采浅了），但连续 N 轮零入库说明
        # 真的没新内容了，停掉等下次调度比白跑 14 轮强。
        if args.max_zero_rounds > 0:
            zero_streak = sum(1 for r in reversed(state["round_log"]) if r["gained"] == 0)
            if zero_streak >= args.max_zero_rounds:
                log(f"  连续 {zero_streak} 轮零入库，源已采干，提前停止"
                    f"（--max-zero-rounds={args.max_zero_rounds}）")
                state["last_error"] = ""
                break

        if not args.no_self_iterate and not _stop:
            # 旧路径：只修 no_links（选择器失效）。覆盖面窄但要保留 ——
            # self_tune 只做诊断和报告，不改配置。
            try:
                state["self_iteration"].append(
                    {"round": index, **self_iterate(index)})
            except Exception as exc:                          # noqa: BLE001
                state["last_error"] = f"self_iterate: {type(exc).__name__}: {exc}"
                log(f"  自迭代异常：{exc}")
            # 全链路诊断：采集 / 抽取 / 摘要 / 分析 四段都看一遍。
            # self_improve.py 只认 no_links，实测它经常一整轮什么都不做
            # （no_new 12 / all_dropped 5 / unknown 9 / no_links 0 时它直接返回）。
            tune_result: dict[str, Any] = {}
            try:
                tune_result = tune_round(index, receipt)
                # 下划线开头的键是给同进程内代码用的，不入状态文件。
                #
                # 直接 **tune_result 会把 Finding 对象也塞进 status.json，
                # 而 status.json 是 json.dumps 出来的 —— 每轮写状态就抛
                # TypeError（详见 tune_round 的注释）。
                state["self_iteration"].append(
                    {"round": index,
                     **{k: v for k, v in tune_result.items()
                        if not k.startswith("_")}})
            except Exception as exc:                          # noqa: BLE001
                state["last_error"] = f"self_tune: {type(exc).__name__}: {exc}"
                log(f"  全链路诊断异常：{exc}")
            # 自进化协调层：把两个系统的事件汇入统一事件流 + 蒸馏原则表。
            # 不改配置，只做事件转写和原则蒸馏。self_improve 的 iterate
            # 也在这里跑（原来散在两个地方，现在统一入口）。
            try:
                from lighthouse import self_evolve
                findings = tune_result.get("_finding_objects") or []
                ev = self_evolve.step(index, findings=findings)
                state["self_iteration"].append({"round": index, **ev})
                if ev.get("self_improve_step"):
                    s = ev["self_improve_step"]
                    log(f"  自进化：摘要规则 {s['defect']}/{s['metric']} "
                        f"{s['before']}→{s['after']} "
                        f"{'保留' if s['kept'] else '撤销' if s['applied'] else '跳过'}")
                if ev.get("principles_distilled"):
                    log(f"  自进化：蒸馏 {ev['principles_distilled']} 条原则，"
                        f"事件流 {ev.get('events_total', 0)} 条")
            except Exception as exc:                          # noqa: BLE001
                state["last_error"] = f"self_evolve: {type(exc).__name__}: {exc}"
                log(f"  自进化异常：{exc}")
        # 成刊。放在自迭代之后：本轮新增的记录要先进库，才谈得上编进哪一期。
        if not _stop:
            try:
                state.setdefault("publications", []).append(
                    {"round": index, **publish_editions(receipt)})
            except Exception as exc:                      # noqa: BLE001
                log(f"  出刊异常：{exc}")
        write_status({**state, "phase": "idle"})

        # 不做「产出衰减就加深翻页」的自适应了。实测证明它是负反馈：
        #   轮1 depth=12 -> +80 条
        #   轮2 depth=12 -> +5  条      ← 同深度、同 skipped=0，产量差 16 倍
        #   轮3 depth=18 -> +4  条
        # 产量下滑跟深度无关：轮 1 把新鲜内容挖完了，后面几轮在反复确认已知 URL
        # （early stop after 40 known urls）。而政府站发布慢，**列表顶部才是最新的**，
        # 往深翻只能拿到更旧的公告 —— 对「盯最新政策」这个用途是负价值。
        #
        # 源采干后的正确反应是退避（run.py 的 SourceBackoff，期满回访），
        # 不是把深度刨得更深。深度保持 --depth 给的固定值。
        # 深度现在是固定值（人工 --depth 决定，不再自动加深，见下面注释）
        state["depth"] = args.depth
        write_status({**state, "phase": "idle", "depth": args.depth})

        # 轮间节流。没有源到期就等下一轮，别空转。这是自迭代少数能真正改变
        # 结果的动作：源按 cadence_tier 有回访间隔，轮次比间隔更密就必然
        # 每轮只碰到十来个源、产出 0 条。实测不节流时一天白跑 40 轮。
        if not _stop:
            wait_for_next_due()

    state["finished_at"] = datetime.now().isoformat(timespec="seconds")
    state["db_end"] = db_count()
    state["phase"] = "done"
    state["total_gain"] = state["db_end"] - state["db_start"]
    write_status(state)
    log("=" * 74)
    log(f"全部结束：{state['rounds_done']} 轮，净增 {state['total_gain']} 条 "
        f"（{state['db_start']} → {state['db_end']}）")
    log(f"统计：python lighthouse/report.py    状态：python lighthouse/status.py")
    return 0


def _hours(when: datetime) -> str:
    delta = when - datetime.now()
    return f"{delta.total_seconds() / 3600:.1f}"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        # 拿了锁就必须还。
        #
        # 不还的后果实测过一次：overnight 跑完 3 轮零入库正常退出，锁目录留在
        # 盘上、里面是个已死进程的 pid。而 collectors.lock._is_stale 判定僵尸
        # 的条件是「pid 已死**且** started_at 超过 3 小时」——刚退出不到 3 小时，
        # 于是永远判不出僵尸，watchdog 每次拉起新实例都被
        # 「已有调度器在跑」挡掉，整条链卡死不干活。
        from collectors.lock import release as _release_lock
        _release_lock(lock_dir=OVERNIGHT_LOCK)
