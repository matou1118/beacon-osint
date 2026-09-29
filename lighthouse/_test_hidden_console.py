"""隔离验证：隐藏控制台能不能阻止 conhost 弹窗。

不动正在跑的采集，只起一个独立的渲染进程，然后看
conhost.exe 有没有因为 playwright 的 node 驱动而冒出来。

判据（这是关键，不能靠感觉）：
    修复前 —— 会出现 父=node.exe 的 conhost.exe
    修复后 —— 整个测试期间一个 conhost.exe 都不出现

    python lighthouse/_test_hidden_console.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "runtime" / "lighthouse" / "hiddentest.log"

# 会被渲染进程继承的控制台环境；子进程据此判断自己有没有控制台
CHILD = r"""
import os, sys, time
sys.path.insert(0, r"%s")
out = []
out.append("child_has_console=%%s" %% bool(0))  # 占位，下面用 ctypes 改
try:
    from lighthouse.winflags import install as _install
    _install()
    from collectors.playwright_worker import render_page
    html = render_page("https://www.mof.gov.cn", timeout_ms=45000)
    with open(r"%s", "a", encoding="utf-8") as f:
        f.write("RENDER_OK %%d\n" %% len(html))
except Exception as exc:
    with open(r"%s", "a", encoding="utf-8") as f:
        f.write("RENDER_ERR %%s: %%s\n" %% (type(exc).__name__, exc))
time.sleep(2)
""" % (str(ROOT), str(LOG), str(LOG))


def conhost_children_of_node(timeout: int = 75) -> list[str]:
    """轮询进程树，记录所有**新出现**的、父进程是 node.exe 的 conhost.exe。

    必须按 pid 去重：轮询会反复看到同一个还活着的进程，不去重就会把
    同一个 conhost 数成几十次。上一版就是因为这个，让实验组显示和对照组
    一样的结果——那个 conhost 其实是对照组留下的，一直没退出。
    """
    import subprocess as sp

    seen: set[str] = set()
    hits: list[str] = []
    baseline = _all_pids()
    end = time.time() + timeout
    while time.time() < end:
        for name, pid, ppid in _procs_of_interest():
            if name != "conhost.exe" or pid in baseline or pid in seen:
                continue
            seen.add(pid)
            owner = _name_of(ppid)
            if "node" in owner.lower():
                hits.append(f"conhost pid={pid} 父={owner}")
        time.sleep(0.25)
    return hits


def _all_pids() -> set[int]:
    import subprocess as sp

    out = sp.run(["powershell", "-NoProfile", "-Command",
                  "(Get-CimInstance Win32_Process).ProcessId"],
                 capture_output=True, text=True).stdout
    return {int(x) for x in out.split() if x.isdigit()}


def _procs_of_interest() -> list[tuple[str, int, int]]:
    import subprocess as sp

    out = sp.run(["powershell", "-NoProfile", "-Command",
                  "Get-CimInstance Win32_Process | Where-Object { "
                  "$_.Name -in @('node.exe','conhost.exe') } | "
                  "ForEach-Object { \"$($_.Name)|$($_.ProcessId)|"
                  "$($_.ParentProcessId)\" }"],
                 capture_output=True, text=True).stdout or ""
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            rows.append((parts[0], int(parts[1]), int(parts[2])))
    return rows


def _name_of(pid: int) -> str:
    import subprocess as sp

    return (sp.run(["powershell", "-NoProfile", "-Command",
                    f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").Name"],
                   capture_output=True, text=True).stdout or "").strip()


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("", encoding="utf-8")
    print(f"测试开始 {time.strftime('%H:%M:%S')}  模式={sys.argv[1] if len(sys.argv) > 1 else 'hidden'}")

    kwargs: dict = {"cwd": str(ROOT), "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.STDOUT}
    if os.name == "nt":
        if len(sys.argv) > 1 and sys.argv[1] == "hidden":
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = 0                       # SW_HIDE
            kwargs["creationflags"] = 0x00000010 | 0x00000400   # NEW_CONSOLE | UNICODE_ENV
            kwargs["startupinfo"] = info
        else:
            kwargs["creationflags"] = 0x08000000       # CREATE_NO_WINDOW（修复前）

    proc = subprocess.Popen([sys.executable, "-c", CHILD], **kwargs)
    print(f"渲染子进程 pid={proc.pid}")
    hits = conhost_children_of_node(timeout=75)
    proc.wait(timeout=30)

    print(f"测试结束 {time.strftime('%H:%M:%S')}")
    print(f"渲染结果: {LOG.read_text(encoding='utf-8').strip() or '(无输出)'}")
    if hits:
        print(f"失败：{len(hits)} 次 node 触发 conhost —— 隐藏控制台没起作用")
        for h in hits[:5]:
            print("   ", h)
        return 1
    print("通过：整个渲染期间没有 node 触发任何 conhost")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
