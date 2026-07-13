"""Lenient query-parameter coercion shared by the REST API and the Web UI.

An unset filter reaches us as an *empty* value, not an absent one: HTML
<select> "All ___" options submit `library=`, and the /titles pager carries
`unmatched=` through to the next page. FastAPI's `int | None` / `bool`
binding rejects `""` outright with a 422, so those params are declared as
plain strings and coerced here instead -- an empty value means "filter not
set", never a validation error.
"""

from __future__ import annotations

_TRUE_VALUES = {"true", "1", "yes", "on"}


def parse_optional_id(value: str | None) -> int | None:
    """`""` / None -> None (no filter); otherwise the int, raising on garbage."""
    if not value or not value.strip():
        return None
    return int(value)


def parse_optional_bool(value: str | None) -> bool:
    """`""` / None -> False (no filter); truthy tokens -> True."""
    if not value or not value.strip():
        return False
    return value.strip().lower() in _TRUE_VALUES
