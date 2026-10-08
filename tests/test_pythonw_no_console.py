"""计划任务用 pythonw 拉起时，标准输出必须仍然可用。

2026-10-05 实测踩到的大坑：``BeaconLighthouse`` 计划任务（LogonTrigger）
用 ``pythonw.exe`` 拉起 watchdog，而计划任务**不给控制台** ——
Windows 于是不分配 stdout，``sys.stdout is None``。于是 ``log()`` 里的
第一个 ``print()`` 直接抛 ``AttributeError``，进程在写「看门狗启动」之前
就死了。症状极具误导性：

    Get-ScheduledTaskInfo → LastTaskResult = 2
    State                  = Ready（看起来像「正常跑完没事干」）
    watchdog.log           → 零新增（连启动横幅都没有）
    overnight.log          → 零新增

同一时刻采集看起来完全正常 —— 因为跑的是**手动**那条链（``python.exe``，
有控制台）。计划任务这条从来没起来过，机器一重启／重新登录就断链，
而日志里什么都看不出来。

对照组（三个都实测过）：
    ``python.exe`` 无重定向                     → 存活 45 秒+
    ``pythonw.exe`` + ``-RedirectStandardError`` → 存活
    ``pythonw.exe`` 无重定向                     → **启动即退 2**

所以修法是代码内自愈（``winflags.ensure_stdio``），而不是在计划任务里套
``cmd /c ... >> log``：后者依赖 cmd 引号在任务 XML 里存活，前者对
任何启动方式都成立。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lighthouse.winflags import ensure_stdio  # noqa: E402


def _run_probe(code: str) -> str:
    """跑一段代码并回传 stdout（刻意**不做**任何重定向，模拟计划任务）。"""
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, cwd=str(ROOT))
    return proc.stdout.decode("utf-8", errors="replace")


def test_ensure_stdio_keeps_healthy_stdout_untouched():
    """stdout 正常时必须是 no-op —— 绝不能把手动那条链的输出也吞进文件。"""
    probe = (
        "import sys;"
        "sys.path.insert(0, %r);" % str(ROOT) +
        "from lighthouse.winflags import ensure_stdio;"
        "before = sys.stdout.encoding;"
        "mode = ensure_stdio(%r);" % (str(ROOT / "data" / "runtime" / "lighthouse" / "_probe.log")) +
        "print(mode, before, sys.stdout.encoding)"
    )
    out = _run_probe(probe)
    assert "unchanged" in out, "健康的 stdout 不该被重定向，实际输出：%r" % out
    assert "\ufffd" not in out, "输出出现乱码，说明重定向破坏了编码：%r" % out


def _probe_writes_to_file(probe_setup: str, log_name: str) -> str:
    """在指定坏掉的 stdout 状态下跑探针，返回日志文件内容。

    注意：重定向后的输出**就该**进日志文件，而不是子进程的 stdout ——
    所以断言必须读文件。用 capture_output 去断言是错的（实测踩过：
    那样写即使功能正常也判失败，因为 print 已经被引到文件里去了）。
    """
    log = ROOT / "data" / "runtime" / "lighthouse" / log_name
    if log.exists():
        log.unlink()
    code = (
        "import sys;"
        + probe_setup +
        "sys.path.insert(0, %r);" % str(ROOT) +
        "from lighthouse.winflags import ensure_stdio;"
        "ensure_stdio(%r);" % str(log) +
        "sys.stdout.write('WRITE_OK\\n');"
        "sys.stdout.flush()"
    )
    subprocess.run([sys.executable, "-c", code], capture_output=True, cwd=str(ROOT))
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_ensure_stdio_repairs_none_stdout():
    """sys.stdout = None（pythonw + 计划任务）必须换成可用文件句柄。"""
    out = _probe_writes_to_file("sys.stdout = None;", "_probe_none.log")
    assert "WRITE_OK" in out, "stdout=None 时应能重定向写入日志文件，实际：%r" % out


def test_ensure_stdio_repairs_closed_stdout():
    """已关闭的 stdout 同样要救回来 —— 任务被杀后重定向的常见残留。"""
    out = _probe_writes_to_file("sys.stdout.close();", "_probe_closed.log")
    assert "WRITE_OK" in out, "closed stdout 应能重定向写入日志文件，实际：%r" % out


def test_watchdog_installs_guard_before_any_logging():
    """三入口都必须装这个垫片，且排在 LOG 定义之后（要用它算路径）。"""
    for name in ("watchdog.py", "overnight.py", "run.py"):
        src = (ROOT / "lighthouse" / name).read_text(encoding="utf-8")
        assert "ensure_stdio" in src, "%s 没装 ensure_stdio" % name
        assert src.index("_ensure_stdio(") > src.index("LOG = " if "LOG = " in src else "LOCK_DIR = "), (
            "%s 的 ensure_stdio 必须排在路径常量定义之后" % name)
        # 必须早于第一次 log() 调用
        first_log_call = src.index("\n    log(")
        assert src.index("_ensure_stdio(") < first_log_call, (
            "%s 的 ensure_stdio 必须排在第一次 log() 之前" % name)
