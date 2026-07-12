"""Classifier package exports."""

from __future__ import annotations

from media_insights.classify.classifier import (
    LABELS,
    Classification,
    apply_parsed_signals,
    classify,
)

__all__ = ["LABELS", "Classification", "apply_parsed_signals", "classify"]
