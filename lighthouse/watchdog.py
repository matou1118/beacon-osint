"""看门狗：调度器自己崩了就重新拉起，直到截止时间。

为什么需要
----------
overnight.py 内部已经能扛住「一轮采集失败」（子进程返回码非 0 也继续下一轮），
但扛不住**它自己**被杀——OOM、机器休眠唤醒失败、被人误关窗口。
出差期间没人盯着，得有个进程专门负责把它拉回来。

看门狗自己也可能崩，所以它做的事情必须极简：只 spawn、只探活、只记日志。

    python lighthouse/watchdog.py --rounds 20 --minutes 30 --deadline 07:30
    python lighthouse/watchdog.py --stop
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 看门狗是整条链的根：它 spawn overnight，overnight 再 spawn run.py，
# run.py 再拉 playwright 的 node 驱动。任何一层没装全局补丁，
# 下面那些孙进程就会自己分配控制台 —— 表现是桌面上一秒一闪的窗口。
# 补丁只对当前进程及其后代有效，所以**每一层都要自己装一次**。
from lighthouse.winflags import install as _install_no_window  # noqa: E402
from lighthouse.winflags import ensure_stdio as _ensure_stdio  # noqa: E402
_install_no_window()

RUNTIME = _ROOT / "data" / "runtime" / "lighthouse"
LOG = RUNTIME / "watchdog.log"
STOP_FLAG = RUNTIME / "WATCHDOG_STOP"

# ★ 必须排在 LOG 定义之后、任何 log() 之前。
#
# 计划任务 BeaconLighthouse 用 pythonw.exe 拉起，而计划任务不给控制台，
# sys.stdout 就是 None —— 于是下面 log() 里的第一个 print() 直接抛
# AttributeError，进程在写「看门狗启动」之前就死了。实测表现为
# LastTaskResult=2、State=Ready、日志零新增，看着像「正常退出没活干」。
#
# 更早之前采集看起来一直正常，是因为跑的是手动那条 python.exe 链；
# 计划任务这条从来没起来过，机器一重启就断链，而日志里什么都看不出来。
_ensure_stdio(LOG.with_suffix(".out"))
# 单例标记：记当前看门狗 pid，供下一个实例判断「已经有我了」
WATCHDOG_PID = RUNTIME / "watchdog.pid"
STATUS_FILE = RUNTIME / "status.json"
PY = sys.executable

# 调度器正常跑完一轮要 30 分钟左右。间隔这么久还没动静就当它死了。
STALL_SECONDS = 3600
_stop = False


def log(text: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}"
    print(line, flush=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def scheduler_exited_cleanly(status_file: "Path | None" = None) -> bool:
    """调度器是「正常完成」还是「异常退出」。

    读 status.json 的 round_log：最近一条的 exit 非 0（或带 error、或压根没有
    round_log）才算异常。退出原因由调度器自己写，比看子进程退出码可靠 ——
    子进程可能��外部杀掉，退出码却是 0。
    """
    try:
        st = json.loads(Path(status_file or STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    log_rows = st.get("round_log") or []
    if not log_rows:
        return False
    last = log_rows[-1]
    if not isinstance(last, dict):
        return False
    if last.get("error"):
        return False
    try:
        return int(last.get("exit", 1)) == 0
    except (TypeError, ValueError):
        return False
def _handle(_signum: int, _frame: object) -> None:
    global _stop
    _stop = True


def seconds_until_next_due(status_file: "Path | None" = None) -> float | None:
    """距离下一个源到期还有多少秒。读不到就返回 None。

    退避闸把 34 个源全挡住时，调度器会「无事可做」秒退（round_log 里
    exit=0）。这时固定睡 60 秒再拉，等于一夜空转 1400 次 —— 什么也没干，
    还把日志刷满。改成按 next_due 等到点再拉。

    那个 epoch 由 run.py 写进每轮的 shadow-*.json（next_due_epoch），
    status.json 顶层没有，所以优先读 status，退而求其次扫最新几份报告。
    """
    import glob

    try:
        st = json.loads(Path(status_file or STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    due = st.get("next_due_epoch")
    # 只在**字段缺失**时���去扫报告，不能用 `if not due`：next_due_epoch
    # 合法地等于 0（run.py 的 `int(next_due or 0)`，含义是「没有源在等」，
    # 该走固定节奏），而 0 是falsy，会被这里当成「没读到」而回退去翻
    # shadow-*.json —— 翻到的是历史报告里的旧 epoch，于是把一个明确的
    # 「按固定节奏」变成了「按某个过期时刻等」。2026-10-03 整夜零入库
    # 就是这么来的：status.json 写着 0（去跑），这里翻出 8.5 小时前的旧值。
    if due is None:
        reports = sorted(glob.glob(str(RUNTIME / "shadow-*.json")),
                         key=os.path.getmtime, reverse=True)
        for path in reports[:3]:
            try:
                with open(path, encoding="utf-8") as fh:
                    due = json.load(fh).get("next_due_epoch")
            except (OSError, ValueError):
                continue
            if due is not None:
                break
    try:
        due_i = int(due)
    except (TypeError, ValueError):
        return None
    if due_i <= 0:
        return None
    # 拿到**已经过期**的到期时间，说明上一轮报告写的是旧值，而调度器
    # 此刻很可能有源可采。这时该立刻重拉，而不是睡 30 秒。
    #
    # 2026-10-03 实测：145 个源全部已到期（退避表里 earliest < now），
    # 但最新报告的 next_due_epoch 是 8.5 小时前的旧值，
    # ``max(30, due - now)`` 把负数夹成 30 —— 于是 watchdog 每 30 秒拉起一次、
    # 每��都「无源可采」，日志刷满而一条没采到，整夜停在 10-03 零入库。
    #
    # 负值本身就是「现在就拉」的信号，不需要夹到 30 秒。
    if due_i <= time.time():
        return 0.0
    return max(30.0, due_i - time.time())


def progress_stamp() -> float:
    """调度器最后一次动 status.json 的时间。"""
    path = RUNTIME / "status.json"
    if not path.exists():
        return 0.0
    return path.stat().st_mtime


def _pid_alive(pid: int) -> bool:
    """进程是否还活着。

    刻意**不用** ``os.kill(pid, 0)``：在 Windows 上那个调用是 TerminateProcess
    的意思，会真的把目标杀掉并返回 0（``collectors/lock.py`` 的注释里记过
    踩坑：校验脚本和 pytest 都被自己静默杀掉，表现为「命令没输出、退出码 0」）。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
                    handle, ctypes.byref(code)):
                return code.value == 259          # STILL_ACTIVE
            return False
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _orphan_scheduler_pids(csv_text: str, me_pid: int = 0) -> list[tuple[int, int]]:
    """从 wmic CSV 里挑出「无主调度器」，返回 [(pid, 父pid), ...]。

    判据两条都要满足：
      * 命令行含 ``overnight.py`` —— 只清调度器，别误杀别的 python 进程
      * **父进程已死** —— 父进程还活着的那个是当前链自己的调度器

    为什么值得抽成纯函数：这段逻辑埋在 main() 的闭包里时，只能靠「搜源码
    字符串」来钉，而源码里就写着 ``os.kill(ppid, 0)``（在注释里解释为什么
    不用它）——文本匹配根本分不清那是注释还是代码。抽出来才能真测。

    **必须用 CSV。** 裸 ``get A,B,C`` 在命令行含空格/换行时会塌成单列
    （CommandLine 自己就带换行），按位置切列必然切错。2026-10-07 实测踩过：
    ``split(None, 2)`` 把整条命令行当成了 pid。
    """
    out: list[tuple[int, int]] = []
    for row in csv.reader(io.StringIO(csv_text)):
        # 列序：Node,CommandLine,ParentProcessId,ProcessId；表头与空行跳掉。
        if len(row) < 4 or row[0].strip().lower() == "node":
            continue
        cmd, ppid_s, pid_s = row[1], row[2], row[3]
        if "overnight.py" not in cmd:
            continue
        try:
            pid, ppid = int(pid_s), int(ppid_s)
        except ValueError:
            continue
        if pid == me_pid:
            continue
        # 父进程还活着 = 当前链的调度器，留着。
        # 用 _pid_alive 而不是 os.kill(ppid, 0)：Windows 上后者是
        # TerminateProcess 的意思，会**真的杀掉**父进程（见 _pid_alive 的注释）。
        if _pid_alive(ppid):
            continue
        out.append((pid, ppid))
    return out


