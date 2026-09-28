"""系统代理探测：采集能不能跑，先看代理通不通。

为什么需要这个
  通宵那晚 03:30 那轮，101 个源全部 FAIL，每个 9 秒：
    [1/101] mohurd_gov FAIL (9s): 400 Proxy connect error: getaddrinfo ENOTFOUND
  ENOTFOUND 是**代理侧**的 DNS 解析失败——那一刻本机代理（127.0.0.1:31180）
  不通。那轮 7.5 分钟跑完、退出码 0、体检报"无异常"，整夜看起来是成功的，
  实际上一个字节都没采到。05:30 那轮代理恢复，才收进 254 条。

  代理是采集的硬依赖，但它配在 Windows 注册表里（ProxyEnable / ProxyServer），
  requests 会自动使用，采集代码自己完全不知道它挂了。等到每个源各自超时才发现，
  代价是 101 × 9 秒的假进度，而且报告里全是"正常结束"。

  所以在开跑前探一次：代理配了却连不上，直接说人话并以非零码退出，
  剩下的时间留给下一个 2 小时的切片。

代理没配的情况不算故障
  直接连网是合法部署方式。只有"配了却连不上"才是问题。
"""
from __future__ import annotations

import socket
import sys
from urllib.parse import urlparse

if sys.platform == "win32":
    import winreg


def system_proxy() -> tuple[str, int] | None:
    """读 Windows 注册表里的系统代理，返回 (host, port)；没配返回 None。

    只在 Windows 上有意义。其他平台 requests 会读环境变量
    （HTTP_PROXY/HTTPS_PROXY），那属于进程环境，不归这里管。
    """
    if sys.platform != "win32":
        return None
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)
            raw = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()
    except OSError:
        return None
    if not enabled or not raw:
        return None
    # 形如 "http=http://127.0.0.1:31180;https=http://127.0.0.1:31181"
    # 取第一个带端口的条目即可：通不通是同一个本地代理进程决定的。
    for part in raw.split(";"):
        candidate = part.split("=", 1)[-1].strip()
        if not candidate:
            continue
        parsed = urlparse(candidate if "//" in candidate else f"//{candidate}")
        if parsed.hostname and parsed.port:
            return parsed.hostname, parsed.port
    return None


def proxy_status(timeout: float = 3.0) -> tuple[bool, str]:
    """返回 (是否可用, 人话描述)。

    可用 = 没配代理（直连），或者代理端口能连上。
    """
    target = system_proxy()
    if target is None:
        return True, "未配置系统代理，走直连"
    host, port = target
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"系统代理 {host}:{port} 可连"
    except OSError as exc:
        return False, (f"系统代理 {host}:{port} 连不上（{type(exc).__name__}: {exc}）。"
                       "采集走代理，这条不通则所有源都会 ENOTFOUND/连接失败。")


if __name__ == "__main__":
    ok, message = proxy_status()
    print(("OK  " if ok else "FAIL") + " " + message)
    raise SystemExit(0 if ok else 1)
