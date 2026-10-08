"""真正脱离会话地启动采集守护进程。

为什么不能用 Start-Process
--------------------------
用 ``Start-Process -WindowStyle Hidden`` 起的进程，父进程是当前 shell，
整棵进程树挂在会话的 job object 上。实测：
    23:57 启动 -> 01:10 全部消失
日志里没有任何 Python 异常——进程是被从外面杀的。因为那段时间没有任何工具
调用在跑，会话被清理，整棵树跟着没了。日志停在某一轮中途（12/109，6.6 分钟），
没有收尾、没有 traceback，就是这个原因。

对「出差无人值守」这是致命的：能不能跑一整夜，取决于有没有人碰会话。

怎么脱离
--------
``Win32_Process.Create``（WMI）。新进程的父进程是 WMI Provider Host，
不在当前会话的进程树里，shell 退出、工具会话被清理都不影响它。
实测创建后进程 11976 的父进程是 WMI 建的 cmd，与本会话无关。

    python lighthouse/launch.py             # 起
    python lighthouse/launch.py --status    # 看
    python lighthouse/launch.py --stop      # 优雅停

说明：本工具不负责开机自启。机器重启后要重新跑一次；需要开机自启的话用
任务计划程序注册一个「用户登录时」触发的任务，调这条同样的命令。
"""
from __future__ import annotations

import argparse
import subprocess
import sys

# 本脚本用 tasklist / powershell / wmic 查进程，每个都要控制台。
# 不装 winflags 就是手工查一次状态闪一个黑框（2026-10-03 实测）。
from lighthouse.winflags import install as _install_no_window  # noqa: E402
_install_no_window()
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "data" / "runtime" / "lighthouse"
# 守护进程启动时带上的参数。用文件存而不是写死，这样改参数不用改这个文件
OPTS = RUNTIME / "launch_opts.txt"
LOG = RUNTIME / "overnight.log"


def default_opts() -> list[str]:
    return [
        "lighthouse/watchdog.py", "--rounds", "999", "--minutes", "35",
        "--deadline", "none", "--stale", "150", "--limit", "60",
        "--workers", "6", "--depth", "12", "--depth-max", "60",
        "--repair-batch", "6",
    ]


def read_opts() -> list[str]:
    if OPTS.exists():
        parts = OPTS.read_text(encoding="utf-8").split()
        if parts:
            return parts
    return default_opts()


def _wmi_create(command: str) -> tuple[int, str]:
    """通过 WMI 创建进程，脱离当前会话，且不分配控制台窗口。

    CREATE_NO_WINDOW(0x08000000) 只能用在 Create 方式上；Win32_Process.Create
    没有这个参数，所以改用 WMI 的 StartupInformation：
    ShowWindow = SW_HIDE(0)，并把桌面设为默认值（不指定的话 WMI 会用
    「新建桌面」，那反而可能多出一个窗口）。
    """
    ps = (
        "$si = New-CimInstance -ClassName Win32_ProcessStartupInformation "
        "-ClientOnly; $si.ShowWindow = 0; "
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{CommandLine={command!r}; ProcessStartupInformation=$si}}; "
        "if ($r) { Write-Output $r.ProcessId } else { Write-Output 'FAIL' }"
    )
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                          capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    return (int(out) if out.isdigit() else -1), out


def _hidden_console_kwargs() -> dict:
    """给子进程一个**隐藏的控制台**。

    为什么是「有控制台但隐藏」而不是「完全不要控制台」
    ----------------------------------------------------
    之前走的是 CREATE_NO_WINDOW：整条链没有控制台。问题是 Windows 上
    「父进程没有控制台」时，playwright 启动它的 node 驱动会**自己分配
    一个新的**（conhost.exe），起来就退——桌面上就是一秒一闪的窗口，
    间隔约 1 分钟。实测抓到的完整链条：

        10:03:04  node     pid=3076  父=pythonw  ...playwright\\driver\\node.exe
        10:03:04  conhost  pid=2564  父=node.exe  conhost.exe 0x4

    同样的问题还会发生在 chrome / msedge / 任何会 spawn 子进程的库上。
    逐个加标志是打地鼠。

    反过来做：让 watchdog 持有一个**隐藏的控制台**，它的所有子孙都继承这个
    控制台，谁都不会再自己分配。窗口不显示（SW_HIDE），但控制台存在。

    CREATE_NEW_CONSOLE 0x10  CREATE_UNICODE_ENVIRONMENT 0x400
    """
    import subprocess

    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0                       # SW_HIDE
    return {
        "creationflags": 0x00000010 | 0x00000400,
        "startupinfo": info,
    }


