"""Lenient query-param coercion.

Empty values reach the routes from HTML forms all the time -- an unselected
<select> submits `library=`, and the /titles pager carries `unmatched=`
through to the next page. Every one of these must mean "filter not set",
never a 422.
"""

from __future__ import annotations

import pytest

from media_insights.query_params import parse_optional_bool, parse_optional_id


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_id_means_no_filter(value) -> None:
    assert parse_optional_id(value) is None


def test_id_parses_real_values() -> None:
    assert parse_optional_id("1") == 1
    assert parse_optional_id("42") == 42


def test_id_still_rejects_garbage() -> None:
    # Leniency is only about *empty*; a genuinely malformed id is still an error.
    with pytest.raises(ValueError):
        parse_optional_id("abc")


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_bool_means_no_filter(value) -> None:
    assert parse_optional_bool(value) is False


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "on"])
def test_truthy_tokens(value) -> None:
    assert parse_optional_bool(value) is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "anything-else"])
def test_falsey_tokens(value) -> None:
    assert parse_optional_bool(value) is False
