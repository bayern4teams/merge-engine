"""Saved run profiles — Phase O.

A profile is everything a run needs *except* the data and the secrets: which
origins, which destination, which source tables hold each entity, how to match
records within an entity, how to resolve conflicting field values, and how the
unified fields map onto destination columns.

Why this exists, measured rather than assumed. Two full runs of identical data
produced identical matching (28,170 candidate pairs, 2,482 clusters) but
*different destination schemas* — 15 columns versus 13 — because the LLM's
field mapping varied on whether ``customer_id`` folds into ``id``. Matching is
deterministic; the metadata decisions around it are not. Re-running a merge is
therefore not a reproducible operation until those decisions are pinned. A
profile pins them.

Secrets are deliberately NOT stored. ``connection.password`` is stripped on
save and re-supplied at run time by the caller, so a profile file can be
copied, diffed, or committed without leaking a database password. Profiles are
matched back to live credentials on a coordinate key (type/host/port/database/
user) rather than by index, so reordering origins cannot silently point a
profile at the wrong server.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("merge-engine.profiles")

PROFILE_DIR = Path("config") / "profiles"

# Stages a profile can cache a fingerprint + artefact for (Phase A4), each
# mapped to the MergeState key(s) it bundles. A stage is usually more than
# one node output that only makes sense reused together (e.g. parse_request's
# entity_tables ships with the target_entities list derived from its own
# keys, and the merge_scope the same LLM call decided) -- `capture_from_state`
# below packages exactly these keys into one artefact, and each caching node
# unpacks the same bundle back into its own return dict on a cache hit.
CACHEABLE_STAGES: dict[str, tuple[str, ...]] = {
    "entity_tables": (  # parse_request's deterministic groups + LLM names
        "entity_tables",
        "target_entities",
        "explicit_target_entities",
        "merge_scope",
        "table_selection_notes",
    ),
    "dependency_tree": ("dependency_tree", "column_dependencies"),  # analyze_dependencies
    "join_graph": ("join_graph",),  # plan_scope's build_join_graph result
    "field_mappings": ("field_mappings",),  # map_fields
}

# Anything that isn't a safe filename character becomes "-", so a profile name
# can never escape PROFILE_DIR or collide with a path separator.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")

_SECRET_KEYS = ("password", "passwd", "secret", "api_key", "token")

# The parts of a run that a profile pins. Anything not listed here is
# recomputed every run on purpose — schemas and row data must stay live.
PINNED_KEYS = (
    "entity_tables",  # O.1 — which source tables hold each entity
    "matching_rules",  # O.2 — blocking keys / unique ids / strictness
    "conflict_policy",  # O.3 — per-field survivorship
    "field_mappings",  # O.4 — unified field -> destination column
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def safe_name(name: str) -> str:
    """Filesystem-safe profile id. Raises on a name that reduces to nothing."""
    cleaned = _SAFE_NAME.sub("-", (name or "").strip()).strip("-.")
    if not cleaned:
        raise ValueError(f"profile name {name!r} has no usable characters")
    return cleaned[:80]


def strip_secrets(value: Any) -> Any:
    """Recursively blank out password-ish values.

    Returns a copy — the caller's live config keeps its real credentials.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k.lower() in _SECRET_KEYS and v:
                out[k] = ""
            else:
                out[k] = strip_secrets(v)
        return out
    if isinstance(value, list):
        return [strip_secrets(v) for v in value]
    return value


def connection_key(origin: dict[str, Any]) -> str:
    """Coordinates that identify a server, ignoring credentials and order.

    Used to re-attach a live password to a saved (password-free) origin.
    """
    conn = origin.get("connection") or {}
    return (
        "|".join(str(conn.get(k, "")).strip().lower() for k in ("host", "port", "database", "user"))
        + "|"
        + str(origin.get("type", "")).strip().lower()
    )


def profile_path(name: str) -> Path:
    return PROFILE_DIR / f"{safe_name(name)}.json"


