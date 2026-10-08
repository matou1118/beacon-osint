"""灯塔采集控制台 — Streamlit view.

一个开关管通宵采集，不用记命令行。放在「采集与来源」组里紧挨「采集运维」，
因为它回答的是同一个问题的另一半：那边看「已入库的数据质量如何」，这边看
「现在还在不在采、采了多少、下一轮要修什么」。

页面只读 data/runtime/lighthouse/ 下那几个文件（status.json / overnight.log /
shadow-*.json），不自己算一遍——status.py 和 report.py 已经有那套口径了，
这里只负责显示，免得两处算法漂移。

复用工作台的 CSS class（panel / metric-row / section-label / aux-stat），
所以本页不写任何样式。
"""
from __future__ import annotations

import glob
import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "data" / "runtime" / "lighthouse"
STATUS = RUNTIME / "status.json"
STOP_FLAG = RUNTIME / "STOP"
WATCHDOG_STOP = RUNTIME / "WATCHDOG_STOP"
LOG = RUNTIME / "overnight.log"
PY = sys.executable


# ---------------------------------------------------------------- 读状态

def _alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        # tasklist 按控制台代码页（GBK）输出，PYTHONUTF8=1 时 os.popen 的 TextIOWrapper
        # 会按 UTF-8 严格解码并抛 UnicodeDecodeError，整个"灯塔采集"页直接渲染失败。
        # .read() 不吃 errors 参数，所以从 buffer 读字节再自己宽松解码。
        with os.popen(f'tasklist /FI "PID eq {pid}" 2>NUL') as proc:
            return str(pid) in proc.buffer.read().decode("utf-8", "replace")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def load_status() -> dict[str, Any] | None:
    if not STATUS.exists():
        return None
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except ValueError:
        return None


def db_count() -> int:
    path = ROOT / "data" / "beacon.db"
    if not path.exists():
        return 0
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        n = con.execute("select count(*) from announcements").fetchone()[0]
        con.close()
        return int(n)
    except Exception:                                          # noqa: BLE001
        return 0


def latest_shadows(limit: int = 40) -> list[dict[str, Any]]:
    files = sorted(glob.glob(str(RUNTIME / "shadow-*.json")), key=os.path.getmtime)
    out = []
    for path in reversed(files[:limit]):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data["_name"] = Path(path).name
        out.append(data)
    return out


def tail_log(lines: int = 60) -> list[str]:
    if not LOG.exists():
        return []
    return LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]


# ---------------------------------------------------------------- 动作

def start(rounds: int, minutes: int, stale: int, limit: int, workers: int,
          repair_batch: int, deadline: str) -> None:
    """拉起看门狗（它会拉起调度器，调度器崩了它再拉回来）。"""
    RUNTIME.mkdir(parents=True, exist_ok=True)
    for flag in (STOP_FLAG, WATCHDOG_STOP):
        flag.unlink(missing_ok=True)
    cmd = [PY, "lighthouse/watchdog.py", "--rounds", str(rounds),
           "--minutes", str(minutes), "--deadline", deadline,
           "--stale", str(stale), "--limit", str(limit),
           "--workers", str(workers), "--repair-batch", str(repair_batch)]
    # 隐藏控制台，**不是** CREATE_NO_WINDOW。理由见 lighthouse/launch.py 和
    # lighthouse/watchdog.py 的实测：CREATE_NO_WINDOW 让进程没有控制台，于是
    # playwright 的 node 驱动（以及 watchdog 轮询用的 tasklist 等控制台子进程）
    # 会自己分配一个新的 conhost.exe——桌面上一秒一闪、约每分钟一次。
    # 给一个**隐藏的控制台**（CREATE_NEW_CONSOLE + SW_HIDE），子孙继承它，
    # 谁都不再自己分配。实测：CREATE_NO_WINDOW -> 3 次 conhost；
    # 隐藏控制台 + winflags 全局补丁 -> 0 次。下游 watchdog→overnight→run
    # 每一级都已自带隐藏控制台 + winflags，唯独 UI 这第一级之前用错了标志。
    popen_kwargs: dict = {"cwd": str(ROOT),
                          "stdout": subprocess.DEVNULL,
                          "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0                       # SW_HIDE
        popen_kwargs["creationflags"] = 0x00000010 | 0x00000400   # NEW_CONSOLE | UNICODE_ENV
        popen_kwargs["startupinfo"] = info
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **popen_kwargs)


def stop() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    STOP_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
    WATCHDOG_STOP.write_text(datetime.now().isoformat(), encoding="utf-8")


