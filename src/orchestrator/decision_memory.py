"""Cross-run memory of human gate decisions (report P9, Part 1 slice).

Decisions are persisted to one JSON file keyed by a stable hash of the question, so a
later run applies a matching decision without asking and reports how many it applied.
The question is stored next to the decision so Part 3 has a backfill source.

A missing, empty, truncated or otherwise unreadable file degrades to "ask again"
(a warning is logged); it never raises on read.

ponytail: a file has no concurrency control, no schema and no query surface (report P9
Ceiling): two live instances on one path are last-writer-wins and "every decision about
entity X" is a full scan. Deferred alternative: the indexed, revocable decision store in
``merge_engine`` (report sections 3.8 and 3.9). Trigger: Part 3, which backfills from
this file.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def question_key(question: Mapping[str, Any]) -> str:
    """Hex sha256 of the canonical JSON of ``question``; identical across processes.

    Never builtin ``hash()``: it is salted per process (PYTHONHASHSEED).
    """
    canonical = json.dumps(question, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DecisionMemory:
    """File-backed store of gate decisions. One instance == one run."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.applied_count = 0
        self._entries = self._load()

    def lookup(self, question: Mapping[str, Any]) -> Any | None:
        """The remembered decision, or ``None`` on a miss. A hit bumps ``applied_count``."""
        entry = self._entries.get(question_key(question))
        if entry is None:
            return None
        self.applied_count += 1
        return entry["decision"]

    def record(self, question: Mapping[str, Any], decision: Any) -> None:
        """Persist ``decision`` for ``question``; durable on return, last write wins."""
        entries = {
            **self._entries,
            question_key(question): {"question": question, "decision": decision},
        }
        self._write(entries)
        self._entries = entries

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, RecursionError) as exc:  # bad UTF-8/JSON, absurd nesting
            logger.warning("decision memory %s unreadable, asking again: %s", self.path, exc)
            return {}
        if not isinstance(raw, dict):
            logger.warning("decision memory %s is not an object, asking again", self.path)
            return {}
        return {k: v for k, v in raw.items() if isinstance(v, dict) and "decision" in v}

    def _write(self, entries: dict[str, dict[str, Any]]) -> None:
        text = json.dumps(entries, sort_keys=True, indent=2, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
