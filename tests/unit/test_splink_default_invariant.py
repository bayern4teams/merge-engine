"""BP-B4 promotion guard — the default-engine invariant (fast; runs on every
``make test``).

ADR 0004 is "accepted, **gated**": the Splink engine is available (UI per-entity
``engine: splink``) but it may only become the *default* engine once the two
measured gate legs in ``tests/eval/test_matcher_recall.py`` pass:

1. over the 560 golden labelled pairs, Splink beats the builtin Fellegi-Sunter
   engine on BOTH precision AND recall;
2. the Airbus golden-parity score exceeds the 12.0% Boeing baseline.

Those legs are slow (they train a real EM model / need a live Airbus run) and are
therefore NOT in the default suite. This guard keeps the fast suite honest
without re-running them: **the default engine may be ``splink`` only if the last
measured gate outcome is a pass** (recorded to ``tests/eval/last_gate.json`` by
the slow legs). It PASSES in the current gated state (default = builtin) and
FAILS if someone promotes Splink to the default without a measured passing gate.

This is a *promotion* invariant, not a performance test: it does not measure
recall, it enforces that recall was measured before the default changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_GATE_RECORD = _ROOT / "tests" / "eval" / "last_gate.json"


def _default_engine() -> str:
    """The default engine from ``config.yaml`` (the promotion surface ADR 0004
    names). Returns ``"builtin"`` when no default engine is declared — matching
    the code path: ``resolve_entities`` falls back to builtin Fellegi-Sunter when
    an entity has no ``engine`` override.

    The config currently declares no matching section, so the default is builtin.
    If a future PR adds a default-engine declaration, this reader finds it in the
    conventional locations so the guard keeps working. (It mirrors
    ``tests/eval/test_matcher_recall._default_engine`` — the two must agree on
    what "the default engine" means, so a promotion can't slip past one by
    declaring it in a section the other doesn't read.)
    """
    import yaml

    cfg_path = _ROOT / "config" / "config.yaml"
    if not cfg_path.exists():
        return "builtin"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    for key in ("entity_resolution", "matching_rules", "matching", "engine"):
        section = cfg.get(key)
        if isinstance(section, dict):
            engine = section.get("default_engine") or section.get("engine")
            if isinstance(engine, str) and engine:
                return engine.lower()
            for _entity, rules in section.items():
                if isinstance(rules, dict) and rules.get("engine") == "splink":
                    return "splink"
    return "builtin"


def test_default_engine_invariant() -> None:
    """Splink may not be the default engine unless the BP-B4 gate has been
    measured and met."""
    default = _default_engine()

    if default == "builtin":
        # The safe side of the promotion decision. Even if the gate has never been
        # measured, builtin-default is the ADR 0004 default, so the invariant holds.
        return

    # Default is splink — the gate must have been measured and passed.
    if not _GATE_RECORD.exists():
        pytest.fail(
            "config.yaml sets the default engine to 'splink' but no measured gate outcome "
            f"({_GATE_RECORD}) exists. Run tests/eval/test_matcher_recall.py -m slow to "
            "measure the BP-B4 gate before promoting Splink to the default engine "
            "(ADR 0004: 'accepted, gated')."
        )

    record = json.loads(_GATE_RECORD.read_text(encoding="utf-8"))
    if not record.get("gate_passed"):
        pytest.fail(
            "config.yaml sets the default engine to 'splink' but the last measured gate "
            f"outcome was NOT a pass: {json.dumps(record, sort_keys=True)}. Per BP-B4 / "
            "ADR 0004, Splink may only be the default engine after (1) beating the builtin "
            "on BOTH precision and recall over the 560 golden pairs AND (2) exceeding the "
            "12.0% Boeing golden-parity baseline. Revert the default to 'builtin' or "
            "re-measure the gate until it passes."
        )