def kill_all() -> None:
    """强制停。优雅停不生效时用（调度器卡在某一步不退时）。"""
    for pattern in ("watchdog.py", "overnight.py", r"lighthouse\run.py"):
        os.system(f'taskkill /FI "WINDOWTITLE eq {pattern}*" /F >NUL 2>&1')
    os.system("taskkill /F /FI \"IMAGENAME eq python.exe\" >NUL 2>&1")
    for flag in (STOP_FLAG, WATCHDOG_STOP):
        flag.unlink(missing_ok=True)


# ---------------------------------------------------------------- 组件

def _metric(value: Any, note: str, tone: str = "") -> str:
    cls = f"metric {tone}".strip()
    return (f"<div class='{cls}'><div class='metric-value'>"
            f"{html.escape(str(value))}</div>"
            f"<div class='metric-note'>{html.escape(note)}</div></div>")


def _banner(state: dict[str, Any] | None) -> tuple[bool, int, str]:
    """返回 (运行中, pid, 阶段文字)。"""
    if state is None:
        return False, 0, "还没跑过"
    pid = int(state.get("pid") or 0)
    if _alive(pid):
        phase = state.get("phase", "?")
        return True, pid, phase
    if state.get("finished_at"):
        reason = state.get("stopped_reason") or "已结束"
        return False, pid, f"已结束（{reason}）"
    return False, pid, "进程不在了，可能被强杀"


