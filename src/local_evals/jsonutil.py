"""Strict, deterministic JSON serialization shared by local evidence writers."""

from __future__ import annotations

import json
from typing import Any


class StrictJSONError(ValueError):
    """A value cannot be represented as finite JSON."""


def canonical_json_text(value: Any, *, ensure_ascii: bool = True) -> str:
    """Serialize a finite JSON value with deterministic key and separator rules."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=ensure_ascii,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise StrictJSONError("value must contain finite JSON-compatible data") from exc
