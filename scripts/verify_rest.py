"""续跑 max_items 验证：跳过已验过的源，直接在进程内跑完剩余的。

为什么单独封装
---------------
命令行里拼 122 个源 id 再 ``Start-Process``，PowerShell 引号转义失败了两次
（一次整个命令被 kill，一次 ArgumentList 收到 Null）。让脚本自己算「待验集」，
shell 只需一句：

    python scripts/verify_rest.py

验证结果本来就增量写进 ``data/runtime/lighthouse/max_items_verify.json``
（见 verify_max_items_cap.py），所以这里只需要**排除已验的键**即可续跑，
中断多少次都能接着来。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERIFY = ROOT / "data" / "runtime" / "lighthouse" / "max_items_verify.json"


def main() -> int:
    import yaml

    from verify_max_items_cap import main as verify_main

    en = [str(s["id"]) for s in yaml.safe_load(
        open(ROOT / "config" / "sources.yaml", encoding="utf-8"))["sources"]
        if s.get("enabled")]
    done = sorted(json.loads(VERIFY.read_text(encoding="utf-8"))) if VERIFY.exists() else []
    rest = [s for s in en if s not in done]
    print("已验 %d / %d，待验 %d" % (len(done), len(en), len(rest)), flush=True)
    if not rest:
        print("全部验完，无需续跑。")
        return 0
    # 把剩余源名和参数交回真正的验证脚本；argv 由 verify 自己解析。
    sys.argv = ["verify_max_items_cap.py", "--sources", ",".join(rest),
                "--archive-pages", "12"]
    return verify_main()


if __name__ == "__main__":
    raise SystemExit(main())