def build_profile(
    name: str,
    *,
    user_request: str,
    origins: list[dict[str, Any]],
    destination: dict[str, Any],
    state: dict[str, Any] | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Capture a finished (or configured) run as a re-runnable profile.

    ``state`` is the graph state to harvest the pinned decisions from. A
    profile built without one is still valid — it pins the connections and the
    request, and the first run fills in the rest.
    """
    state = state or {}
    profile = {
        "name": name,
        "id": safe_name(name),
        "description": description,
        "created_at": _now(),
        "updated_at": _now(),
        "user_request": user_request,
        "origins": strip_secrets(origins or []),
        "destination": strip_secrets(destination or {}),
        "fingerprints": {},
        "artefacts": {},
    }
    for key in PINNED_KEYS:
        profile[key] = state.get(key) or {}
    if state.get("schemas"):
        # Phase A4: the identity a repeat run auto-matches this profile by.
        # Computed here (and refreshed in capture_from_state) rather than
        # left for the caller, so it's never accidentally stale relative to
        # the origins/destination this same profile pins.
        profile["overall_fingerprint"] = overall_fingerprint(
            origins or [], state["schemas"], destination or {}
        )
    return profile


def save_profile(profile: dict[str, Any]) -> Path:
    """Write a profile to disk, preserving its original created_at."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    path = profile_path(profile.get("name") or profile.get("id") or "")

    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            profile["created_at"] = existing.get("created_at", profile.get("created_at"))
        except (OSError, json.JSONDecodeError):
            logger.warning("could not read existing profile %s; overwriting", path)

    profile["updated_at"] = _now()
    # Never trust the caller to have stripped credentials.
    for field in ("origins", "destination"):
        profile[field] = strip_secrets(profile.get(field))

    path.write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")
    logger.info("saved run profile %s -> %s", profile.get("name"), path)

    fp = profile.get("overall_fingerprint")
    if fp:
        register_in_index(fp, profile.get("name") or profile.get("id") or "")

    return path


def load_profile(name: str) -> dict[str, Any] | None:
    path = profile_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("profile %s is unreadable: %s", path, exc)
        return None


def list_profiles() -> list[dict[str, Any]]:
    """Summaries of every saved profile, newest first.

    A corrupt file is skipped with a warning rather than breaking the list —
    one bad profile must not hide the others.
    """
    if not PROFILE_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in PROFILE_DIR.glob("*.json"):
        if path == _index_path():
            # The fingerprint->profile-name lookup file, not a user profile.
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("skipping unreadable profile %s", path)
            continue
        out.append(
            {
                "name": data.get("name", path.stem),
                "id": data.get("id", path.stem),
                "description": data.get("description", ""),
                "user_request": data.get("user_request", ""),
                "origin_count": len(data.get("origins") or []),
                "entities": sorted((data.get("entity_tables") or {}).keys()),
                "pins": [k for k in PINNED_KEYS if data.get(k)],
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
            }
        )
    out.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return out


def delete_profile(name: str) -> bool:
    path = profile_path(name)
    if not path.exists():
        return False
    path.unlink()
    logger.info("deleted run profile %s", name)
    return True


def restore_credentials(
    saved: list[dict[str, Any]],
    live: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Re-attach live passwords to password-free saved origins.

    Matching is by connection coordinates, not by position, so reordering the
    live origins cannot point a saved origin at a different server. Returns
    the restored origins plus the coordinate keys that found no credentials —
    the caller must treat a non-empty list as "cannot run yet", never as a
    warning to log and continue past.
    """
    by_key = {connection_key(o): o for o in (live or [])}
    restored: list[dict[str, Any]] = []
    missing: list[str] = []

    for origin in saved or []:
        merged = json.loads(json.dumps(origin, default=str))
        conn = merged.setdefault("connection", {})
        match = by_key.get(connection_key(origin))
        if match:
            live_conn = match.get("connection") or {}
            for key in _SECRET_KEYS:
                if live_conn.get(key):
                    conn[key] = live_conn[key]
        # MongoDB and local trust-auth setups legitimately have no password;
        # only report a gap when one was expected.
        if not conn.get("password") and origin.get("_requires_password", True) and match is None:
            missing.append(connection_key(origin))
        restored.append(merged)

    return restored, missing


def apply_profile(state: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Seed graph state with a profile's pinned decisions.

    Only fills keys the profile actually pins and the state has not already
    set, so a caller can still override any single decision for one run
    without editing the profile.
    """
    if not profile:
        return state
    for key in PINNED_KEYS:
        value = profile.get(key)
        if value and not state.get(key):
            state[key] = value
    state["profile_name"] = profile.get("name", "")
    # Phase A4: fingerprints/artefacts ride along separately from
    # PINNED_KEYS above -- a node consults state["_profile_cache"] via
    # stage_cache_get(...) to decide whether ITS OWN cached artefact still
    # matches the current run's input shape, rather than trusting a pin
    # blindly the way PINNED_KEYS is applied above.
    state["_profile_cache"] = {
        "fingerprints": dict(profile.get("fingerprints") or {}),
        "artefacts": dict(profile.get("artefacts") or {}),
    }
    return state


def capture_from_state(profile: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Update a profile with the decisions a completed run actually made.

    This is what turns the first (LLM-driven) run into a pinned, repeatable
    one: whatever the LLM chose is written down so the next run reuses it
    instead of choosing again.
    """
    for key in PINNED_KEYS:
        value = state.get(key)
        if value:
            profile[key] = value
    if state.get("schemas"):
        profile["overall_fingerprint"] = overall_fingerprint(
            state.get("origins") or [], state["schemas"], state.get("destination") or {}
        )
    # Cacheable per-stage artefacts (Phase A4) -- distinct from PINNED_KEYS
    # above: these are keyed by the exact input fingerprint they were
    # computed from, not just "the last value seen", so a later run against
    # a DIFFERENT source shape correctly misses the cache instead of
    # reusing a stale answer. Each stage bundles every state key
    # CACHEABLE_STAGES lists for it (e.g. entity_tables ships with the
    # target_entities/merge_scope the same LLM call decided), so a cache hit
    # can restore the node's FULL return dict, not just one field of it.
    for stage, keys in CACHEABLE_STAGES.items():
        fp = state.get(f"_{stage}_fingerprint")
        if not fp:
            continue
        bundle = {k: state[k] for k in keys if state.get(k)}
        if bundle:
            stage_cache_set(profile, stage, fp, bundle)
    return profile


# ─────────────────────────────────────────────────────────────────────────
# Stage-level fingerprint caching (Phase A4).
#
# PINNED_KEYS/apply_profile above are the *decisions* a profile pins — they
# say nothing about whether those decisions still apply to the run in front
# of you. This section adds that check: every cacheable stage's output is
# stored alongside the exact fingerprint of the inputs it was computed
# from, so a lookup can tell "reuse this" from "the source shape moved,
# recompute" without re-running the stage to find out.
# ─────────────────────────────────────────────────────────────────────────


def overall_fingerprint(
    origins: list[dict[str, Any]], schemas: dict[str, Any], destination: dict[str, Any]
) -> str:
    """The identity a profile is looked up by: this (origins, destination)
    coordinate + shape combination, nothing else. Two runs against the same
    databases with the same discovered schema resolve to the same profile
    automatically — no name to remember or select from a dropdown.
    """
    from src.orchestrator.fingerprint import destination_fingerprint, origins_fingerprint

    return origins_fingerprint(origins, schemas) + ":" + destination_fingerprint(destination)


def _index_path() -> Path:
    # Computed fresh on every call (never a frozen module-level constant)
    # so tests that monkeypatch PROFILE_DIR to an isolated tmp_path -- the
    # existing convention this whole module already follows via
    # profile_path() -- correctly redirect the index too, not just
    # individual profile files.
    return PROFILE_DIR / "_index.json"


def _load_index() -> dict[str, str]:
    path = _index_path()
    if not path.exists():
        return {}
    try:
        loaded: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        return loaded
    except (OSError, json.JSONDecodeError):
        logger.warning("profile fingerprint index is unreadable; starting fresh")
        return {}


def _save_index(index: dict[str, str]) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _index_path().write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")


def register_in_index(fingerprint: str, profile_name: str) -> None:
    """Record that ``fingerprint`` resolves to ``profile_name``.

    Last-write-wins by design: if two profiles were ever saved for the same
    (origins, destination) shape, the most recently saved one is what
    auto-detection offers — a stale duplicate is not preferred over a fresh
    one just because it was created first.
    """
    index = _load_index()
    index[fingerprint] = profile_name
    _save_index(index)


def find_profile_by_fingerprint(fingerprint: str) -> dict[str, Any] | None:
    """The saved profile matching this exact (origins, destination) shape,
    or None on no match — including when the indexed profile's own file has
    since been deleted, which is treated as "no match" rather than an
    error."""
    name = _load_index().get(fingerprint)
    if not name:
        return None
    return load_profile(name)


def stage_cache_get(profile: dict[str, Any] | None, stage: str, current_fingerprint: str) -> Any:
    """The cached artefact for ``stage``, or None if there isn't one or it
    was computed from different inputs than ``current_fingerprint``
    describes.

    Returning None on ANY mismatch (missing profile, missing stage,
    fingerprint drift) is deliberate: every caller's fallback is "compute it
    normally", so the failure mode of this function being wrong is always
    "did more work than strictly necessary", never "used a stale answer".
    """
    if not profile:
        return None
    fingerprints = profile.get("fingerprints") or {}
    if fingerprints.get(stage) != current_fingerprint:
        return None
    return (profile.get("artefacts") or {}).get(stage)


def stage_cache_set(
    profile: dict[str, Any], stage: str, fingerprint: str, artefact: Any
) -> dict[str, Any]:
    """Record ``stage``'s output and the fingerprint it was computed from,
    in place, returning the same profile dict for chaining."""
    profile.setdefault("fingerprints", {})[stage] = fingerprint
    profile.setdefault("artefacts", {})[stage] = artefact
    return profile
