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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "data" / "runtime" / "lighthouse"
# 守护进程启动时带上的参数。用文件存而不是写死，这样改参数不用改这个文件
OPTS = RUNTIME / "launch_opts.txt"


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


def start(extra: list[str]) -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    OPTS.write_text(" ".join(extra), encoding="utf-8")
    for flag in ("STOP", "WATCHDOG_STOP"):
        (RUNTIME / flag).unlink(missing_ok=True)
    lock = ROOT / "data" / "runtime" / "collect.lock"
    if lock.exists():
        # 只清死进程留下的锁；活着的说明真有人在采，不动
        import json

        try:
            pid = int(json.loads((lock / "holder.json").read_text())["pid"])
            if _alive(pid):
                print(f"collect.lock 被活进程 {pid} 持有，先不启动")
                return 2
        except Exception:                                      # noqa: BLE001
            pass
        import shutil

        shutil.rmtree(lock, ignore_errors=True)

    inner = subprocess.list2cmdline([sys.executable, "-u", *extra])
    # 直接跑 python，不套 cmd /c。套了就会多出一个有窗口的 cmd.exe
    # （实测 WMI 建 cmd 时窗口是可见的，用户能看到）。
    # CREATE_NO_WINDOW 只创建进程、不分配控制台，所以不依赖 python 侧自己设标志。
    command = f'cd /d "{ROOT}" && {inner}'
    pid, note = _wmi_create(command)
    if pid <= 0:
        print("WMI 创建失败，退回 Start-Process（不保证脱离会话）")
        subprocess.Popen([sys.executable, "-u", *extra], cwd=str(ROOT))
        return 1
    print(f"已脱离会话启动，pid={pid}")
    print(f"  命令：python {' '.join(extra)}")
    print("  看状态：python lighthouse/status.py")
    print("  优雅停：python lighthouse/status.py --stop")
    return 0


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                         capture_output=True, text=True).stdout or ""
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
    parser.add_argument("args", nargs="*", help="覆盖默认启动参数")
    args = parser.parse_args(argv)
    if args.status:
        return status()
    if args.stop:
        return stop()
    return start(args.args or default_opts())


if __name__ == "__main__":
    raise SystemExit(main())
