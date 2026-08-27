"""Runtime Splink version gate for every B.4 phase (Branch_Plan.md B.4).

The plan's opening mandate for the whole section:

    Every phase verifies its API against the **installed** version at runtime
    (``splink.__version__``, ``installed_minor_version``) and refuses to run
    against an unexpected major.

This module is that gate, centralised so every phase imports it once rather
than each re-deriving the check. It is deliberately the only place in the B.4
subsystem that reads ``splink.__version__``; everything else goes through
:func:`require_splink`.

Two distinct failure modes, kept apart on purpose:

* **Splink not installed / not importable** → :data:`splink_available` is
  ``False``. Callers (the workbench, the per-entity ``engine`` selector) must
  *degrade* to the builtin engine, exactly the way
  ``splink_matcher.resolve_with_splink`` already does. A missing optional
  dependency is not an error.
* **Wrong major version** → :func:`check_splink` raises
  :class:`SplinkVersionError`. The ADR (0004) pins behaviour to 4.0.x
  specifically, not "whatever Splink happens to be installed", because every
  API call B.4 makes was verified against the installed 4.0.16 and several of
  them moved location between 3.x and 4.x (the labels table schema, the
  blocking-rule library, ``compute_graph_metrics``). Running the workbench
  against a 5.x with an unverified API surface would silently produce wrong
  numbers, which is exactly what the gate exists to prevent.

The minor/patch is *reported*, not hard-failed: a future 4.1 may add APIs we
don't use, and warning-on-minor would make every minor bump a code change.
Only the major is a contract.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("merge-engine.tools.splink_compat")

# Pinned in ADR 0004 and verified by introspecting the installed package.
# See docs/decisions/0004-adopt-splink-matcher.md and the B.4 API-verification
# note at the top of TODO.md.
REQUIRED_MAJOR = 4
VERIFIED_VERSION = "4.0.16"

try:  # pragma: no cover - import guard mirrors splink_matcher's own guard
    import splink

    _IMPORTED_VERSION = getattr(splink, "__version__", "unknown")
    _SPLINK_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # ImportError or a broken install
    splink = None  # type: ignore[assignment]
    _IMPORTED_VERSION = "unknown"
    _SPLINK_IMPORT_ERROR = exc


class SplinkVersionError(RuntimeError):
    """Raised when the installed Splink major version is not the one B.4 was
    verified against. Distinct from "not installed" (which callers handle by
    degrading to the builtin engine, not by raising)."""

    def __init__(self, installed: str, required_major: int = REQUIRED_MAJOR) -> None:
        super().__init__(
            f"Splink major version mismatch: installed {installed!r}, "
            f"required major {required_major}. B.4 phases are verified against "
            f"{VERIFIED_VERSION} and refuse to run on an unverified major."
        )
        self.installed = installed
        self.required_major = required_major


def splink_available() -> bool:
    """True when Splink imported cleanly. Used by the UI/engine selector to
    decide whether to offer the Splink workbench at all."""
    return splink is not None


def imported_version() -> str:
    """The ``splink.__version__`` string, or ``"unknown"`` when not installed."""
    return _IMPORTED_VERSION


def installed_minor_version() -> tuple[int, int] | None:
    """``(major, minor)`` parsed from ``splink.__version__``, or ``None`` when
    unavailable/unparseable. Named to match the plan's own vocabulary so the
    gate reads exactly like the spec that demanded it."""
    if splink is None:
        return None
    parts = _IMPORTED_VERSION.split(".")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return None


def check_splink() -> None:
    """Raise :class:`SplinkVersionError` if the installed major is not the one
    B.4 was verified against. Does *not* raise when Splink is merely missing —
    use :func:`splink_available` for that; a missing optional dependency is a
    degradation path, not an error. Safe to call repeatedly (it is cheap and
    idempotent)."""
    if splink is None:
        # Absence is not a version mismatch; let the caller decide to degrade.
        return
    minor = installed_minor_version()
    if minor is None:
        raise SplinkVersionError(_IMPORTED_VERSION)
    if minor[0] != REQUIRED_MAJOR:
        raise SplinkVersionError(_IMPORTED_VERSION)


def require_splink() -> dict[str, Any]:
    """The gate every phase calls at its entry. Returns a small info dict so a
    phase can record the exact version it ran against (for the S-N model
    version record, which must store the Splink version per the plan).

    Raises :class:`SplinkVersionError` on a major mismatch. Returns
    ``{"available": False}`` when Splink is missing rather than raising, so a
    workbench endpoint can answer "Splink not installed — builtin only"
    cleanly instead of 500-ing.
    """
    check_splink()
    if splink is None:
        return {"available": False, "version": "unknown", "required_major": REQUIRED_MAJOR}
    return {
        "available": True,
        "version": _IMPORTED_VERSION,
        "required_major": REQUIRED_MAJOR,
        "verified_version": VERIFIED_VERSION,
    }
