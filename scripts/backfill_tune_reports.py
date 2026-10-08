"""把历史 shadow 报告回灌进 tune-*.json。

TuneReport 早期版本没往报告里写 buckets/records/dropped，导致 tune_stats.py
聚合时「源处理累计」是空的。历史轮次回填一遍，之后新轮次自带数据。

只补字段，不改 findings —— findings 是当时那轮的判断，事后不该改。
"""
import glob
import json
import os
from datetime import datetime
from pathlib import Path

RUNTIME = Path("data/runtime/lighthouse")
tuning = RUNTIME / "tuning"

# 配对键 = 「调度器本趟启动时间之后的第 N 份 shadow」。
#
# 前两种配法都试过、都不对：
#   * 按「第 N 轮配第 N 份 shadow」—— shadow 目录堆了历次会话共 157 份，
#     而 tune 的轮次号只在当前这趟调度器里连续，会配到 9/28 的文件。
#   * 按 mtime 就近匹配 —— 重写 tune 文件本身就会刷新它的 mtime，
#     三份全被配到最后一份 shadow。
# started_at 是 status.json 里的字段，只有当前这趟调度器会写。
status = json.loads((RUNTIME / "status.json").read_text(encoding="utf-8"))
started = str(status.get("started_at") or "")
if not started:
    raise SystemExit("status.json 没有 started_at，无法定位本趟会话")
session_start = datetime.fromisoformat(started).timestamp()

shadows = sorted((p for p in RUNTIME.glob("shadow-*.json")
                  if p.stat().st_mtime >= session_start),
                 key=lambda p: p.stat().st_mtime)
print("本趟 started_at=%s，其后 shadow 报告 %d 份" % (started, len(shadows)))
if not shadows:
    raise SystemExit("本趟还没产出 shadow 报告")

filled = 0
for path in sorted(tuning.glob("tune-*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("buckets"):
        print("  %s 已有 buckets，跳过" % path.name)
        continue
    idx = int(data.get("round") or 0) - 1
    if idx < 0 or idx >= len(shadows):
        print("  %s: 本趟只有 %d 份 shadow，第 %d 轮没有对应"
              % (path.name, len(shadows), idx + 1))
        continue
    src = shadows[idx]
    sh = json.loads(src.read_text(encoding="utf-8"))
    data["buckets"] = sh.get("buckets") or {}
    data["records"] = int(sh.get("records") or 0)
    data["dropped"] = int(sh.get("dropped") or 0)
    data["backfilled_from"] = src.name
    for f in data.get("findings") or []:
        if f.get("kind") == "coverage":
            f["evidence"] = {"buckets": data["buckets"],
                             "records": data["records"],
                             "dropped": data["dropped"],
                             "ingested": 0}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    filled += 1
    print("  %s <- %s  buckets=%s records=%d dropped=%d"
          % (path.name, src.name, data["buckets"], data["records"], data["dropped"]))
print("回填 %d 份" % filled)