def reap_orphan_schedulers() -> None:
    """watchdog 启动前清掉上一代留下的无主调度器。

    为什么需要（2026-10-09 实测）：watchdog 由计划任务每 15 分钟重复触发，
    而它拉起的 overnight 用 DETACHED_PROCESS 脱离了进程组，所以
    **watchdog 被重启时，已经拉起的调度器不会被一起带走**。实测一天攒了 2 个：

        pid 4720  overnight  父=4384(已死) 07:23 启动
        pid 5280  overnight  父=1760(已死) 08:53 启动

    它们卡在 collect.lock 上干等（--lock-wait-minutes 20），纯占内存；更糟的是
    **它们也在写 status.json** —— status_summary 于是读到死掉的孤儿写的轮次
    日志，汇报出来的 rounds_done / 入库量全不可信。

    杀错一个正在采集的调度器，代价远大于留一个孤儿，所以判据必须同时看
    「是不是调度器」和「父进程还在不在」。
    """
    if os.name != "nt":
        return
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name like 'python%'",
             "get", "CommandLine,ParentProcessId,ProcessId", "/format:csv"],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.decode("mbcs", errors="replace")
    except Exception:                                     # noqa: BLE001
        return
    killed = 0
    for pid, ppid in _orphan_scheduler_pids(out, os.getpid()):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            killed += 1
            log(f"清理无主调度器 pid={pid}（父进程 {ppid} 已死）")
        except Exception:                                 # noqa: BLE001
            pass
    if killed:
        log(f"共清理 {killed} 个无主调度器")


