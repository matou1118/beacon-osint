"""源的历史表现：谁在磨洋工、谁在连续失败。

用来给排空采集做降频决策。数据来源都是现成的，不新建表：
  - 连续失败次数：source_sync_state.consecutive_failures
  - 秒/条：data/rounds.jsonl 里逐轮的 source_stats

为什么需要
  实测（scripts/audit_efficiency.py，近 3 个通宵日志）：
    - 26% 的请求是 SKIP，1497 次里 392 次。排空模式每轮都去试那些连败的源，
      每轮都失败。zj_fgw / zj_kjt / zj_ny / js_fgw / js_ny / js_rst
      在 source_sync_state 里 consecutive_failures 已经 21-22。
    - 单源效率差 264 倍：bj_gov 3.1 秒/条（720 条），
      sc_qh_gov 818 秒/条（总共 3 条）、sc_shanxi_gov 113 秒/条。
      这类源每轮都打，每轮几十秒换零星几条。

  但**不是所有源都该砍**：耗时前 20 名占 46% 时间、产出 43%，砍掉只提升
  1.1x——耗时和产出是成比例的，没有明显死重。所以只降频两类：**在连续失败**
  的，和**秒/条差得离谱**的，其余照旧。

注意：首次出现这个模块时 rounds.jsonl 里还没有 source_stats（是同一轮加的），
所以读不到历史时一律返回"没有数据"，由调用方按不过滤处理。宁可不过滤，
也不能因为历史缺失就把源全跳了。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUNDS_LOG = ROOT / "data" / "rounds.jsonl"


@dataclass
class SourcePerf:
    sid: str
    seconds_per_record: float
    records: int
    rounds_seen: int


def load_efficiency(max_rounds: int = 40) -> dict[str, SourcePerf]:
    """从 rounds.jsonl 算每个源的秒/条。没有数据就返回空 dict。"""
    if not ROUNDS_LOG.exists():
        return {}
    rows: list[dict] = []
    try:
        with ROUNDS_LOG.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return {}
    rows = rows[-max_rounds:]

    acc: dict[str, list[float]] = {}
    for row in rows:
        for st in row.get("source_stats") or []:
            sid = st.get("id")
            if not sid:
                continue
            acc.setdefault(sid, [0.0, 0, 0])   # seconds, records, rounds
            acc[sid][0] += float(st.get("seconds") or 0.0)
            acc[sid][1] += int(st.get("records") or 0)
            acc[sid][2] += 1

    out: dict[str, SourcePerf] = {}
    for sid, (secs, recs, rounds) in acc.items():
        if recs <= 0 or rounds <= 0:
            continue
        out[sid] = SourcePerf(sid, secs / recs, recs, rounds)
    return out


def failing_sources(db, threshold: int) -> dict[str, int]:
    """连续失败次数 >= threshold 的源 -> 失败次数。

    走 db.session() + session.get(SourceSyncState, sid)，跟 source_due() 同一个
    入口，不另写 SQL——这是项目里读这张表的既有做法。
    """
    from storage.models import SourceSyncState

    out: dict[str, int] = {}
    try:
        with db.session() as session:
            for state in session.query(SourceSyncState).all():
                n = int(getattr(state, "consecutive_failures", 0) or 0)
                if n >= threshold:
                    out[str(state.source_id)] = n
    except Exception:  # noqa: BLE001
        return {}
    return out
