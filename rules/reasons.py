from __future__ import annotations

import re
from typing import Any, Mapping


def lookup(context: Mapping[str, Any], path: str) -> Any:
    if path.startswith("announcement."):
        path = path[len("announcement.") :]
        value: Any = context.get("announcement")
    elif path.startswith("user."):
        path = path[len("user.") :]
        value = context.get("user")
    else:
        value = context
    for part in path.split("."):
        if isinstance(value, Mapping):
            value = value.get(part)
        else:
            return None
    if value is None and not path.startswith(("announcement.", "user.")):
        for candidate in ("announcement", "user"):
            nested = context.get(candidate)
            if isinstance(nested, Mapping):
                nested_value: Any = nested
                for part in path.split("."):
                    if not isinstance(nested_value, Mapping):
                        nested_value = None
                        break
                    nested_value = nested_value.get(part)
                if nested_value is not None:
                    return nested_value
    return value


def render_reason(template: str, context: Mapping[str, Any], extra: Mapping[str, Any] | None = None) -> str:
    values = dict(context)
    values.update(extra or {})

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = lookup(values, key)
        if value is None:
            value = values.get(key, "")
        if isinstance(value, (list, tuple, set)):
            return "、".join(str(item) for item in value)
        return str(value)

    return re.sub(r"\{([^{}]+)\}", replace, template).strip()


def normalize_reasons(reasons: list[str]) -> list[str]:
    result: list[str] = []
    for reason in reasons:
        value = str(reason or "").strip()
        if value and value not in result:
            result.append(value)
    return result
