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

CREATE_NO_WINDOW = 0x08000000
_applied = False


def install() -> bool:
    """给 subprocess.Popen 打补丁。幂等。返回是否本次真的打了补丁。"""
    global _applied
    if _applied or os.name != "nt":
        return False
    original = subprocess.Popen.__init__

    def patched(self, *args, **kwargs):                  # type: ignore[no-untyped-def]
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched                  # type: ignore[assignment]
    _applied = True
    return True


def windows() -> int:
    """已装的库中，本进程树里仍然会自己分配控制台的已知点。

    playwright 的 node 驱动是实测会弹窗的那个（父=node.exe 的 conhost）。
    它自带 creationflags，所以 install() 拦不住。真要清零得改
    site-packages/playwright/driver/package/… 里的启动代码，那不值得——
    隐藏控制台已经把它从 3 次压到 1 次，剩下的间隔长、窗口小。
    """
    return 1 if os.name == "nt" else 0
