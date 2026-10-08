"""重探那 68 个源。

为什么现在探得动
----------------
它们的 notes 是 2026-09-25 的旧记录，写着「Playwright Sync API inside the
asyncio loop」——那次打死 46 个源，之后渲染改成了独立 worker 进程
（``collectors/playwright_worker.py``），bug 早已修掉。notes 只是没更新。

所以这批不是坏源，是**过期的坏记录**。刚才抽测 sc_ln_gov / sc_jx_gov /
sc_sd_gov 三个全部探测成功、采到 1 条，证明这条路通了。

分两批
------
* ``playwright`` 组（32 个）：当时被渲染 bug 误判
* ``pending``组（36 个）：探测器修好后一直没重跑

**不改任何源配置** —— 只产出 ``data/probes/probe-report.json``，
由人来决定启用哪些。探测器的目的是提供证据，不是替人做决定。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

SOURCES = ROOT / "config" / "sources.yaml"
PROBE = ROOT / "scripts" / "probe_sources.py"


def targets() -> tuple[list[str], list[str]]:
    data = yaml.safe_load(SOURCES.read_text(encoding="utf-8")) or {}
    sources = data.get("sources") or []
    playwright, pending = [], []
    for source in sources:
        if source.get("enabled"):
            continue
        notes = str(source.get("notes") or "")
        if "Playwright Sync API" in notes:
            playwright.append(str(source.get("id")))
        elif "待重新探测" in notes:
            pending.append(str(source.get("id")))
    return playwright, pending


def run(ids: list[str], label: str) -> int:
    if not ids:
        print("%s: 无目标" % label, flush=True)
        return 0
    print("=== %s：%d 个源，开始探测 ===" % (label, len(ids)), flush=True)
    cmd = [sys.executable, str(PROBE), "--include-pending", "--timeout", "60",
           "--report", str(ROOT / "data" / "probes" / ("probe-%s.json" % label))]
    # 注意：**不要**传 --summarize-report。那个参数是「读旧汇总做对比」，
    # 传一个不存在的路径会让探测器在最后一步 FileNotFoundError 崩掉，
    # 前面几十个源已经探完的结果也跟着白跑（2026-10-02 实测：68 个源只跑了 2 个）。
    for sid in ids:
        cmd += ["--source", sid]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    for line in tail[-4:]:
        print("   %s" % line[:200], flush=True)
    if proc.returncode != 0:
        print("   返回码 %d：%s" % (proc.returncode,
                                    (proc.stderr or "")[-300:]), flush=True)
    return 0 if proc.returncode == 0 else 1


def main() -> int:
    playwright, pending = targets()
    print("待探测：Playwright 旧误判 %d 个 + 待重新探测 %d 个 = %d"
          % (len(playwright), len(pending), len(playwright) + len(pending)))
    run(playwright, "playwright")
    run(pending, "pending")

    print("\n=== 汇总：重新探测通过的源 ===", flush=True)
    ok = []
    for label in ("playwright", "pending"):
        path = ROOT / "data" / "probes" / ("probe-%s.json" % label)
        if not path.exists():
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for row in report.get("results") or []:
            if int(row.get("records") or 0) > 0:
                ok.append((str(row.get("source_id")), int(row["records"])))
    for sid, n in sorted(ok, key=lambda x: -x[1]):
        print("   %-22s %d 条" % (sid, n), flush=True)
    print("\n共 %d 个源探测到内容。它们仍是 disabled —— 启用与否由你决定。"
          % len(ok))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())