"""让本进程 spawn 出来的**所有**子进程都不带控制台窗口。

为什么需要这一层
----------------
弹窗的根因是「父进程没有控制台时，Windows 会给子进程分配一个新的」。

修过两层，都不彻底：

1. 给采集进程加 CREATE_NO_WINDOW —— 无效。实测 3 次 conhost 被 node 触发。
2. 给采集进程一个**隐藏的控制台**（CREATE_NEW_CONSOLE + SW_HIDE）——
   3 次降到 1 次，但没清零。playwright 启动 node 驱动时用了自己的标志，
   不继承父控制台，于是照样自己分配一个。

所以正确的层级不是「逐个库去改」，而是「这个进程里 spawn 出来的任何东西都
无窗口」。做法是给 ``subprocess.Popen.__init__`` 打个补丁：调用方没显式指定
creationflags 时，统一补上 CREATE_NO_WINDOW。

覆盖到的（实测会自己 spawn 子进程的）：
    playwright 的 node 驱动        <- 弹窗的真凶
    crawl4ai / cloakbrowser 的浏览器进程
    repair / promote / probe
    以后任何新引入的库

什么时候不生效
--------------
调用方**自己传了** creationflags 的地方不受影响——那是它有意为之（比如
playwright 自己传了标志）。那需要逐个处理，见 ``EXTRA`` 里列的已知点。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
_applied = False

#: ``ensure_stdio()`` 在没传参时用的兜底日志路径。与 watchdog/overnight 共用一个
#: 目录，这样「计划任务起的进程」和「手动起的进程」写进同一个文件，出问题时
#: 两边的痕迹能对上。
RUNTIME_DEFAULT_LOG = Path(__file__).resolve().parents[1] / "data" / "runtime" / "lighthouse" / "scheduler.out"


def install() -> bool:
    """给 subprocess.Popen 打补丁：子进程不带控制台窗口。幂等。

    关键：**不覆盖调用方已经指定了控制台策略的场合**。

    前一版无条件强制 CREATE_NO_WINDOW，结果把 playwright_worker 里
    Renderer 给 worker 刚设好的 CREATE_NEW_CONSOLE 冲掉了——两个补丁互相
    打架，worker 那层的隐藏控制台等于没生效，全链路实测仍有残留。
    调用方显式设了 CREATE_NEW_CONSOLE（配 STARTUPINFO/SW_HIDE）时，
    它是知道自己在做什么的，这里必须让路。

    规则（按实测收敛）：
      * 调用方设了 CREATE_NEW_CONSOLE -> **保留**。那是 Renderer 给渲染 worker
        配的隐藏控制台，无条件覆盖会把那个冲掉（前一版就是这么坏的）。
      * 其余情况 -> 剥掉 DETACHED_PROCESS 并补 CREATE_NO_WINDOW。
        DETACHED_PROCESS 是 node 弹窗的直接原因：它让 Windows 脱离现有控制台
        然后自己分配一个，conhost 起来就退，正是每秒一闪的那个窗口。
    """
    global _applied
    if _applied or os.name != "nt":
        return False
    original = subprocess.Popen.__init__

    def patched(self, *args, **kwargs):                  # type: ignore[no-untyped-def]
        flags = int(kwargs.get("creationflags") or 0)
        if not flags & CREATE_NEW_CONSOLE:
            flags = (flags & ~DETACHED_PROCESS) | CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched                  # type: ignore[assignment]
    _applied = True
    return True


    """保证 `sys.stdout` / `sys.stderr` **可用，且能编码 UTF-8**。

    为什么需要这一层
    ----------------
    无人值守链路上这一类 bug 修过两次，**两次根因都判错了**，所以必须记下来。

    第一次（`pythonw` + 计划任务，2026-10-05）：Windows 不给控制台，
    `sys.stdout is None`，第一个 `print()` 抛 `AttributeError`，
    进程在写「看门狗启动」之前就死。症状极有误导性：
    `LastTaskResult=2`、`State=Ready`、日志零新增 —— 看着像「正常跑完没活干」。
    而同一时刻手动那条 `python.exe` 链完全正常，所以「采集一直没断」是假象，
    计划任务其实从没起来过，机器一重启就断链。

    第二次（`scripts/pipeline.py`，2026-10-05）：第一版修法只检查
    「流能不能写」（`stream.write(")` 成功就放行），但**能写不等于能编码**。
    Windows 上 stdout 的编码是系统 ANSI 代码页（本机 GBK），写空串毫无问题，
    写 `⚠`（U+26A0）直接 `UnicodeEncodeError`，整个流水线进程死掉，
    后面所有步骤都不再执行。我据此把 4 个测试失败误诊成「import streamlit
    污染 stdout」—— 判别特征其实是那句 `UnicodeEncodeError: 'gbk' codec
    can't encode`，出现它就该去看哪个脚本 print 了非 GBK 字符。

    所以这里做三件事，缺一不可：

    1. `sys.stdout is None` 或已关闭 → 换成日志文件句柄（UTF-8）。
    2. 能 `reconfigure` → 强制 `encoding=utf-8, errors=replace`。
       **判据是编码能力，不是写入能力** —— 这是第一次修错的教训。
    3. 设 `PYTHONIOENCODING=utf-8` 到环境变量，让**子进程**也走 UTF-8。
       这一步最关键：`pipeline.py` 会拉起 batch_collect / reclassify /
       dedup 一串脚本，它们各自也会 print 中文和符号；只修父进程没用。

    返回实际生效的模式，调用方可以记进日志备查。
    """
    import sys

    if log_path is None:
        log_path = RUNTIME_DEFAULT_LOG
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_path = os.devnull

    # 子进程继承：比逐个给子脚本打补丁可靠得多，也省事得多。
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ.setdefault("PYTHONUTF8", "1")

    mode = "unchanged"
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or getattr(stream, "closed", False):
            try:
                stream = open(log_path, "a", encoding="utf-8", buffering=1)
            except OSError:
                stream = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, stream)
            mode = "redirected"
            continue
        # ★ 关键一步：**重新配置编码**，而不只是看它「能不能写」。
        #
        # 第一版修法只写 `stream.write("")` 试一下，成功就放行——但
        # **能写不等于能编码**。Windows 上 stdout 的编码是系统 ANSI 代码页
        # （本机 GBK），写空串完全正常，写 ``⚠``（U+26A0）直接
        # UnicodeEncodeError，进程当场死。
        # 2026-10-05 实测：scripts/pipeline.py 就这样把整条
        # 清洗/补字段/分类/去重链打断了，而且因为测试里报的是
        # 「找不到中文字符串」，我一度误诊成 import 顺序污染。
        #
        # errors="replace" 是有意的兜底：万一还有别的字符编不出去，
        # 输出一个替代字符也好过整个进程崩掉。
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        reconfigure = getattr(stream, "reconfigure", None)
        if encoding != "utf8" and callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
                mode = "reconfigured" if mode == "unchanged" else mode
            except (ValueError, OSError, AttributeError):
                pass
    return mode


def ensure_stdio(log_path=None):
    import sys

    if log_path is None:
        log_path = RUNTIME_DEFAULT_LOG
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_path = os.devnull

    # 子进程继承：比逐个给子脚本打补丁可靠得多，也省事得多。
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ.setdefault("PYTHONUTF8", "1")

    mode = "unchanged"
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or getattr(stream, "closed", False):
            try:
                stream = open(log_path, "a", encoding="utf-8", buffering=1)
            except OSError:
                stream = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, stream)
            mode = "redirected"
            continue
        # ★ 关键：重新配置「编码」，而不只是看它能不能写。
        # GBK stdout 写空串没问题、写 ⚠ 就炸——只看可写性会漏掉它。
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        reconfigure = getattr(stream, "reconfigure", None)
        if encoding != "utf8" and callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
                mode = "reconfigured" if mode == "unchanged" else mode
            except (ValueError, OSError, AttributeError):
                pass
    return mode


def windows() -> int:
    """已装的库中，本进程树里仍然会自己分配控制台的已知点。

    playwright 的 node 驱动是实测会弹窗的那个（父=node.exe 的 conhost）。
    它自带 creationflags，所以必须**强制覆盖**而不是「缺省时补」——
    两者都装的情况下仍实测到 1 次残留，见 install() 的说明。
    """
    return 1 if os.name == "nt" else 0