def start(extra: list[str]) -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    OPTS.write_text(" ".join(extra), encoding="utf-8")
    for flag in ("STOP", "WATCHDOG_STOP"):
        (RUNTIME / flag).unlink(missing_ok=True)
    _clear_stale_lock()
    if _lock_held_by_live_process():
        print("collect.lock 被活进程持有，先不启动")
        return 2
    return _spawn_watchdog(extra)


def _spawn_watchdog(extra: list[str]) -> int:
    """起 watchdog。它用普通 python.exe（要控制台）+ 隐藏控制台。

    不用 pythonw：pythonw 是 GUI 子系统的程序，本身就没有控制台，
    而「没有控制台」正是所有弹窗的根源。
    """
    import subprocess
    import sys

    console_python = Path(sys.executable).with_name("python.exe")
    exe = str(console_python if console_python.exists() else sys.executable)
    cmd = [exe, "-u", "lighthouse/watchdog.py", *extra]
    log_handle = open(LOG, "a", encoding="utf-8")
    try:
        kwargs = _hidden_console_kwargs()
    except Exception:                                          # noqa: BLE001
        kwargs = {}
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log_handle,
                            stderr=subprocess.STDOUT, **kwargs)
    print(f"已启动，pid={proc.pid}（隐藏控制台）")
    print(f"  解释器 {exe}")
    print(f"  参数 {' '.join(extra)}")
    print("  看状态：python lighthouse/status.py")
    print("  优雅停：python lighthouse/status.py --stop")
    return 0


def _lock_held_by_live_process() -> bool:
    lock = ROOT / "data" / "runtime" / "collect.lock"
    if not lock.exists():
        return False
    try:
        import json

        pid = int(json.loads((lock / "holder.json").read_text(encoding="utf-8"))["pid"])
        return _alive(pid)
    except Exception:                                          # noqa: BLE001
        return False


def _clear_stale_lock() -> None:
    """只清死进程留下的锁；活着的说明真有人在采。"""
    import shutil

    lock = ROOT / "data" / "runtime" / "collect.lock"
    if lock.exists() and not _lock_held_by_live_process():
        shutil.rmtree(lock, ignore_errors=True)


def _alive(pid: int) -> bool:
    # CREATE_NO_WINDOW：tasklist 自己要控制台，不给就闪一个框（2026-10-03 实测）。
    # text=True 用默认 utf-8 解 GBK 输出会 UnicodeDecodeError，所以显式 mbcs。
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        capture_output=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.decode("mbcs", errors="replace")
    return str(pid) in out


def status() -> int:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
         "Where-Object { $_.CommandLine -like '*lighthouse*' } | "
         "ForEach-Object { $r='other'; if($_.CommandLine -like '*watchdog*'){$r='watchdog'}"
         "elseif($_.CommandLine -like '*overnight.py*'){$r='scheduler'}"
         "elseif($_.CommandLine -like '*lighthouse\\run.py*'){$r='collect'}; "
         "\"$($_.ProcessId)`t$r\" }"],
        capture_output=True, text=True).stdout or ""
    rows = [r for r in out.replace("\t", " ").splitlines() if r.strip()]
    if not rows:
        print("没有 lighthouse 进程在跑")
        return 1
    print("进程：")
    for row in rows:
        parts = row.split()
        if len(parts) >= 2:
            print(f"  pid={parts[0]:<8}{parts[1]}")
    return 0


def stop() -> int:
    for name in ("watchdog.py", "overnight.py", r"lighthouse\run.py"):
        subprocess.run(["wmic", "process", "where",
                        f"CommandLine like '%{name}%'", "call", "terminate"],
                       capture_output=True)
    (RUNTIME / "STOP").write_text("stop", encoding="utf-8")
    (RUNTIME / "WATCHDOG_STOP").write_text("stop", encoding="utf-8")
    print("已停（graceful：当前轮跑完才退出）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="脱离会话地启动采集守护")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("args", nargs="*", help="透传给 watchdog.py 的参数")
    # parse_known_args 而不是 parse_args：watchdog 的参数（--rounds --minutes …）
    # 会原样透传下去，parse_args 会把它们当成自己的未知参数而报错退出。
    # 之前就是这么写的，结果任务计划程序调它时静默失败，采集根本没起来。
    args, passthrough = parser.parse_known_args(argv)
    if args.status:
        return status()
    if args.stop:
        return stop()
    return start(passthrough or default_opts())


if __name__ == "__main__":
    raise SystemExit(main())