def render(db_path: str = "data/beacon.db") -> None:
    st.markdown("<div class='section-label'>灯塔采集控制台</div>", unsafe_allow_html=True)

    # ---------------------------------------------------------- 开关
    head_left, head_right = st.columns([3, 2])
    with head_left:
        st.markdown(
            "<div style='color:var(--text-muted);font-size:13px;line-height:1.7'>"
            "通宵无人值守采集：分级路由 → 质量门禁 → 摘要 → 入库，每轮结束后自动"
            "反推失配的源并复采验证。<b>抢不到 collect.lock 就自动让路</b>，"
            "不会和主项目采集并行写库。"
            "</div>",
            unsafe_allow_html=True)
    with head_right:
        st.caption("配置存在 lighthouse/config/web_collector_config.yaml")

    state = load_status()
    running, pid, phase = _banner(state)
    now = db_count()

    left, right = st.columns([2, 3])
    with left:
        if running:
            st.markdown(
                f"<div class='metric-row'>"
                f"{_metric('运行中', '调度器阶段', '')}"
                f"{_metric(phase, '当前阶段', '')}"
                f"{_metric(pid, '进程号', '')}"
                f"</div>", unsafe_allow_html=True)
        else:
            st.markdown(
                f"<div class='metric-row'>"
                f"{_metric('已停止', phase, '')}"
                f"{_metric(now, '库内公告总数', '')}"
                f"</div>", unsafe_allow_html=True)

    with right:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("轮次", f"{state.get('rounds_done', 0)}/{state.get('rounds_planned', 0)}"
                 if state else "—")
        c2.metric("入库总数", f"{now:,}")
        gain = int(state.get("total_gain") or (now - int(state.get("db_start") or now))) if state else 0
        c3.metric("本次净增", f"{gain:+,}")
        depth = (state or {}).get("depth")
        c4.metric("翻页深度", f"{depth} 页" if depth else "—")

    st.divider()

    # ---------------------------------------------------------- 控制
    st.markdown("<div class='section-label'>控制</div>", unsafe_allow_html=True)
    changes = (state or {}).get("depth_changes") or []
    if changes:
        st.caption("产出衰减时已自动加深翻页："
                   + "；".join(f"第{c['round']}轮 {c['from']}→{c['to']}页"
                              for c in changes[-4:]))
    if running:
        b1, b2, b3 = st.columns([1, 1, 2])
        if b1.button("优雅停止", type="primary", width="stretch",
                     help="当前轮跑完才退出，不丢已采到的数据"):
            stop()
            st.rerun()
        if b2.button("强制停止", width="stretch",
                     help="立刻杀掉所有相关进程。调度器卡住不退时用"):
            kill_all()
            st.rerun()
        with b3:
            st.caption(f"看门狗 pid 会自动重启调度器，除非已写停止标记。"
                       f"当前调度器 pid={pid}")
    else:
        with st.form("lighthouse_start"):
            f1, f2, f3 = st.columns(3)
            rounds = f1.number_input("轮数", 1, 60, 14, 1)
            minutes = f2.number_input("每轮分钟", 5, 180, 30, 5)
            deadline = f3.text_input("截止时刻", "07:30", help="24 小时制 HH:MM，到点自动停")
            g1, g2, g3, g4 = st.columns(4)
            stale = g1.number_input("早停阈值", 2, 500, 40, 10,
                                    help="连续多少个已入库 URL 才停。调小会整夜空转")
            limit = g2.number_input("每源上限", 1, 200, 25, 5)
            workers = g3.number_input("并发", 1, 12, 6, 1)
            repair_batch = g4.number_input("每轮自修复上限", 0, 20, 6, 1)
            submitted = st.form_submit_button("启动通宵采集", type="primary")
        if submitted:
            start(int(rounds), int(minutes), int(stale), int(limit), int(workers),
                  int(repair_batch), deadline.strip() or "07:30")
            time.sleep(1.5)
            st.rerun()

    st.divider()

    # ---------------------------------------------------------- 逐轮
    rounds_log = (state or {}).get("round_log") or []
    if rounds_log:
        st.markdown("<div class='section-label'>逐轮</div>", unsafe_allow_html=True)
        st.dataframe(
            [{"轮": r["round"], "用时(分)": r["minutes"], "退出码": r["exit"],
              "入库前": r["db_before"], "入库后": r["db_after"], "净增": r["gained"],
              "时间": r["at"][5:16].replace("T", " ")} for r in rounds_log],
            width="stretch", hide_index=True, height=min(420, 40 + 36 * len(rounds_log)))

    # ---------------------------------------------------------- 自迭代
    si = (state or {}).get("self_iteration") or []
    repaired = [(x, line) for x in si for line in (x.get("repaired") or [])]
    promoted = [(x, line) for x in si for line in (x.get("promoted") or [])]
    if repaired or promoted:
        st.markdown("<div class='section-label'>自迭代</div>", unsafe_allow_html=True)
        st.caption(f"自动修了 {len(repaired)} 次选择器、恢复了 {len(promoted)} 个源。"
                   f"只做有证据的动作：推不出来的源保持原样。")
        for _x, line in (repaired + promoted)[-15:]:
            st.text(line)

    # ---------------------------------------------------------- 采集画像
    shadows = latest_shadows(60)
    if shadows:
        st.markdown("<div class='section-label'>最近采集画像</div>", unsafe_allow_html=True)
        routes: dict[str, int] = {}
        reasons: dict[str, int] = {}
        rec = drop = 0
        for s in shadows:
            routes.update(s.get("by_route") or {})
            rec += s.get("records", 0)
            drop += s.get("dropped", 0)
            for k, v in (s.get("by_reason") or {}).items():
                reasons[k[:48]] = reasons.get(k[:48], 0) + v
        b1, b2 = st.columns([1, 1])
        with b1:
            total = sum(routes.values()) or 1
            st.markdown(
                "<div class='metric-row'>"
                + "".join(
                    f"<div class='metric'><div class='metric-value'>"
                    f"{100*v/total:.0f}%</div><div class='metric-note'>{html.escape(k)}</div></div>"
                    for k, v in routes.items())
                + _metric(f"{rec:,}", "累计采到", "")
                + _metric(f"{100*rec/max(1,rec+drop):.0f}%", "门禁通过率", "")
                + "</div>", unsafe_allow_html=True)
            st.caption("路由分布决定开销：static 3x / render 30x / crawl4ai 50x。"
                       "static 占比越高越省。")
        with b2:
            if reasons:
                st.markdown("<div class='section-label'>丢弃原因 top</div>",
                            unsafe_allow_html=True)
                for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:8]:
                    st.text(f"{count:>5}  {reason}")

    # ---------------------------------------------------------- 日志
    with st.expander("实时日志", expanded=False):
        auto = st.checkbox("自动刷新（5 秒）", value=False)
        st.code("\n".join(tail_log(80)) or "（还没有日志）", language="text")
        if auto:
            time.sleep(5)
            st.rerun()

    st.caption("命令行等价物：python lighthouse/status.py　"
               "详细统计：python lighthouse/report.py")


# 单独跑这一个视图：python -m streamlit run lighthouse_view.py
# 不只是方便调试——app.py 是脚本式入口（import 即 main()），
# 没法直接对整个 app 写渲染测试；给视图留这个口子才能用
# streamlit.testing.v1.AppTest 真正渲染一遍，确保没有异常。
if __name__ == "__main__":
    render()
