"""Matching package exports."""

from __future__ import annotations

from media_insights.matching.matcher import MatchResult, match_observation
from media_insights.matching.parser import ParsedTitle
from media_insights.matching.parser import parse as parse_title
from media_insights.matching.providers import (
    Provider,
    ProviderSignals,
    enabled_providers,
    lookup_all,
)

__all__ = [
    "MatchResult",
    "ParsedTitle",
    "Provider",
    "ProviderSignals",
    "enabled_providers",
    "lookup_all",
    "match_observation",
    "parse_title",
]
