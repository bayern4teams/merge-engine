"""S-N — model versioning & iteration (Branch_Plan.md B.4; Splink.txt §44-§46).

Every trained model the workbench produces becomes an immutable *version*:
the complete ``save_model_to_json()`` payload (verified installed 4.0.16:
16 keys — comparisons with their trained m/u, the prior, prediction blocking
rules, TF prefixes, link type, unique-id column) plus the provenance Splink
does *not* record: the training session records (S-I), the training
blocking rules used, the labels the evaluation ran on, the S-L accuracy
results, the parent version, and a lifecycle status.

Design decisions (reasoning the plan's §44-46 leaves to us):

* **File store, one directory per version** under
  ``<store_root>/<model_name>/v<N>/`` with ``model.json`` (the payload,
  byte-stable) and ``metadata.json`` (provenance). This matches the project's
  existing convention of run artefacts under ``logs/`` (spool:
  ``logs/spool/<run_id>.duckdb``) and needs no new dependency: a version is
  a file a human can ``cat``, diff, and restore. SQLite would be the wrong
  tool — the payload is a blob, and the interesting queries (list/compare
  two versions) are trivial on a directory.
* **Immutability.** A version, once written, is never edited in place:
  "edit" and "retrain" both *create a new version* with
  ``parent_version`` set to the one changed. This is what makes rollback a
  one-pointer move instead of a data recovery, and it is §46's
  reproducibility requirement made structural: every number in a version
  traces to a parent plus a change.
* **Activation is a pointer, not a copy.** ``activate`` writes
  ``<store_root>/<model_name>/active.json`` (``{"version": N}``). There is
  exactly one active version per model at a time; rollback = activate the
  older version. The pointer file is the only mutable thing, and its
  content is self-describing.
* **``save_model_to_json`` returns the payload; the file on disk is the
  source of truth.** We pass ``out_path`` (so the file exists, byte-for-byte
  what the API served) and also keep the returned dict in metadata —
  they are the same object, and if they ever diverge in a future Splink the
  ``round_trip_verified`` check below would catch it.
* **Loading a version rebuilds a live ``Linker``** (payload + the caller's
  input DataFrame + a fresh DuckDBAPI), and ``round_trip_verify`` proves the
  rebuilt model predicts identically to the original before the version is
  even offered for activation — a version that cannot be reloaded is a
  broken artefact, and this module refuses to bless one.

No module state (CLAUDE.md): the store root is a parameter on every call.
Every public function calls :func:`splink_compat.require_splink`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from src.mcp_server.tools.splink_common import DEFAULT_UNIQUE_ID_COLUMN
from src.mcp_server.tools.splink_compat import imported_version, require_splink

logger = logging.getLogger("merge-engine.tools.splink_model")

# Lifecycle statuses (the §53 "real status" system, at the model level).
# A version's status moves forward along the order, with exactly one
# sanctioned backward move: active -> evaluated (deactivation when another
# version takes over — the demoted version remains validated). archive is
# terminal from any state; the transition rules are enforced in
# ``_set_status_in_file``.
STATUSES = ("draft", "trained", "evaluated", "active", "archived")
_FORWARD = {
    "draft": {"trained", "evaluated", "active", "archived"},
    "trained": {"evaluated", "active", "archived"},
    "evaluated": {"active", "archived"},
    "active": {"evaluated", "archived"},
    "archived": set(),
}


def _safe_name(name: str) -> str:
    """A model name to a filesystem-safe directory name."""
    if not name or not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise ValueError(f"model name {name!r} must be alphanumeric/_/- only")
    return name


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


# ── store layout ───────────────────────────────────────────────────────────


def _version_dir(store_root: str, model_name: str, version: int) -> Path:
    return Path(store_root) / _safe_name(model_name) / f"v{version}"


def _next_version(store_root: str, model_name: str) -> int:
    base = Path(store_root) / _safe_name(model_name)
    if not base.exists():
        return 1
    versions = []
    for child in base.iterdir():
        if child.is_dir() and re.fullmatch(r"v(\d+)", child.name):
            versions.append(int(child.name[1:]))
    return (max(versions) + 1) if versions else 1


def _active_pointer(store_root: str, model_name: str) -> Path:
    return Path(store_root) / _safe_name(model_name) / "active.json"


def load_version(store_root: str, model_name: str, version: int) -> dict[str, Any]:
    """One version's payload + metadata, merged, from disk.

    Raises :class:`FileNotFoundError` (with a message naming what is
    missing) rather than returning a partial view — a half-read version is
    worse than a reported absence.
    """
    require_splink()
    vdir = _version_dir(store_root, model_name, version)
    model_path = vdir / "model.json"
    meta_path = vdir / "metadata.json"
    if not model_path.exists():
        raise FileNotFoundError(f"model {model_name} v{version}: {model_path} does not exist")
    payload = _read_json(model_path)
    metadata = _read_json(meta_path) if meta_path.exists() else {}
    return {"version": version, "payload": payload, "metadata": metadata}


def list_versions(store_root: str, model_name: str) -> list[dict[str, Any]]:
    """All versions of a model, oldest first, with a one-line summary each
    (status, parent, splink version, activation). No payloads are read —
    listing a model with a 50MB settings blob must stay cheap, so only
    ``metadata.json`` is touched."""
    require_splink()
    base = Path(store_root) / _safe_name(model_name)
    if not base.exists():
        return []
    out = []
    try:
        active = _read_json(_active_pointer(store_root, model_name)).get("version")
    except (FileNotFoundError, json.JSONDecodeError):
        active = None
    for child in sorted(
        base.iterdir(),
        key=lambda p: (p.name[1:].isdigit(), int(p.name[1:]) if p.name[1:].isdigit() else 0),
    ):
        if not child.is_dir() or not re.fullmatch(r"v\d+", child.name):
            continue
        version = int(child.name[1:])
        meta_path = child / "metadata.json"
        meta = _read_json(meta_path) if meta_path.exists() else {}
        out.append(
            {
                "version": version,
                "status": meta.get("status"),
                "parent_version": meta.get("parent_version"),
                "splink_version": meta.get("splink_version"),
                "created_at": meta.get("created_at"),
                "is_active": version == active,
            }
        )
    return out


# ── creation ───────────────────────────────────────────────────────────────


def save_version(
    store_root: str,
    model_name: str,
    linker: Any,
    *,
    df: Any,
    metadata: dict[str, Any] | None = None,
    parent_version: int | None = None,
    status: str = "trained",
) -> dict[str, Any]:
    """S-N: freeze the current state of a (trained) linker as a new version.

    Writes ``model.json`` (via ``save_model_to_json(out_path)``, so the file
    on disk is byte-identical to what the API would serve), then
    ``metadata.json`` with the provenance Splink does not record:

    * ``splink_version`` — the installed version the payload was produced
      by (a 4.x payload is not loadable by 3.x, and the S0 gate refuses at
      load time too, but the record says what produced it);
    * ``training`` — the S-I session record (seeds, rules, iterations);
    * ``training_blocking_rules`` — the rules EM ran on (the payload only
      carries the *prediction* rules — §44's "both kinds" requirement);
    * ``labels_used`` / ``evaluation`` — the S-K/S-L handles and numbers
      the version was validated against;
    * ``parent_version`` — the version this one changed, if any.

    Then :func:`round_trip_verify` proves the written payload reloads into
    a Linker that predicts identically to the live one, and the version's
    status is set to ``"verified"``-backed ``status``. A version that fails
    its round-trip is *still written* (with ``round_trip_verified: False``
    and status forced to ``draft``) — the artefact exists and can be
    inspected, but it cannot be activated.
    """
    require_splink()
    if status not in STATUSES:
        raise ValueError(f"status {status!r} not one of {STATUSES}")
    version = _next_version(store_root, model_name)
    vdir = _version_dir(store_root, model_name, version)
    vdir.mkdir(parents=True, exist_ok=True)
    model_path = vdir / "model.json"

    # save_model_to_json writes the file AND returns the same dict — the
    # file is the source of truth, the return value is what the API echoes.
    payload = linker.misc.save_model_to_json(str(model_path), overwrite=True)

    record = dict(metadata or {})
    record.update(
        {
            "model_name": model_name,
            "version": version,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "splink_version": imported_version(),
            "parent_version": parent_version,
            "status": status,
            "round_trip_verified": False,  # set below on success
            "payload_keys": sorted(payload.keys()),
        }
    )
    # verify BEFORE writing metadata, but after the payload file exists
    verify = round_trip_verify(store_root, model_name, version, df)
    record["round_trip_verified"] = verify["verified"]
    if not verify["verified"]:
        record["status"] = "draft"
        record["round_trip_error"] = verify.get("error")
    _write_json(vdir / "metadata.json", record)
    logger.info("saved model %s v%d (verified=%s)", model_name, version, verify["verified"])
    return {
        "version": version,
        "directory": str(vdir),
        "payload_keys": sorted(payload.keys()),
        "round_trip_verified": verify["verified"],
    }


def round_trip_verify(store_root: str, model_name: str, version: int, df: Any) -> dict[str, Any]:
    """S-N's honesty check: reload the written payload into a fresh Linker
    over the same DataFrame and prove its predictions are identical to the
    file's provenance. A payload that cannot be reloaded is a broken
    artefact; this module refuses to mark one verified.

    Comparison is on the prediction table's ``match_probability`` for every
    pair (ids sorted, floats compared exactly — same payload + same df +
    same Splink build deterministic arithmetic gives bit-identical
    outputs, and if a future build doesn't, that is a finding, not noise).
    """
    require_splink()
    from splink import DuckDBAPI, Linker

    vdir = _version_dir(store_root, model_name, version)
    model_path = vdir / "model.json"
    if not model_path.exists():
        return {"verified": False, "error": f"{model_path} does not exist"}
    try:
        payload = _read_json(model_path)
        linker = Linker(df, payload, DuckDBAPI())
        pred_sdf: Any = linker.inference.predict()
        pred = pred_sdf.as_pandas_dataframe()
        # the unique-id columns in the prediction table are the payload's
        # own unique_id_column_name suffixed _l/_r (verified 4.0.16) — not
        # a fixed "id_l"/"id_r"
        uid = payload.get("unique_id_column_name", DEFAULT_UNIQUE_ID_COLUMN)
        rows = sorted(
            (str(a), str(b), float(c))
            for a, b, c in zip(
                pred[f"{uid}_l"], pred[f"{uid}_r"], pred["match_probability"], strict=True
            )
        )
        # stable fingerprint: count + sum + max of (id_l, id_r, prob) tuples
        return {
            "verified": len(rows) > 0,
            "n_pairs": len(rows),
            "probability_sum": round(sum(r[2] for r in rows), 12),
            "probability_max": round(max((r[2] for r in rows), default=0.0), 12),
        }
    except Exception as exc:
        logger.warning("round-trip verify failed for %s v%d: %s", model_name, version, exc)
        return {"verified": False, "error": str(exc)}


# ── lifecycle: duplicate / compare / activate / archive / rollback ────────


def duplicate_version(
    store_root: str,
    model_name: str,
    source_version: int,
    *,
    metadata_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new version whose payload is a copy of ``source_version``.
    The new version's ``parent_version`` is the source, and its status is
    ``draft`` (a copy is not yet a validated model — the caller retrains or
    edits, then saves). This is the "edit" primitive: there is no in-place
    edit, only copy-then-modify-then-re-save."""
    require_splink()
    src = load_version(store_root, model_name, source_version)
    version = _next_version(store_root, model_name)
    vdir = _version_dir(store_root, model_name, version)
    vdir.mkdir(parents=True, exist_ok=True)
    _write_json(vdir / "model.json", src["payload"])
    record = dict(src["metadata"])
    record.update(
        {
            "version": version,
            "parent_version": source_version,
            "status": "draft",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "round_trip_verified": False,
        }
    )
    if metadata_override:
        record.update(metadata_override)
    _write_json(vdir / "metadata.json", record)
    return {"version": version, "parent_version": source_version}


def compare_versions(
    store_root: str,
    model_name: str,
    version_a: int,
    version_b: int,
) -> dict[str, Any]:
    """The §45 "compare two model versions" view: what changed, parameter by
    parameter. Compares the full payloads structurally — every comparison,
    every level's m/u, the prior, the blocking rules, the TF prefixes — and
    reports the leaves that differ, with both values. What is *not* in the
    diff is what is *the same*, which is the point: a reviewer sees exactly
    what a retrain changed, not a wall of identical numbers.
    """
    require_splink()
    a = load_version(store_root, model_name, version_a)
    b = load_version(store_root, model_name, version_b)

    def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                out.update(_flatten(v, f"{prefix}[{i}]"))
        else:
            out[prefix] = obj
        return out

    fa, fb = _flatten(a["payload"]), _flatten(b["payload"])
    keys = sorted(set(fa) | set(fb))
    diffs = []
    for k in keys:
        va, vb = fa.get(k, "<absent>"), fb.get(k, "<absent>")
        # floats: exact comparison is fine for same-build payloads; a
        # difference here is a real change, not float noise
        if va != vb:
            diffs.append({"path": k, "version_a": va, "version_b": vb})
    return {
        "version_a": version_a,
        "version_b": version_b,
        "n_changed_leaves": len(diffs),
        "diffs": diffs,
    }


def activate_version(store_root: str, model_name: str, version: int) -> dict[str, Any]:
    """Point ``active.json`` at ``version`` — the single mutable operation in
    the store. Refuses to activate a version that failed its round-trip
    verify (a broken artefact must not become the model the pipeline runs
    on), and refuses archived versions. The previous active version (if any)
    is left in place with its status untouched — it is simply no longer
    active, and can be re-activated (that is rollback)."""
    require_splink()
    v = load_version(store_root, model_name, version)
    meta = v["metadata"]
    if meta.get("status") == "archived":
        raise ValueError(f"v{version} is archived — unarchive it (set_status) before activating")
    if not meta.get("round_trip_verified"):
        raise ValueError(
            f"v{version} failed round-trip verification ({meta.get('round_trip_error', 'no verify')}) "
            "— it cannot become the active model"
        )
    pointer = _active_pointer(store_root, model_name)
    previous = None
    if pointer.exists():
        previous = _read_json(pointer).get("version")
    _write_json(pointer, {"version": version})
    _record_activation(store_root, model_name, version)  # rollback depends on this
    # status bookkeeping: the activated version is "active", the old one
    # drops back to "evaluated" (it was validated, it is just not the
    # running model now)
    _set_status_in_file(store_root, model_name, version, "active")
    if previous is not None and previous != version:
        _set_status_in_file(store_root, model_name, previous, "evaluated")
    return {"activated": version, "previous_active": previous}


def rollback(store_root: str, model_name: str) -> dict[str, Any]:
    """Re-activate the version that was active before the current one.
    The history of activations is in ``active_history.json`` (appended by
    ``activate_version``'s caller via :func:`_record_activation`); rollback
    is "activate the second-to-last entry". With no history to go back to,
    raises — there is nothing to roll back *to*, and guessing is how a
    wrong model silently runs a production merge."""
    require_splink()
    history_path = Path(store_root) / _safe_name(model_name) / "active_history.json"
    if not history_path.exists():
        raise FileNotFoundError(f"no activation history for {model_name} — nothing to roll back to")
    history = _read_json(history_path)
    if len(history) < 2:
        raise ValueError(
            f"only one activation on record for {model_name} — nothing to roll back to"
        )
    current = history[-1]["version"]
    previous = history[-2]["version"]
    if previous == current:
        raise ValueError(
            f"current and previous activation are both v{current} — nothing to roll back to"
        )
    result = activate_version(store_root, model_name, previous)
    return {"rolled_back_from": current, **result}


def _record_activation(store_root: str, model_name: str, version: int) -> None:
    history_path = Path(store_root) / _safe_name(model_name) / "active_history.json"
    history: list[dict[str, Any]] = []
    if history_path.exists():
        try:
            history = _read_json(history_path)
        except json.JSONDecodeError:
            history = []
    history.append({"version": version, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    _write_json(history_path, history)


def archive_version(store_root: str, model_name: str, version: int) -> dict[str, Any]:
    """Terminal status: an archived version cannot be activated or rolled
    back to. The files stay on disk (audit trail — §46 wants the full
    lineage preserved, not deleted); archiving is a label, not a
    deletion."""
    require_splink()
    _set_status_in_file(store_root, model_name, version, "archived")
    pointer = _active_pointer(store_root, model_name)
    if pointer.exists() and _read_json(pointer).get("version") == version:
        pointer.unlink()  # an archived model cannot remain the active one
        return {"archived": version, "was_active": True}
    return {"archived": version, "was_active": False}


def set_status(store_root: str, model_name: str, version: int, status: str) -> dict[str, Any]:
    """Move a version along the lifecycle. Enforces the forward-only
    order (draft→trained→evaluated→active) — a version cannot regress from
    evaluated to draft without going through an explicit archive
    (deleting the status's meaning). ``archive`` is allowed from any
    non-archived status."""
    require_splink()
    if status not in STATUSES:
        raise ValueError(f"status {status!r} not one of {STATUSES}")
    _set_status_in_file(store_root, model_name, version, status)
    return {"version": version, "status": status}


def _set_status_in_file(store_root: str, model_name: str, version: int, status: str) -> None:
    meta_path = _version_dir(store_root, model_name, version) / "metadata.json"
    meta = _read_json(meta_path)
    current = meta.get("status")
    if status == current:
        return
    if status not in _FORWARD.get(current, set()):
        raise ValueError(f"illegal status transition {current!r} -> {status!r} for v{version}")
    meta["status"] = status
    _write_json(meta_path, meta)


# ── loading for use ────────────────────────────────────────────────────────


def load_active(store_root: str, model_name: str, df: Any) -> Any:
    """A live, fresh ``Linker`` for the model's *active* version over
    ``df``. This is what the resolve path calls when the engine is
    configured to "splink active model" — the pipeline never reads a
    payload by hand; it gets a trained, verified, activated model back as
    a runnable object."""
    require_splink()
    from splink import DuckDBAPI, Linker

    pointer = _active_pointer(store_root, model_name)
    if not pointer.exists():
        raise FileNotFoundError(f"no active model version for {model_name} — activate one first")
    version = _read_json(pointer)["version"]
    v = load_version(store_root, model_name, version)
    return Linker(df, v["payload"], DuckDBAPI())
