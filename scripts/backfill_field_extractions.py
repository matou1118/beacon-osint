"""回填 field_extractions 表。

已有 527 条 announcements 但 field_extractions=0。
这个脚本从 announcements 表读出已抽取的字段，写入 field_extractions，
让对比/印证引擎有结构化数据可用。

用法：
    python scripts/backfill_field_extractions.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.db import Database


# announcements 表字段 → topic field_key 的映射
FIELD_MAP = {
    "amount": "subsidy_amount",
    "target_group": "target_group",
    "deadline": "deadline",
    "institution_name": "institution",
    "region_province": "region",
    "key_points": "key_measures",
}


def backfill(db_path: str = "data/beacon.db", topic_id: int = 1) -> dict[str, int]:
    db = Database(db_path)
    result = {"inserted": 0, "updated": 0, "skipped": 0}
    with db.session() as session:
        from sqlalchemy import select
        from storage.models import Announcement, FieldExtraction

        announcements = list(session.scalars(select(Announcement)).all())
        print(f"扫描 {len(announcements)} 条 announcements...")

        for ann in announcements:
            for ann_field, field_key in FIELD_MAP.items():
                value = getattr(ann, ann_field, None)
                if value is None or value == "" or value == []:
                    continue

                # key_points 是 list，转 JSON 存
                if isinstance(value, list):
                    value_text = json.dumps(value, ensure_ascii=False)
                else:
                    value_text = str(value)

                evidence = str(ann.content or "")[:200]

                existing = session.scalar(
                    select(FieldExtraction).where(
                        FieldExtraction.announcement_id == ann.id,
                        FieldExtraction.topic_id == topic_id,
                        FieldExtraction.field_key == field_key,
                    )
                )
                if existing is None:
                    session.add(FieldExtraction(
                        announcement_id=ann.id,
                        topic_id=topic_id,
                        field_key=field_key,
                        value=value_text,
                        confidence=80,
                        evidence_text=evidence,
                        method="rule",
                        status="auto",
                    ))
                    result["inserted"] += 1
                else:
                    existing.value = value_text
                    existing.evidence_text = evidence
                    result["updated"] += 1
        session.commit()

    db.close()
    return result


if __name__ == "__main__":
    res = backfill()
    print(f"回填完成: {res}")
