from __future__ import annotations

import re
from typing import Any

_REDACTIONS = (
    (re.compile(r"(?i)(银行账号|收款账号|账号)\s*[：:]\s*[0-9][0-9\s-]{7,}[0-9]"), r"\1：[已脱敏]"),
    (re.compile(r"(?i)(联系电话|咨询电话|联系方式|电话)\s*[：:\s]*[0-9][0-9\s()+-]{6,}[0-9]"), r"\1：[已脱敏]"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号已脱敏]"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证号已脱敏]"),
)


def redact_sensitive(value: Any) -> str:
    text = str(value or "")
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text