def _another_watchdog_alive() -> bool:
    """记录里的 pid 是不是别的、且还活着的看门狗。"""
    if not WATCHDOG_PID.exists():
        return False
    try:
        recorded = int(WATCHDOG_PID.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if recorded == os.getpid():
        return False
    return _pid_alive(recorded)


def _spawn_detached(cmd: list[str]) -> int:
    """起一个**真正独立**的子进程。

    光把 watchdog 自己用 WMI 脱离（lighthouse/launch.py）是不够的：
    watchdog 再用普通 Popen 起调度器时，父进程是 watchdog，整条链
    仍然挂在启动它的会话上。实测就是这样——WMI 起了 watchdog，
    它的子进程照样被连带杀掉（23:57 起到 01:10，watchdog 和 scheduler
    一起消失）。

    所以每一级都要用 DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP 断开：
        0x00000008  DETACHED_PROCESS       脱离父进程的进程组
        0x00000200  CREATE_NEW_PROCESS_GROUP  自成一组，收 Ctrl+C 也影响不到
    Windows 之外用 start_new_session。
    """
    # ★ PYTHONPATH 必须显式给子进程。
    #
    # 2026-10-03 实测：watchdog 拉起的 overnight.py **每轮 20 秒就死**，
    # 而 overnight.log 最后一行停在 10-02 09:09 —— 也就是说从那时起
    # overnight 一次都没真正跑起来，watchdog 却一直报「调度器正常完成」
    # （它读的是旧 status.json 里 10-02 那轮留下的 exit=0）。
    # 于是 143 个源整夜零入库，日志里连一行报错都没有。
    #
    # 真因：`python -u lighthouse/overnight.py` 这种「按路径跑脚本」的
    # 方式，sys.path[0] 是脚本所在目录（lighthouse/），不是项目根，
    # 所以 overnight.py 里的 `from lighthouse.atomic_write import ...`
    # 直接 ModuleNotFoundError。cwd 设成项目根救不了它——cwd 不进 sys.path。
    #
    # 而子进程的 stdout/stderr 全接 DEVNULL，异常一行都看不见，
    # 于是崩溃被伪装成「正常完成」。两处一起改：给路径，也给日志。
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    kwargs: dict = {"cwd": str(_ROOT), "env": env}
    if os.name == "nt":
        # CREATE_NEW_CONSOLE 0x10 + CREATE_UNICODE_ENVIRONMENT 0x400，
        # 配 STARTF_USESHOWWINDOW / SW_HIDE。
        #
        # 为什么是「有控制台但隐藏」而不是「完全不要控制台」——这是弹窗的根治点。
        # 父进程没有控制台时，playwright 启动它的 node 驱动会自己分配一个
        # （conhost.exe 起来就退，桌面上一秒一闪，实测约每分钟一次）。chrome、
        # msedge 同理。
        #
        # 实测（lighthouse/_test_hidden_console.py，真实渲染 mof.gov.cn）：
        #     CREATE_NO_WINDOW                      -> 3 次 conhost
        #     隐藏控制台                             -> 1 次 conhost
        #     隐藏控制台 + winflags 全局补丁          -> 0 次 conhost
        #
        # winflags 只在本进程生效，传不到子进程；所以每一级 spawn 都得自己给
        # 一个隐藏控制台，让子孙继承它。CREATE_NO_WINDOW 不提供控制台，
        # 下游就会自己再分配——那正是弹窗的成因。
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0                       # SW_HIDE
        kwargs["creationflags"] = 0x00000010 | 0x00000400
        kwargs["startupinfo"] = info
        kwargs["stdin"] = subprocess.DEVNULL
        # 调度器的输出接进它自己的日志，不要 DEVNULL。
        #
        # 上一版把 stdout/stderr 全丢进 DEVNULL，结果 overnight.py 的
        # ModuleNotFoundError 一行都看不到，watchdog 只看到「子进程退出了、
        # 退出码 0 的旧记录还在」——于是一次崩溃被当成正常完成整整一夜。
        # 子进程死掉必须留痕，否则 watchdog 就是瞎的。
        try:
            kwargs["stdout"] = open(RUNTIME / "scheduler.out", "a", encoding="utf-8")
            kwargs["stderr"] = subprocess.STDOUT
        except OSError:
            kwargs["stdout"] = subprocess.DEVNULL
            kwargs["stderr"] = subprocess.DEVNULL
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs).pid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通宵调度看门狗")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--limit", type=int, default=600)
    parser.add_argument("--stale", type=int, default=200)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--cooldown-minutes", type=int, default=10,
                        help="重启次数用尽后的冷却分钟数，冷却完重新守夜"
                             "（不弃守——弃守后看门狗还活着但不再拉采集，"
                             "比崩溃更难发现）")
    parser.add_argument("--lock-wait-minutes", type=int, default=20,
                        help="透传给 overnight：起 run.py 前等采集锁的最大分钟数")
    parser.add_argument("--max-zero-rounds", type=int, default=0,
                        help="连续多少轮零入库就停。0=不限（推荐：政府源按天"
                             "更新，白天连续几轮没新东西是正常的，不是采干）")
    parser.add_argument("--repair-batch", type=int, default=6)
    # 翻页深度从 12 降到 4，与 config/web_collector_config.yaml 的
    # collect_defaults.archive_pages 保持一致（2026-09-30）。
    #
    # 两处必须一致：这个值会被拼成 `--archive-pages N` 传给 run.py，命令行
    # 参数**覆盖**配置里的 collect_defaults。只改配置不改这里，watchdog 起的
    # 每轮仍是 12 页 —— 改配置看起来生效了、实测却没变化，就是踩这个坑。
    #
    # 12 页配 playwright 渲染时 6 个源烧满 300 秒预算全部零产出（实测一轮
    # 46% 时间花在零产出源上）。4 页覆盖绝大多数增量。
    parser.add_argument("--depth", type=int, default=30)
    parser.add_argument("--depth-max", type=int, default=60)
    parser.add_argument("--deadline", default="none",
                        help="到点停止，HH:MM。默认 none = 一直守到被显式停掉")
    parser.add_argument("--max-restarts", type=int, default=2000,
                        help="异常退出重试上限。200 太低：一夜真崩溃 200 次"
                             "约 1 小时就把守夜用尽（20 秒一轮）。")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)

    RUNTIME.mkdir(parents=True, exist_ok=True)
    if args.stop:
        STOP_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")
        log("已写看门狗停止标记")
        return 0
    if STOP_FLAG.exists():
        STOP_FLAG.unlink()

    # 单例：同一时刻只允许一个看门狗。
    #
    # 为什么必须有：`BeaconLighthouse` 这个计划任务是 LogonTrigger，会在下次
    # 登录时自己拉起一个 watchdog。2026-10-01 实测那个任务的参数是过时的
    # （--depth 12 --limit 60 --minutes 35，而调优后是 30/600/600）。没有单例
    # 保护的话，两个看门狗各自拉起调度器 → 两个采集器写同一个库 → 就是当天
    # 傍晚那 62 次连续异常退出的成因。
    #
    # 不复用 collectors.lock：那把锁是给「采集时短暂持有」设计的，看门狗要
    # 一直持着，会把正常采集也挡住。
    if _another_watchdog_alive():
        log(f"已有看门狗在跑（pid {WATCHDOG_PID.read_text(encoding='utf-8').strip()}），"
            f"本次不重复启动")
        return 0
    WATCHDOG_PID.write_text(str(os.getpid()), encoding="utf-8")

    signal.signal(signal.SIGINT, _handle)
    try:
        signal.signal(signal.SIGTERM, _handle)
    except (AttributeError, ValueError):
        pass

    now = datetime.now()
    # 默认 none：不设截止。钟点不是停采集的理由，衰减了该往深里挖。
    no_deadline = args.deadline.strip().lower() in {"none", "", "-", "0"}
    if no_deadline:
        deadline = now + timedelta(days=3650)
    else:
        hour, _, minute = args.deadline.partition(":")
        deadline = now.replace(hour=int(hour), minute=int(minute or 0),
                               second=0, microsecond=0)
        if deadline <= now:
            deadline += timedelta(days=1)

    log("=" * 66)
    log(f"看门狗启动 pid={os.getpid()} "
        f"{'不设截止，一直守到被停掉' if no_deadline else '截止 ' + deadline.strftime('%m-%d %H:%M')}"
        f" 最多重启 {args.max_restarts} 次 起始深度 {args.depth}")

    restarts = 0
    child_pid = 0

    # 先清上一代留下的无主调度器，再开始守夜。理由见 reap_orphan_schedulers。
    reap_orphan_schedulers()

    def child_alive() -> bool:
        if os.name == "nt":
            # CREATE_NO_WINDOW 必须显式给：winflags 的全局补丁只改「调用方没传
            # creationflags」的情况，而这里如果传了 text=True 之外的组合，
            # 补丁不一定覆盖到。tasklist 每次判断进程存活都要跑一次，
            # 不给隐藏控制台就是桌面上一秒一闪一个黑框（2026-10-03 实测）。
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {child_pid}"],
                capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.decode("mbcs", errors="replace")
            return str(child_pid) in out
        try:
            os.kill(child_pid, 0)
            return True
        except OSError:
            return False

    while not _stop and datetime.now() < deadline:
        if STOP_FLAG.exists():
            log("看到停止标记，退出")
            break
        if child_pid and child_alive():
            time.sleep(20)
            continue

        if child_pid:
            # 区分「正常完成」和「崩溃」。两者都表现为「子进程不在了」，
            # 但只有后者该计入重启次数。
            #
            # 回归 2026-09-30 20:52~20:55：退避闸把 34 个源全挡住后，调度器
            # 每轮无事可做、秒退（round_log 里 exit=0，是正常结束）。watchdog
            # 把它当成崩溃，20 秒一轮连打 199 次，撞满 max_restarts=200 后
            # 直接弃守退出 —— 那一夜采集就此停摆，而日志里只有一行
            # 「重启次数用尽」，看不出真正原因。
            #
            # 判据用 status.json 的 round_log：最近一条 exit 非 0（或缺字段）
            # 才算崩溃。退出原因由调度器自己写，比看退出码更可靠（子进程可能
            # 被外部杀掉，exit 却是 0）。
            if scheduler_exited_cleanly():
                # 调度器正常完成 = 本轮所有源都在回访/退避闸里。**别反复空转**：
                # 固定睡 60 秒再拉一次的话，一夜就是 1440 次「拉起→秒退→睡 60
                # 秒」，什么也没干，还把日志刷满。正确做法是等下一个源到期。
                wait_s = seconds_until_next_due()
                if wait_s is None:
                    log("调度器正常完成，等 300 秒后重试（读不到到期时间）")
                    time.sleep(300)
                else:
                    log(f"调度器正常完成（无源可采），{wait_s:.0f} 秒后有源到期再拉起")
                    # 封顶 1 小时：到点后即便调度器判断有偏差，也能自己纠正。
                    #
                    # 下限 30 秒只在「到期时间还没到」时才有意义；
                    # wait_s == 0 表示**已经到期**，那是要立刻重拉，
                    # 被 max(..., 30) 夹成 30 秒就退化成空转了
                    # （2026-10-03 整夜零入库就是这个）。
                    if wait_s <= 0:
                        log("到期时间已过，立即重拉")
                        time.sleep(0)
                    else:
                        time.sleep(min(max(wait_s, 30), 3600))
                child_pid = 0
                continue
            if restarts >= args.max_restarts:
                # ★ 冷却后**重置计数**，而不是弃守退出。
                #
                # 重启上限原本是「用尽就退出」，但对「跑一整夜」这个要求是错的：
                # 一旦崩溃循环用尽额度，看门狗就永久弃守，之后**再也不会**拉起
                # 采集，而看门狗自己还活着、不报警——比直接崩溃更难发现。
                # 2026-10-03 实测正是这样：2000 次额度在半小时内被
                # overnight 的崩溃循环耗尽，日志最后写「看门狗结束，共重启
                # 2000 次」，之后整夜零采集。
                #
                # 改成冷却：一段时间内连续崩这么多次，说明不是瞬时抖动，
                # 但不代表永远起不来。睡 10 分钟、计数归零、再守。
                # 真正的终局交给「重启也拉不起来」那条分支去判。
                log(f"重启次数用尽（{restarts}），冷却 {args.cooldown_minutes} 分钟后重新守夜")
                time.sleep(args.cooldown_minutes * 60)
                restarts = 0
                child_pid = 0
                continue
            restarts += 1
            log(f"调度器（pid {child_pid}）异常退出，第 {restarts} 次重启")
        else:
            log("拉起调度器")

        cmd = [PY, "-u", "lighthouse/overnight.py",
               "--rounds", str(args.rounds), "--minutes", str(args.minutes),
               "--limit", str(args.limit), "--stale", str(args.stale),
               "--workers", str(args.workers),
               "--depth", str(args.depth), "--depth-max", str(args.depth_max),
               "--repair-batch", str(args.repair_batch),
               # ★ 显式传 0 = 不限轮数。
               #
               # overnight 的 --max-zero-rounds 默认是 3：连续 3 轮零入库就
               # 「源已采干」提前停止。可源是**按天更新**的，白天到夜里很可能
               # 连续 3 轮都采不到新东西——那是正常状态，不是采干。
               #
               # 2026-10-03 实测踩过：日志里「全部结束：3 轮，净增 0 条
               # （10621 → 10621）」，一次通宵被这个默认值提前掐断。
               #
               # 轮间节流（等下一个到期时间）本来就不会空转，所以不限轮数是
               # 安全的：没源可采时它睡到下一个到期时间再试，而不是原地打转。
               "--max-zero-rounds", str(args.max_zero_rounds),
               "--lock-wait-minutes", str(args.lock_wait_minutes),
               "--deadline", args.deadline]
        child_pid = _spawn_detached(cmd)
        log(f"  调度器 pid={child_pid}（已脱离进程组）")
        time.sleep(20)

    if child_pid and child_alive():
        log("让调度器自己跑完（不杀它）")
    log(f"看门狗结束，共重启 {restarts} 次")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        # 清掉单例标记。残留 pid 会让下一个实例误判「已经有看门狗了」而永不启动。
        # 只在标记指向自己时才删——别删掉别人正在用的。
        try:
            if (WATCHDOG_PID.exists()
                    and WATCHDOG_PID.read_text(encoding="utf-8").strip() == str(os.getpid())):
                WATCHDOG_PID.unlink(missing_ok=True)
        except OSError:
            pass
