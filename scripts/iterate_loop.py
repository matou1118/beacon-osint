"""通宵采集的自我迭代循环。

每隔一段时间自动做三件事：
  1. 跑 reconcile_check（核对：源列表 vs 库内记录）
  2. 跑 status_summary（汇总 + 停摆告警）
  3. 两者都写进一份 JSON 报告，累积成时间线

用法：
    python scripts/iterate_loop.py --once          # 跑一轮
    python scripts/iterate_loop.py --watch 1800    # 每 30 分钟一轮，自己跑一晚上
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPORTS = ROOT / "data" / "runtime" / "lighthouse" / "iteration"
REPORTS.mkdir(parents=True, exist_ok=True)


def _run(script: str) -> dict:
    """跑一个只读脚本，抓它的 JSON 输出。失败不抛 —— 循环不能因为
    一次检查失败就停。"""
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), "--json"],
        cwd=str(ROOT), capture_output=True,
        encoding="utf-8", errors="replace", timeout=900)
    if p.returncode != 0:
        return {"error": "exit=%d %s" % (p.returncode, (p.stderr or "")[:200])}
    text = p.stdout or ""
    i = text.find("{")
    if i < 0:
        return {"error": "no json in output", "raw": text[:200]}
    try:
        return json.loads(text[i:])
    except ValueError as exc:
        return {"error": "bad json: %s" % exc}


def one_round(n: int | None = None) -> dict:
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = {
        "at": ts,
        "iso": datetime.now().isoformat(timespec="seconds"),
        "summary": _run("status_summary.py"),
    }
    rec = _run("reconcile_check.py")
    out["reconcile"] = rec
    n = n or 0
    out["round"] = n

    # ★ 自主动作：检测到的问题 → 查原则表 → 执行动作。
    #
    # 2026-10-01 整夜的教训：系统能检测出「零入库」却只写进报告，等人来看。
    # 那不是自主，是提醒。接上 auto_act 之后，检测和执行在同一轮里闭环。
    auto = _run("auto_act.py")
    out["auto_act"] = auto

    path = REPORTS / ("iter-%s.json" % ts)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                    encoding="utf-8")

    s = out["summary"]
    alarms = s.get("alarms") or []
    print("[%s] 第 %s 轮" % (ts, n))
    print("  告警: %s" % ("；".join(alarms) if alarms else "无"))
    print("  库内: %s  今日: %s" % (s.get("db", {}).get("total"),
                                  s.get("db", {}).get("today")))
    r = rec
    if "problems" in r:
        probs = r["problems"] or []
        print("  核对问题: %d 个" % len(probs))
        for item in probs[:8]:
            # JSON 里每项是 {"source_id": ..., "why": ...}
            sid = item.get("source_id", "?") if isinstance(item, dict) else item[0]
            why = item.get("why", "") if isinstance(item, dict) else item[1]
            print("     %-18s %s" % (sid, why))
    elif "error" in r:
        print("  核对失败: %s" % r["error"][:100])
    else:
        print("  核对: 通过")
    # 自主动作的执行结果。**成功时也要打印**——不然「系统自己改了配置」
    # 这件事在日志里看不见，等于没发生。
    aa = out.get("auto_act")
    if isinstance(aa, dict) and aa.get("actions"):
        for item in aa["actions"]:
            if item.get("applied"):
                print("  自主动作: %s -> %s（%s）"
                      % (item.get("failure_type"), item.get("action"),
                         item.get("reason", "")))
            else:
                print("  自主动作未执行: %s（%s）"
                      % (item.get("failure_type"), item.get("reason", "")))
    elif isinstance(aa, str) and "auto_act" in aa:
        print("  自主动作: 执行器异常 %s" % aa[:90])
    print("  报告: %s" % path.name)
    return out


def main() -> int:
    args = sys.argv[1:]
    if "--once" in args:
        one_round(0)
        return 0
    interval = 1800
    for a in args:
        if a.startswith("--watch"):
            tail = a.split("=", 1)[1] if "=" in a else None
            if tail:
                interval = int(tail)
    interval = int(interval)
    print("自我迭代循环启动：每 %d 秒一轮，Ctrl-C 停止" % interval)
    n = 0
    while True:
        try:
            one_round(n)
        except KeyboardInterrupt:
            print("已停止")
            return 0
        except Exception as exc:                      # noqa: BLE001
            print("  本轮异常（继续）: %s: %s" % (type(exc).__name__, exc))
        n += 1
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("已停止")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
