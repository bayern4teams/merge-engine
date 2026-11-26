"""P21 — the ER service (orchestrator layer, D-54).

One module holding the machinery behind the ``/v1`` routes: the online search
index (built from the same P5 token keys and the P13/P14 model the batch run
persists into ``merge_engine.er_model``), the resolve/PATCH decision paths
(find-or-create, per-attempt idempotency, client ``choice``, relaxed PATCH),
the extension-store protocol (ledger-first, keyed-by-id Mongo upsert), and the
history writer. Routes in ``src/api`` call this module; this module calls
tools/connectors — never the reverse (CLAUDE.md layering).

Design authorities: TECHNICAL_REPORT_2026-09-26.md P21 rev. c (D-44 … D-70)
and docs/er_contract_v13.md. Nothing here fits a threshold by hand: scores
come from the stored model, bars from its bands or the provisional
0.95/0.70 defaults published as provisional (D-63).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg2.extras  # type: ignore[import-untyped]  # psycopg2 ships no type stubs

from src.mcp_server.tools.fellegi_sunter import FieldModel, MatchModel
from src.mcp_server.tools.resolve_entities import (
    _AUTO_EXCLUDE,
    _AUTO_MATCH,
    _normalize,
    _score_pair,
    _token_keys,
    _wnp_prune,
)
from src.utils.id_utils import deterministic_uuid

logger = logging.getLogger("merge-engine.er_service")

# Contract §5.4 / D-67: soft cap per item across extra+source_text. Exceed it
# and the item still succeeds — stored truncated with `truncated: true`, never
# silent, never an error (their "no size limit" ask answered with a ceiling we
# state).
MAX_ITEM_BYTES = 64 * 1024

# Provisional extension-type bars (D-63): a type with no batch run has no
# fitted model, so there is nothing to fit these to — they are published as
# provisional in /v1/entity-types, never presented as calibrated.
PROVISIONAL_THRESHOLDS = {"auto_match": _AUTO_MATCH, "review": _AUTO_EXCLUDE}

ENRICHMENT_ORIGIN = "enrichment_engine"

_MAX_ITEMS = 100


def utcnow() -> datetime:
    return datetime.now(UTC)


def request_sha256(payload: Any) -> str:
    """Canonical hash of a request item for the idempotency table.

    sorted-keys + compact separators so two syntactically different but
    semantically equal bodies hash the same (the only way `request_sha256`
    can mean what the contract says it means).
    """
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def clamp_item_text(item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Apply D-67's 64 KB soft cap to ``extra``+``source_text`` of one item.

    Returns (possibly truncated item copy, truncated?). The cap is measured
    over the two fields combined, per item; truncation cuts source_text, then
    extra (JSON-serialisable prefix), and flags itself in the response —
    never silent, never a failure.
    """
    out = dict(item)
    extra_raw = out.get("extra") or {}
    source_text = out.get("source_text") or ""
    combined = len(json.dumps(extra_raw, default=str)) + len(source_text.encode())
    if combined <= MAX_ITEM_BYTES:
        return out, False
    out["source_text"] = ""
    keep = max(MAX_ITEM_BYTES - len(json.dumps(extra_raw, default=str)), 0)
    if keep:
        text = source_text
        while keep and len(text.encode()) > keep:
            text = text[: max(len(text) // 2, 1)]
        out["source_text"] = text
    extra_budget = max(MAX_ITEM_BYTES - len((out["source_text"] or "").encode()), 0)
    extra_json = json.dumps(extra_raw, sort_keys=True, default=str)
    if len(extra_json) > extra_budget:
        # Keep whole keys (each value JSON-serialised) until the budget is
        # spent — a half-JSON blob is worse than a shorter, valid one.
        kept: dict[str, Any] = {}
        used = 2  # braces
        for k, v in extra_raw.items():
            entry = json.dumps({k: v}, default=str)
            if used + len(entry) > extra_budget:
                break
            kept[k] = v
            used += len(entry) + 1
        out["extra"] = kept
    return out, True


# ── model persistence ───────────────────────────────────────────────────────


def serialize_model(model: Any) -> dict[str, Any] | None:
    """JSON-safe form of a fitted ``MatchModel`` (what er_model.params holds).

    Only data is persisted — m/u tables, per-field term frequencies, the
    match prior and P14's token document frequencies — everything the online
    scorer needs to reproduce ``_score_pair`` exactly as the batch run scored.
    """
    if model is None:
        return None
    token_df = getattr(model, "token_df", None)
    return {
        "match_prior": model.match_prior,
        "fields": {
            name: {
                "name": fm.name,
                "m": fm.m,
                "u": fm.u,
                "term_frequency": fm.term_frequency,
                "is_unique_id": fm.is_unique_id,
            }
            for name, fm in model.fields.items()
        },
        "token_df": list(token_df) if token_df is not None else None,
        # explicit band if this model carries one (tests + tuned runs)
        "thresholds": getattr(model, "thresholds", None),
    }


def deserialize_model(params: dict[str, Any] | None) -> MatchModel | None:
    """Rebuild a MatchModel from ``serialize_model`` output (inverse, exact)."""
    if not params or "fields" not in params:
        return None
    try:
        model = MatchModel(
            fields={
                name: FieldModel(
                    name=spec["name"],
                    m=spec["m"],
                    u=spec["u"],
                    term_frequency=spec.get("term_frequency", {}),
                    is_unique_id=spec.get("is_unique_id", False),
                )
                for name, spec in params["fields"].items()
            },
            match_prior=params["match_prior"],
        )
        token_df = params.get("token_df")
        if token_df is not None:
            model.token_df = (token_df[0], token_df[1])
        # C6: an explicit band stored with the run's model survives the
        # round-trip (thresholds_from_model reads the attribute back); the
        # batch fit bars stay implicit and read back as the defaults.
        band = params.get("thresholds")
        if isinstance(band, dict):
            model.thresholds = band
        return model
    except Exception as exc:  # a corrupt row must not take the service down
        logger.warning("Could not rebuild match model: %s", exc)
        return None


# ── errors ──────────────────────────────────────────────────────────────────


class ErError(Exception):
    """An error with an HTTP status and contract-shaped body."""

    def __init__(self, status: int, code: str, message: str, fields: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields or {}

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.fields:
            out["fields"] = self.fields
        return out


# ── the service ─────────────────────────────────────────────────────────────


@dataclass
class TypeState:
    """One entity type's online state: rows, token index, model, bars."""

    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    token_index: dict[str, set[str]] = field(default_factory=dict)
    model: MatchModel | None = None
    thresholds: dict[str, float] = field(default_factory=lambda: dict(PROVISIONAL_THRESHOLDS))
    provisional: bool = True
    loaded_at: float = 0.0


class ErService:
    """The machinery behind the /v1 routes.

    ``connector`` is a PostgreSQLDestinationConnector (destination DB, D-45
    side tables included). Extension-store Mongo access is injected as
    ``mongo_db`` — an object exposing ``collection(name)`` with
    ``replace_one({"_id": …}, doc, upsert=True)``, ``find_one`` and
    ``count_documents`` (pymongo's AsyncMongoClient database satisfies this;
    tests inject the local mongod). Keeping it injected is what makes D-62's
    failure-isolation testable: None simulates "their Mongo is down" without
    the unified path noticing.
    """

    def __init__(
        self,
        connector: Any,
        entity_types_provider: Callable[[], dict[str, dict[str, Any]]] | None = None,
        mongo_db: Any = None,
        mongo_database: str = "enrichment_entities",
    ):
        self.conn = connector
        self._types_provider = entity_types_provider
        self.mongo_db = mongo_db
        self.mongo_database = mongo_database
        self._types: dict[str, TypeState] = {}
        # In-process write serialization: FastAPI runs the sync route
        # handlers on a thread pool, and two threads sharing this service
        # share one psycopg2 session — session advisory locks do NOT
        # serialize against your own session, so the advisory pair below
        # guards OTHER processes only. Within the process the batches
        # queue here (contract guarantee 4: concurrent creates get one id).
        self._write_gate = threading.Lock()
        self.sweep_grace_seconds = 60.0

    # ── type registry ───────────────────────────────────────────────────

    def unified_types(self) -> dict[str, dict[str, Any]]:
        """Destination types from the live schema (the /v1/entity-types source).

        Provider returns ``{entity_type: {"table", "fields",
        "identity_fields", "required_fields", "reference_fields",
        "table_schema"}}``; defaults to reading the connector directly.
        """
        if self._types_provider is not None:
            return self._types_provider()
        return build_entity_type_catalog(self.conn)

    def registry_row(self, entity_type: str) -> dict[str, Any] | None:
        rows = self.conn.execute_sql(
            f'SELECT store, thresholds, collection FROM "{self.conn._er_schema}"'
            ".er_type_registry WHERE entity_type = %s",
            (entity_type,),
        )
        if not rows:
            return None
        store, thresholds, collection = rows[0]
        return {"store": store, "thresholds": thresholds or {}, "collection": collection}

    def extension_types(self) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute_sql(
            f"SELECT entity_type, fields, identity_fields, required_fields, "
            f'reference_fields, thresholds, collection FROM "{self.conn._er_schema}"'
            ".er_type_registry WHERE store = 'enrichment'",
        )
        out: dict[str, dict[str, Any]] = {}
        for et, fields, ident, req, refs, th, coll in rows:
            out[et] = {
                "store": "enrichment",
                "collection": f"{self.mongo_database}.{coll}" if coll else None,
                "fields": fields or {},
                "identity_fields": ident or [],
                "required_fields": req or [],
                "reference_fields": refs or {},
                "thresholds": th or dict(PROVISIONAL_THRESHOLDS),
                "provisional": True,
            }
        return out

    def type_def(self, entity_type: str) -> dict[str, Any] | None:
        """The /v1/entity-types entry for one type (unified or extension)."""
        ext = self.extension_types().get(entity_type)
        if ext is not None:
            return {"entity_type": entity_type, **ext}
        uni = self.unified_types().get(entity_type)
        if uni is not None:
            state = self._ensure_type(entity_type)
            return {
                "entity_type": entity_type,
                "store": "unified",
                "table": uni["table"],
                "fields": uni["fields"],
                "identity_fields": uni["identity_fields"],
                "required_fields": uni["required_fields"],
                "reference_fields": uni["reference_fields"],
                "thresholds": state.thresholds,
                "provisional": state.provisional,
            }
        return None

    def register_extension_type(
        self, entity_type: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        """PUT /v1/entity-types/{type} (contract §5.2, P21j).

        Registration is validation + insert/upsert into er_type_registry.
        Fields are only ever ADDED (§5.2): an existing extension type keeps
        every field it has; same definition twice is a 200 no-op. A name the
        destination already owns is 409 — a unified table is not movable by
        an HTTP call. Thresholds are ER's provisional bars (no fitted model
        exists for a type nobody has ever loaded).
        """
        fields = body.get("fields") or {}
        identity = body.get("identity_fields") or []
        required = body.get("required_fields") or []
        refs = body.get("reference_fields") or {}
        allowed_types = {"string", "integer", "number", "boolean", "date", "datetime", "string[]"}
        bad = {k: v for k, v in fields.items() if v not in allowed_types}
        if bad:
            raise ErError(422, "VALIDATION_FAILED", "bad field type(s)", bad)
        unknown = [f for f in list(identity) + list(required) if f not in fields]
        if unknown:
            raise ErError(
                422,
                "VALIDATION_FAILED",
                "identity/required fields must be declared in fields",
                {f: "not in fields" for f in unknown},
            )
        for ref_field, ref_type in refs.items():
            if ref_field in fields:
                raise ErError(
                    422,
                    "VALIDATION_FAILED",
                    "reference field is also a data field",
                    {ref_field: "cannot be both"},
                )
            if self.type_def(str(ref_type)) is None:
                raise ErError(
                    422,
                    "VALIDATION_FAILED",
                    "reference target type is unknown",
                    {ref_field: f"'{ref_type}' is in neither store"},
                )
        if entity_type in self.unified_types():
            raise ErError(409, "TYPE_TAKEN", f"'{entity_type}' is a unified type")
        s = self.conn._er_schema
        existing_rows = self.conn.execute_sql(
            f'SELECT fields, identity_fields, required_fields FROM "{s}".er_type_registry '
            "WHERE entity_type = %s",
            (entity_type,),
        )
        if not existing_rows:
            self.conn.execute_sql(
                f'INSERT INTO "{s}".er_type_registry (entity_type, store, fields, '
                "identity_fields, required_fields, reference_fields, thresholds, collection) "
                "VALUES (%s, 'enrichment', %s, %s, %s, %s, %s, %s)",
                (
                    entity_type,
                    json.dumps(fields),
                    list(identity),
                    list(required),
                    json.dumps(refs),
                    json.dumps(dict(PROVISIONAL_THRESHOLDS)),
                    entity_type,
                ),
            )
            self.invalidate()
            entry = self.type_def(entity_type)
            return 201, entry or {"entity_type": entity_type, "store": "enrichment"}
        # Additive update (§5.2): fields only ever added, lists only ever
        # extended. Same definition again changes nothing → 200 no-op.
        cur_fields, cur_identity, cur_required = existing_rows[0]
        merged = dict(cur_fields or {})
        merged_identity = list(cur_identity or [])
        merged_required = list(cur_required or [])
        changed = False
        for k, v in fields.items():
            if k not in merged:
                merged[k] = v
                changed = True
        for f in identity:
            if f not in merged_identity:
                merged_identity.append(f)
                changed = True
        for f in required:
            if f not in merged_required:
                merged_required.append(f)
                changed = True
        if changed:
            self.conn.execute_sql(
                f'UPDATE "{s}".er_type_registry '
                "SET fields = %s, identity_fields = %s, required_fields = %s "
                "WHERE entity_type = %s",
                (json.dumps(merged), merged_identity, merged_required, entity_type),
            )
            self.invalidate()
        entry = self.type_def(entity_type)
        return 200, entry or {"entity_type": entity_type, "store": "enrichment"}

    def entity_types_payload(self) -> dict[str, Any]:
        entries = []
        for name in sorted(self.unified_types()):
            entry = self.type_def(name)
            if entry is not None:
                entries.append(entry)
        for name in sorted(self.extension_types()):
            entry = self.type_def(name)
            if entry is not None:
                entries.append(entry)
        return {"entity_types": entries}

    # ── online index (P21c) ─────────────────────────────────────────────

    def _ensure_type(self, entity_type: str) -> TypeState:
        state = self._types.get(entity_type)
        if state is None:
            state = TypeState()
            self._types[entity_type] = state
        return state

    def invalidate(self, entity_type: str | None = None) -> None:
        """Drop cached rows/model for one type (or all) — after each load."""
        if entity_type is None:
            self._types.clear()
        else:
            self._types.pop(entity_type, None)

    def rebuild_index(self, entity_type: str) -> TypeState:
        """Reload destination rows + the run-persisted model for one type.

        The index is exactly the batch's blocking key function applied to the
        type's own destination rows (``_token_keys`` — no second blocking
        logic, P21c), and the scorer is ``_score_pair`` with the stored model.
        A type with no destination table and no registry row is unsupported
        (C10/D-55); a table with no er_model row scores via the heuristic
        path of _score_pair (model=None) — honest, and labelled provisional
        in /v1/entity-types.
        """
        state = self._ensure_type(entity_type)
        tdef = self.unified_types().get(entity_type)
        if tdef is None:
            return state
        cols = [c["name"] for c in tdef.get("table_columns", [])]
        data_cols = [c for c in cols if c != "id"]
        rows: dict[str, dict[str, Any]] = {}
        index: dict[str, set[str]] = {}
        if data_cols:
            col_list = ", ".join(f'"{c}"' for c in ["id", *data_cols])
            for r in self.conn.execute_sql(f"SELECT {col_list} FROM {tdef['table']}"):
                rid = str(r[0])
                rec = dict(zip(data_cols, r[1:], strict=False))
                rec = {"id": rid, **data_projection(rec, data_cols)}
                rows[rid] = rec
                for tok in _token_keys({k: v for k, v in rec.items() if k != "id"}):
                    index.setdefault(tok, set()).add(rid)
        model_params = self.conn.execute_sql(
            f'SELECT params, run_id FROM "{self.conn._er_schema}".er_model WHERE entity_type = %s',
            (entity_type,),
        )
        if model_params:
            state.model = deserialize_model(model_params[0][0])
        thresholds: dict[str, float] | None = None
        if state.model is not None:
            thresholds = thresholds_from_model(state.model)
        if thresholds is None:
            thresholds = dict(PROVISIONAL_THRESHOLDS)
        state.thresholds = thresholds
        state.provisional = state.model is None
        state.rows = rows
        state.token_index = index
        state.loaded_at = time.monotonic()
        return state

    def candidates_for(
        self, item: dict[str, Any], state: TypeState, limit: int
    ) -> list[dict[str, Any]]:
        """P21c candidate search, side-effect-free: token lookup → score with
        the stored model → WNP cut (the same node-centric rule the batch's
        P5 meta-blocking uses, no new constant) → drop below the review bar
        (contract rule 3) → sort → limit."""
        query = {k: v for k, v in item.get("attributes", {}).items() if v is not None}
        for ref_field, ref_val in (item.get("references") or {}).items():
            query[ref_field] = ref_val
        if not query:
            return []
        tokens = _token_keys(query)
        counts: dict[str, int] = {}
        for tok in tokens:
            for rid in state.token_index.get(tok, ()):
                counts[rid] = counts.get(rid, 0) + 1
        if not counts:
            return []
        scored: dict[str, float] = {}
        for rid in counts:
            row = state.rows[rid]
            score = _score_pair(query, row, state.model, normalise=_normalize)
            scored[rid] = float(score)
        kept = _wnp_prune({(k, "q"): w for k, w in scored.items()})
        review = state.thresholds["review"]
        out: list[dict[str, Any]] = []
        for (a, b), w in kept.items():
            rid = a if a != "q" else b
            if rid == "q" or w < review or rid not in state.rows:
                continue
            out.append({"id": rid, "score": round(float(w), 6), "attributes": state.rows[rid]})
        out.sort(key=lambda c: -float(c["score"]))
        return out[:limit]

    # ── the decision path (P21c/P21l core) ──────────────────────────────

    def prepare_batch(self, entity_types: set[str]) -> None:
        """Refresh the index for every type a batch touches, once per batch.

        A destination read of the involved types is cheap next to the batch
        itself and is always correct — no staleness heuristic, and a batch
        whose rows changed behind our back (a test planting rows, a finished
        load) is seen by the NEXT request, not the one after that. Search and
        resolve call this at entry; one scorer, one freshness rule.
        """
        for et in sorted(entity_types):
            tdef = self.type_def(et)
            if tdef is not None and tdef["store"] == "unified":
                self.rebuild_index(et)

    def decide(self, entity_type: str, item: dict[str, Any]) -> dict[str, Any]:
        """What resolve/search says right now for one item (no writes).

        v1.3 decision table (§5.4): exactly one candidate ≥ auto_match →
        MATCHED; two or more → AMBIGUOUS on every ask; best in band →
        AMBIGUOUS; below review → CREATED. PENDING_REVIEW is never produced
        for the Enrichment Engine (D-69).
        """
        tdef = self.type_def(entity_type)
        if tdef is None:
            return {"decision": "UNSUPPORTED_ENTITY_TYPE"}
        if tdef["store"] == "unified":
            state = self._ensure_type(entity_type)
            if not state.loaded_at:
                state = self.rebuild_index(entity_type)
        else:
            state = self._extension_state(entity_type, tdef)
        candidates = self.candidates_for(item, state, limit=10)
        return self._decide_from_candidates(entity_type, tdef, item, candidates)

    def _decide_from_candidates(
        self,
        entity_type: str,
        tdef: dict[str, Any],
        item: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        th = tdef["thresholds"]
        auto, review = float(th["auto_match"]), float(th["review"])
        over = [c for c in candidates if c["score"] >= auto]
        in_band = [c for c in candidates if review <= c["score"] < auto]
        differing = differing_fields(candidates)
        if len(over) >= 2:
            return {"decision": "AMBIGUOUS", "candidates": over[:10], "differing_fields": differing}
        if len(over) == 1:
            top = over[0]
            return {
                "decision": "MATCHED",
                "id": top["id"],
                "score": top["score"],
                "matched_fields": matched_fields(item, top["attributes"]),
            }
        if in_band:
            return {
                "decision": "AMBIGUOUS",
                "candidates": candidates[:10],
                "differing_fields": differing,
            }
        return {"decision": "CREATED", "candidates_below_review": len(candidates)}

    def _extension_state(self, entity_type: str, tdef: dict[str, Any]) -> TypeState:
        """Rows for the extension store read live from their Mongo (D-62).

        Failure isolation: their Mongo down raises MongoUnavailable, which
        callers map to 503 for extension types only — the unified path never
        touches this code.
        """
        if self.mongo_db is None:
            raise MongoUnavailable("extension store not configured/unreachable")
        coll = (tdef.get("collection") or "").split(".")[-1] or entity_type
        state = TypeState(thresholds=dict(tdef["thresholds"]), provisional=True)
        try:
            docs = list(self._mongo_coll(coll).find({}))
        except Exception as exc:
            raise MongoUnavailable(f"extension store read failed: {exc}") from exc
        for doc in docs:
            rid = str(doc.get("_id"))
            rec = {k: v for k, v in doc.items() if k not in ("_id", "_version")}
            state.rows[rid] = {"id": rid, **rec}
            for tok in _token_keys({k: v for k, v in rec.items() if k != "id"}):
                state.token_index.setdefault(tok, set()).add(rid)
        return state

    def _mongo_coll(self, name: str) -> Any:
        db = self.mongo_db
        coll = db[name] if hasattr(db, "__getitem__") else db.collection(name)
        # pymongo lazily connects; fail FAST here so the ledger-first
        # protocol never reserves an id for a store that cannot be reached.
        with contextlib.suppress(AttributeError):
            # dict-backed fakes (unit tests) have no client to ping
            coll.database.client.admin.command("ping")
        return coll

    # ── batch entry points (what the /v1 routes call) ───────────────────

    def run_resolve(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """POST /v1/entities:resolve. The whole request is one unit for the
        load-pause 503 (v1.3 §5.4: 'the WHOLE request is rejected, we resend
        all of it later'), while items stay independent for every other
        outcome (one bad item never fails the batch)."""
        items = body.get("items") or []
        if not isinstance(items, list) or not items:
            raise ErError(400, "VALIDATION_FAILED", "items[] is required")
        if len(items) > _MAX_ITEMS:
            raise ErError(400, "VALIDATION_FAILED", f"max {_MAX_ITEMS} items per request")
        types = {str(i.get("entity_type")) for i in items if isinstance(i, dict)}
        # extension items 503 ONLY when their store is actually down (D-62
        # isolation); a unified batch never pays their latency.
        self._extension_state_probe(types)
        if not self._write_gate.acquire(timeout=2.0):
            raise ErError(503, "LOAD_RUNNING", "a batch load is running; retry later")
        try:
            try:
                self.acquire_write_locks(types)
            except WritePaused:
                raise ErError(503, "LOAD_RUNNING", "a batch load is running; retry later") from None
            self.prepare_batch(types)
            results = []
            for raw_item in items:
                raw = raw_item if isinstance(raw_item, dict) else {}
                item, truncated = clamp_item_text(raw)
                item_id = item.get("item_id")
                key = item.get("idempotency_key") or ""
                if not key or len(key) > 255:
                    results.append(
                        {
                            "item_id": item_id,
                            "decision": "INVALID",
                            "error": "VALIDATION_FAILED",
                            "fields": {"idempotency_key": "required, <= 255 chars"},
                        }
                    )
                    continue
                # The mismatch rule guards RETRIES — the hash is over what the
                # client actually sent, before our D-67 clamp.
                sha = request_sha256(raw)
                try:
                    result = self.idempotent_take(key, sha, self.resolve_item, item)
                except ErError as exc:
                    results.append(
                        {
                            "item_id": item_id,
                            "decision": "INVALID",
                            "error": exc.code,
                            "message": exc.message,
                            "fields": exc.fields,
                        }
                    )
                    continue
                result = dict(result)
                if truncated:
                    result["truncated"] = True
                result.pop("_status", None)
                result["item_id"] = item_id
                results.append(result)
            return 200, {"results": results}
        finally:
            self.release_write_locks()
            self._write_gate.release()

    def run_search(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """POST /v1/entities:search (P21i, D-68): side-effect-free preview of
        what resolve would answer right now. No keys, no provenance, no
        choice — nothing created, changed, or logged as a decision."""
        items = body.get("items") or []
        if not isinstance(items, list) or not items:
            raise ErError(400, "VALIDATION_FAILED", "items[] is required")
        if len(items) > _MAX_ITEMS:
            raise ErError(400, "VALIDATION_FAILED", f"max {_MAX_ITEMS} items per request")
        types = {str(i.get("entity_type")) for i in items if isinstance(i, dict)}
        if any((t := self.type_def(et)) is not None and t["store"] == "enrichment" for et in types):
            self._extension_state_probe(types)
        self.prepare_batch(types)
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("item_id")
            try:
                verdict = self.decide(str(item.get("entity_type") or ""), item)
            except MongoUnavailable as exc:
                raise ErError(503, "EXTENSION_STORE_DOWN", str(exc)) from exc
            decision = verdict["decision"]
            entry: dict[str, Any] = {"item_id": item_id, "would_decide": decision}
            if decision in ("AMBIGUOUS", "MATCHED"):
                cands = verdict.get("candidates") or (
                    [{"id": verdict["id"], "score": verdict["score"], "attributes": {}}]
                    if decision == "MATCHED"
                    else []
                )
                entry["candidates"] = [
                    {"id": c["id"], "score": c["score"], "attributes": c.get("attributes", {})}
                    for c in cands[:10]
                ]
                entry["differing_fields"] = verdict.get("differing_fields", [])
            results.append(entry)
        return 200, {"results": results}

    def _extension_state_probe(self, types: set[str]) -> None:
        """Raise MongoUnavailable when an item targets an extension type and
        their store is unreachable (checked before touching anything)."""
        for et in types:
            tdef = self.type_def(et)
            if tdef is not None and tdef["store"] == "enrichment":
                if self.mongo_db is None:
                    raise MongoUnavailable("extension store not configured/unreachable")
                coll = (tdef.get("collection") or "").split(".")[-1] or et
                try:
                    self._mongo_coll(coll).find_one({"_id": "__probe__"})
                except Exception as exc:
                    raise MongoUnavailable(f"extension store unreachable: {exc}") from exc

    # ── reads (P21d / P21k) ─────────────────────────────────────────────

    def resolve_id(self, entity_type: str, entity_id: str) -> tuple[str, str | None]:
        """Follow er_redirect once (chains compressed on write). Returns
        (surviving_id, redirected_from|None)."""
        rows = self.conn.execute_sql(
            f'SELECT new_id FROM "{self.conn._er_schema}".er_redirect '
            "WHERE entity_type = %s AND old_id = %s",
            (entity_type, entity_id),
        )
        if rows:
            return str(rows[0][0]), entity_id
        return entity_id, None

    def id_ever_issued(self, entity_type: str, entity_id: str) -> bool:
        """True if the id exists anywhere ER ever wrote (destination, entity
        map, redirect target side, extension outbox) — the UNKNOWN_ID guard
        for `choice {id}` (D-66: never-issued ids are INVALID, not created)."""
        tdef = self.type_def(entity_type)
        if tdef is not None and tdef["store"] == "enrichment":
            try:
                coll = (tdef.get("collection") or "").split(".")[-1] or entity_type
                if self._mongo_coll(coll).find_one({"_id": entity_id}) is not None:
                    return True
            except MongoUnavailable:
                raise
            except Exception as exc:
                logger.warning(
                    "id_ever_issued: extension store read failed for %s: %s", entity_id, exc
                )
        if self.conn.execute_sql(
            f'SELECT 1 FROM "{self.conn._er_schema}".er_entity_version '
            "WHERE entity_type = %s AND id = %s",
            (entity_type, entity_id),
        ):
            return True
        if self.conn.execute_sql(
            f'SELECT 1 FROM "{self.conn._er_schema}".er_extension_outbox '
            "WHERE entity_type = %s AND id = %s",
            (entity_type, entity_id),
        ):
            return True
        table = (tdef or {}).get("table")
        if table:
            try:
                found = self.conn.execute_sql(f"SELECT 1 FROM {table} WHERE id = %s", (entity_id,))
                if found:
                    return True
            except Exception as exc:
                # Unreadable destination table (dropped type?): the ledger and
                # version rows above already decided most lookups.
                logger.warning("id_ever_issued: destination read failed for %s: %s", entity_id, exc)
        return False

    def get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        """P21d: read one entity, redirect-aware; 404 only for never-issued
        ids; version from er_entity_version; extension types read Mongo."""
        tdef = self.type_def(entity_type)
        if tdef is None:
            raise ErError(404, "NOT_FOUND", f"entity type '{entity_type}' is in neither store")
        sid, redirected_from = self.resolve_id(entity_type, entity_id)
        version_row = self.conn.execute_sql(
            f'SELECT version FROM "{self.conn._er_schema}".er_entity_version '
            "WHERE entity_type = %s AND id = %s",
            (tdef["entity_type"], sid),
        )
        version = int(version_row[0][0]) if version_row else 1
        if tdef["store"] == "enrichment":
            if self.mongo_db is None:
                raise MongoUnavailable("extension store not configured/unreachable")
            coll = (tdef.get("collection") or "").split(".")[-1] or entity_type
            try:
                doc = self._mongo_coll(coll).find_one({"_id": sid})
            except MongoUnavailable:
                raise
            except Exception as exc:
                raise MongoUnavailable(f"extension store read failed: {exc}") from exc
            if doc is None:
                raise ErError(404, "NOT_FOUND", f"entity '{sid}' not found")
            attributes = {k: v for k, v in doc.items() if k not in ("_id", "_version")}
            return {
                "id": sid,
                "entity_type": entity_type,
                "attributes": attributes,
                "references": {},
                "version": int(doc.get("_version", version)),
                "redirected_from": redirected_from,
            }
        table = tdef["table"]
        rows = self.conn.execute_sql(f"SELECT * FROM {table} WHERE id = %s", (sid,))
        if not rows:
            raise ErError(404, "NOT_FOUND", f"entity '{sid}' was never issued")
        # execute_sql returns plain tuples; column names come from the schema.
        cols_desc = self.conn.execute_sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = split_part(%s, '.', 1) AND table_name = split_part(%s, '.', 2) "
            "ORDER BY ordinal_position",
            (_qualify(table), _qualify(table)),
        )
        names = [r[0] for r in cols_desc]
        record = dict(zip(names, rows[0], strict=False))
        attributes = {
            k: v
            for k, v in record.items()
            if k in tdef["fields"] and k not in tdef["reference_fields"]
        }
        references = {
            k: record.get(k) for k in tdef["reference_fields"] if record.get(k) is not None
        }
        return {
            "id": sid,
            "entity_type": entity_type,
            "attributes": attributes,
            "references": references,
            "version": version,
            "redirected_from": redirected_from,
        }

    def get_history(
        self, entity_type: str, entity_id: str, since_version: int | None
    ) -> dict[str, Any]:
        """P21k: GET .../history — merged ids redirect, as for GET."""
        tdef = self.type_def(entity_type)
        if tdef is None:
            raise ErError(404, "NOT_FOUND", f"entity type '{entity_type}' is in neither store")
        sid, redirected_from = self.resolve_id(entity_type, entity_id)
        sql = (
            "SELECT version, at, field, old, new, source, reason, attempted, provenance "
            f'FROM "{self.conn._er_schema}".er_field_history '
            "WHERE entity_type = %s AND id = %s"
        )
        params: list[Any] = [entity_type, sid]
        if since_version is not None:
            sql += " AND version > %s"
            params.append(since_version)
        sql += " ORDER BY event_id"
        changes = []
        for ver, at, f, old, new, source, reason, attempted, prov in self.conn.execute_sql(
            sql, tuple(params)
        ):
            row: dict[str, Any] = {
                "version": ver,
                "at": at.isoformat() if hasattr(at, "isoformat") else at,
                "field": f,
                "old": old,
                "new": new,
                "source": source,
            }
            if reason:
                row["reason"] = reason
            if attempted is not None:
                row["attempted"] = attempted
            if prov:
                row["provenance"] = prov
            changes.append(row)
        out: dict[str, Any] = {"id": sid, "changes": changes}
        if redirected_from:
            out["redirected_from"] = redirected_from
        return out

    # ── idempotency (P21e step 2, D-66 per-attempt keys) ────────────────

    def idempotent_take(
        self,
        key: str,
        sha: str,
        handler: Any,
        *args: Any,
    ) -> dict[str, Any]:
        """Run ``handler`` at most once per key, replay its stored answer.

        Stripe-shaped (§2.23 [ID3]): same key + same hash → the first response
        with ``idempotent_replay: true``; same key + different hash → 422
        IDEMPOTENCY_PAYLOAD_MISMATCH (that rule protects RETRIES; a re-ASK
        carries a new ``:2`` key, which is exactly why v1.2's token flow is
        gone). The claim + the write land in one transaction, and the
        idempotency-row insert itself serializes concurrent same-key callers:
        a second INSERT ... no ON CONFLICT blocks on the PK until the first
        commits, then raises — which we resolve into a replay.
        """
        conn = self.conn._ensure_connection()
        s = self.conn._er_schema
        try:
            with self.conn.atomic():
                cur = conn.cursor()
                cur.execute(
                    f"INSERT INTO {s}.er_idempotency (key, request_sha256, status, response) "
                    "VALUES (%s, %s, -1, NULL)",
                    (key, sha),
                )
        except Exception as exc:
            if "duplicate key" not in str(exc):
                raise
            rows = self.conn.execute_sql(
                f"SELECT request_sha256, status, response FROM {s}.er_idempotency WHERE key = %s",
                (key,),
            )
            if not rows:
                raise
            stored_sha, status, stored_response = rows[0]
            if stored_sha != sha:
                raise ErError(
                    422,
                    "IDEMPOTENCY_PAYLOAD_MISMATCH",
                    "idempotency key reused with a different payload",
                ) from exc
            if status == -1:
                # The first attempt claimed the key but its transaction did
                # not commit (crash/reject): the key is re-runnable — the
                # whole point of storing it transactionally.
                self.conn.execute_sql(
                    f"DELETE FROM {s}.er_idempotency WHERE key = %s AND status = -1", (key,)
                )
                result = self.idempotent_take(key, sha, handler, *args)
                result["idempotent_replay"] = True
                return result
            body = dict(stored_response or {})
            body["idempotent_replay"] = True
            return body
        result = handler(*args)
        self.conn.execute_sql(
            f"UPDATE {s}.er_idempotency SET status = %s, response = %s::jsonb WHERE key = %s",
            (int(result.get("_status", 200)), json.dumps(result, default=str), key),
        )
        return dict(result)

    # ── locks (P21e step 4, D-49) ───────────────────────────────────────

    def acquire_write_locks(self, entity_types: set[str]) -> None:
        """Try the batch load's write lock; if held, the whole write request
        is a 503 (C7/D-49 — resolve rejects the whole request, the client
        resends everything after Retry-After). Then take a per-type lock for
        every type in the batch so two workers cannot both miss and both
        create (contract guarantee 4).

        Session-level advisory locks on this service's dedicated connection:
        the load (P21g-2) holds the same write key for its whole transaction.
        The try is given a BOUNDED wait (~2s) rather than a single attempt:
        a concurrent ER worker holds it only for its own short batch, so
        waiting turns acceptance-3's "two concurrent creates" into a queue
        (second worker rebuilds under the lock and answers MATCHED — exactly
        one row); a batch load holds it for seconds-to-minutes, so the wait
        expires and the request 503s exactly as D-49 demands. Per-type keys
        are taken in sorted order (released likewise) so multi-type batches
        can't deadlock.

        ``prepare_batch`` (the index rebuild) MUST run after this returns —
        that ordering is what makes the queued worker see the first one's
        committed row.
        """
        conn = self.conn._ensure_connection()
        keys = sorted(_lock_key("er-type", et) for et in entity_types)
        deadline = time.monotonic() + 2.0
        while True:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (WRITE_LOCK_KEY,))
                if cur.fetchone()[0]:
                    break
            if time.monotonic() >= deadline:
                raise WritePaused()
            time.sleep(0.05)
        with conn.cursor() as cur:
            for k in keys:
                cur.execute("SELECT pg_advisory_lock(%s)", (k,))
        conn.commit()
        self._held_lock_keys = keys

    def release_write_locks(self) -> None:
        conn = self.conn._ensure_connection()
        with conn.cursor() as cur:
            for k in sorted(getattr(self, "_held_lock_keys", []), reverse=True):
                cur.execute("SELECT pg_advisory_unlock(%s)", (k,))
            cur.execute("SELECT pg_advisory_unlock(%s)", (WRITE_LOCK_KEY,))
        conn.commit()
        self._held_lock_keys = []

    # ── resolve item (P21e + P21l choice) ───────────────────────────────

    def resolve_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """One resolve item, end to end: validation, scoring, choice, and on
        a create the full find-or-create contract path. The caller holds the
        write locks for the whole batch."""
        entity_type = item.get("entity_type") or ""
        attrs = item.get("attributes") or {}
        refs = item.get("references") or {}
        tdef = self.type_def(entity_type)
        if tdef is None:
            return {"decision": "UNSUPPORTED_ENTITY_TYPE", "_status": 200}
        bad_fields = {k: "unknown" for k in attrs if k not in tdef["fields"]}
        for r in refs:
            if r not in tdef["reference_fields"]:
                bad_fields[r] = "unknown"
        if bad_fields:
            return {
                "decision": "INVALID",
                "error": "VALIDATION_FAILED",
                "fields": bad_fields,
                "_status": 200,
            }
        missing = [f for f in tdef["required_fields"] if not attrs.get(f)]
        if missing:
            return {
                "decision": "INVALID",
                "error": "VALIDATION_FAILED",
                "fields": {f: "required" for f in missing},
                "_status": 200,
            }
        for ref_field, ref_id in refs.items():
            if not self.id_ever_issued(tdef["reference_fields"][ref_field], str(ref_id)):
                return {
                    "decision": "INVALID",
                    "error": "BAD_REFERENCE",
                    "fields": {ref_field: f"'{ref_id}' was never issued"},
                    "_status": 200,
                }

        choice = item.get("choice")
        verdict = self.decide(entity_type, item)
        decision = verdict["decision"]

        if choice is not None:
            return self._apply_choice(entity_type, tdef, item, verdict, choice)

        if decision == "AMBIGUOUS":
            out = dict(verdict)
            out["_status"] = 200
            return out
        if decision == "MATCHED":
            return {
                "decision": "MATCHED",
                "id": verdict["id"],
                "score": verdict["score"],
                "matched_fields": verdict["matched_fields"],
                "_status": 200,
            }
        # CREATED through the create path (P21e step 6).
        return self.create_entity(entity_type, tdef, item, reason=None)

    def _apply_choice(
        self,
        entity_type: str,
        tdef: dict[str, Any],
        item: dict[str, Any],
        verdict: dict[str, Any],
        choice: dict[str, Any],
    ) -> dict[str, Any]:
        """v1.3 §5.4: the client decides on AMBIGUOUS (D-66)."""
        if "new" in choice:
            # MATCH_APPEARED guard IS P21e step 5 — decide() just scored the
            # CURRENT state; a candidate that crossed auto_match since the
            # client's search is found here, before any create.
            if verdict["decision"] == "MATCHED":
                return {
                    "decision": "MATCHED",
                    "id": verdict["id"],
                    "score": verdict["score"],
                    "reason": "MATCH_APPEARED",
                    "decided_by": "client",
                    "_status": 200,
                }
            return self.create_entity(entity_type, tdef, item, reason="CREATED_BY_CLIENT_CHOICE")
        chosen = str(choice.get("id") or "")
        if not chosen or not self.id_ever_issued(entity_type, chosen):
            return {
                "decision": "INVALID",
                "error": "UNKNOWN_ID",
                "message": f"'{chosen}' was never issued",
                "_status": 200,
            }
        sid, redirected_from = self.resolve_id(entity_type, chosen)
        self.log_history(
            entity_type,
            sid,
            [
                {
                    "field": "__choice__",
                    "old": None,
                    "new": sid,
                    "source": ENRICHMENT_ORIGIN,
                    "reason": "CLIENT_CHOICE",
                    "provenance": item.get("provenance"),
                }
            ],
        )
        out: dict[str, Any] = {
            "decision": "MATCHED",
            "id": sid,
            "decided_by": "client",
            "_status": 200,
        }
        if redirected_from:
            out["redirected_from"] = redirected_from
        return out

    # ── create (P21e step 6 unified / P21j extension ledger-first) ──────

    def create_entity(
        self, entity_type: str, tdef: dict[str, Any], item: dict[str, Any], reason: str | None
    ) -> dict[str, Any]:
        if tdef["store"] == "enrichment":
            return self._create_extension(entity_type, tdef, item)
        return self._create_unified(entity_type, tdef, item)

    def _create_unified(
        self, entity_type: str, tdef: dict[str, Any], item: dict[str, Any]
    ) -> dict[str, Any]:
        """Enrichment enters as an origin (D-46): one append-only
        er_enrichment_record, its entity_map row (record_id -> id, the same
        table the batch persists so P21g-1 picks it up next run), and a
        destination row projected through survivorship from the single
        source. The id is the batch's own singleton id function over the
        enrichment record id — reproduction property (P21e step 6)."""
        attrs = {k: v for k, v in (item.get("attributes") or {}).items() if v is not None}
        refs = item.get("references") or {}
        # Derive record_id from the idempotency key when there is one: a
        # REPLAY must reuse the exact reservation (same id, same entity_map
        # row), not mint a second entity. uuid only for keyless callers.
        key = str(item.get("idempotency_key") or "") or uuid4hex()
        record_id = f"{ENRICHMENT_ORIGIN}:k:{key}"
        entity_id = new_id(entity_type, record_id)
        prov = item.get("provenance") or {}
        conn = self.conn._ensure_connection()
        s = self.conn._er_schema
        # Own transaction on the shared connection WITHOUT atomic(): the
        # idempotency layer above may already own atomic()'s slot, and
        # nesting it deadlocks the commit ownership (connector's own
        # _commit() is transaction-aware and defers inside one).
        cur = conn.cursor()
        try:
            self.conn.ensure_entity_map()
            cur.execute(
                f'INSERT INTO "{s}".er_enrichment_record (record_id, entity_type, attributes, '
                '"references", inferred_fields, extra, source_text, provenance) '
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s::jsonb)",
                (
                    record_id,
                    entity_type,
                    json.dumps(attrs, default=str),
                    json.dumps(refs, default=str),
                    list(item.get("inferred_fields") or []),
                    json.dumps(item.get("extra") or {}, default=str),
                    item.get("source_text"),
                    json.dumps(prov, default=str),
                ),
            )
            cur.execute(
                f'INSERT INTO "{self.conn._schema}"."merge_engine_entity_map" '
                "(entity_type, record_id, cluster_id, first_seen_run, updated_at) "
                "VALUES (%s, %s, %s, %s, now()) "
                "ON CONFLICT (entity_type, record_id) DO UPDATE SET "
                "cluster_id = EXCLUDED.cluster_id, updated_at = now()",
                (entity_type, record_id, entity_id, str(prov.get("run_id") or "")),
            )
            self._write_single_origin_row(
                cur, tdef, entity_id, attrs, refs, item.get("inferred_fields") or []
            )
            cur.execute(
                f'INSERT INTO "{s}".er_entity_version (entity_type, id, version, updated_at) '
                "VALUES (%s, %s, 1, now()) ON CONFLICT (entity_type, id) DO NOTHING",
                (entity_type, entity_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
        self.log_history(
            entity_type,
            entity_id,
            [
                {
                    "field": f,
                    "old": None,
                    "new": v,
                    "source": ENRICHMENT_ORIGIN,
                    "run_id": prov.get("run_id"),
                    "provenance": prov,
                }
                for f, v in attrs.items()
            ],
            version=1,
        )
        self.invalidate(entity_type)
        return {"decision": "CREATED", "id": entity_id, "_status": 201}

    def _write_single_origin_row(
        self,
        cur: Any,
        tdef: dict[str, Any],
        entity_id: str,
        attrs: dict[str, Any],
        refs: dict[str, Any],
        inferred: list[str],
    ) -> None:
        """Destination row for a single-origin (enrichment) projection.

        Survivorship on one origin is the identity projection with two
        contract rules (D-48): an inferred value only fills a field nothing
        else fills (a single origin: only None columns qualify, so it stands),
        and reference columns take ER ids as sent. Columns the item doesn't
        mention stay NULL — the next batch run's survivorship (P21g-1)
        merges them with real-origin values under enrichment's lowest
        priority.
        """
        cols, vals = ["id"], [entity_id]
        inferred_cols = [
            f'"{f}"' for f in inferred if f in tdef["fields"] and (f in attrs or f in refs)
        ]
        for field_name, value in {**attrs, **refs}.items():
            if field_name not in tdef["fields"] and field_name not in tdef["reference_fields"]:
                continue
            cols.append(f'"{field_name}"')
            vals.append(value)
        table = tdef["table"]
        # ON CONFLICT DO UPDATE expressions refer to the existing row as
        # <relation>.col — the bare relation name, never schema-qualified.
        relation = table.split(".")[-1]
        col_sql = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        # D-48 rule 2: an INFERRED value only fills a field nothing else
        # fills — COALESCE keeps an existing (source) value on conflict.
        # Non-inferred enrichment values may write over (the next batch run
        # re-decides them under the origin priority anyway, D-46).
        updates = ", ".join(
            f'{c} = COALESCE("{relation}".{c}, EXCLUDED.{c})'
            if c in inferred_cols
            else f"{c} = EXCLUDED.{c}"
            for c in cols
            if c != "id"
        )
        cur.execute(
            f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT (id) DO UPDATE SET {updates}"
            if updates
            else f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING",
            tuple(vals),
        )

    def _create_extension(
        self, entity_type: str, tdef: dict[str, Any], item: dict[str, Any]
    ) -> dict[str, Any]:
        """P21j ledger-first protocol (D-62 condition 3, §2.23 [ID1]):

        1. Postgres transaction: entity_map + er_entity_version + outbox
           reservation COMMIT. A crash here leaves a reserved id, never a
           second one.
        2. Mongo upsert keyed by _id = the reserved id — replay-safe by
           construction.
        3. Ledger commit: outbox row -> written; history rows.

        A crash between 1 and 2 (acceptance 16) replays: same key -> same id
        (step 1 is idempotent through the idempotency layer), and the sweep
        re-upserts the doc from the ledger — at-least-once delivery with
        idempotent effects is exactly-once effects.
        """
        if self.mongo_db is None:
            raise MongoUnavailable("extension store unreachable — unified types unaffected")
        attrs = {k: v for k, v in (item.get("attributes") or {}).items() if v is not None}
        refs = item.get("references") or {}
        prov = item.get("provenance") or {}
        key = str(item.get("idempotency_key") or "") or uuid4hex()
        record_id = f"{ENRICHMENT_ORIGIN}:k:{key}"
        entity_id = new_id(entity_type, record_id)
        coll = (tdef.get("collection") or "").split(".")[-1] or entity_type
        s = self.conn._er_schema
        # step 1 — the reservation commits before anything touches Mongo.
        self.conn.execute_sql(
            f'INSERT INTO "{s}".er_extension_outbox (entity_type, id, payload, state) '
            "VALUES (%s, %s, %s::jsonb, 'reserved') ON CONFLICT (entity_type, id) DO NOTHING",
            (
                entity_type,
                entity_id,
                json.dumps(
                    {"attributes": attrs, "references": refs, "provenance": prov}, default=str
                ),
            ),
        )
        self.conn.execute_sql(
            f'INSERT INTO "{self.conn._schema}"."merge_engine_entity_map" '
            "(entity_type, record_id, cluster_id, first_seen_run, updated_at) "
            "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (entity_type, record_id) DO UPDATE "
            "SET cluster_id = EXCLUDED.cluster_id, updated_at = now()",
            (entity_type, record_id, entity_id, str(prov.get("run_id") or "")),
        )
        self.conn.execute_sql(
            f'INSERT INTO "{s}".er_entity_version (entity_type, id, version, updated_at) '
            "VALUES (%s, %s, 1, now()) ON CONFLICT (entity_type, id) DO NOTHING",
            (entity_type, entity_id),
        )
        # step 2 — keyed-by-id upsert (never insert: replay must not double).
        doc = {"_id": entity_id, "_version": 1, **attrs, **refs}
        self._mongo_coll(coll).replace_one({"_id": entity_id}, doc, upsert=True)
        # step 3 — ledger last.
        self.conn.execute_sql(
            f'UPDATE "{s}".er_extension_outbox SET state = %s, written_at = now() '
            "WHERE entity_type = %s AND id = %s",
            ("written", entity_type, entity_id),
        )
        self.log_history(
            entity_type,
            entity_id,
            [
                {"field": f, "old": None, "new": v, "source": ENRICHMENT_ORIGIN, "provenance": prov}
                for f, v in attrs.items()
            ],
            version=1,
        )
        return {"decision": "CREATED", "id": entity_id, "_status": 201}

    def sweep_extension_outbox(self) -> int:
        """Reconciliation sweep (the load-bearing safety net, §2.23 [ID1] —
        never disable it for performance): every reservation older than the
        grace period is re-upserted from the ledger. Called periodically by
        the service and on start-up; a reserved-never-written row older than
        grace is an alarm condition, and this is the action it triggers."""
        s = self.conn._er_schema
        stale = self.conn.execute_sql(
            f'SELECT entity_type, id, payload FROM "{s}".er_extension_outbox '
            "WHERE state = 'reserved' AND created_at < now() - make_interval(secs => %s)",
            (self.sweep_grace_seconds,),
        )
        swept = 0
        for entity_type, entity_id, payload in stale:
            tdef = self.type_def(entity_type)
            if tdef is None or self.mongo_db is None:
                continue
            coll = (tdef.get("collection") or "").split(".")[-1] or entity_type
            doc = {
                "_id": entity_id,
                "_version": 1,
                **(payload.get("attributes") or {}),
                **(payload.get("references") or {}),
            }
            try:
                self._mongo_coll(coll).replace_one({"_id": entity_id}, doc, upsert=True)
            except Exception as exc:
                logger.error("Outbox sweep could not reach Mongo for %s: %s", entity_id, exc)
                continue
            self.conn.execute_sql(
                f'UPDATE "{s}".er_extension_outbox SET state = %s, written_at = now() '
                "WHERE entity_type = %s AND id = %s",
                ("written", entity_type, entity_id),
            )
            swept += 1
        if swept:
            logger.warning(
                "Outbox sweep replayed %d reserved-never-written create(s) from the ledger.", swept
            )
        return swept

    # ── PATCH (P21f) ────────────────────────────────────────────────────

    def patch_entity(
        self,
        entity_type: str,
        entity_id: str,
        body: dict[str, Any],
        if_match: int | None,
    ) -> dict[str, Any]:
        """Relaxed PATCH (rev. c): the client may send values that differ from
        ER's; survivorship decides (enrichment lowest priority, D-46) and the
        outcome is recorded in history either way — accepted or
        LOWER_PRIORITY_REJECTED (acceptance 17). If-Match stale -> 412."""
        tdef = self.type_def(entity_type)
        if tdef is None:
            raise ErError(404, "NOT_FOUND", f"entity type '{entity_type}' is in neither store")
        set_fields = body.get("set") or {}
        bad = {k: "unknown" for k in set_fields if k not in tdef["fields"]}
        for r in body.get("references") or {}:
            if r not in tdef["reference_fields"]:
                bad[r] = "unknown"
        aliases = body.get("add_aliases")
        if aliases and "aliases" not in tdef["fields"]:
            bad["add_aliases"] = "this type has no aliases field"
        if bad:
            raise ErError(422, "VALIDATION_FAILED", "PATCH validation failed", bad)
        sid, redirected_from = self.resolve_id(entity_type, entity_id)
        current = self._current_version(entity_type, sid)
        if if_match is not None and if_match != current:
            raise ErError(412, "VERSION_CONFLICT", f"current version is {current}", {})
        record_id = f"{ENRICHMENT_ORIGIN}:{uuid4hex()}"
        prov = body.get("provenance") or {}
        s = self.conn._er_schema
        inferred = set(body.get("inferred_fields") or [])
        if tdef["store"] == "enrichment":
            if self.mongo_db is None:
                raise MongoUnavailable("extension store unreachable")
            coll = (tdef.get("collection") or "").split(".")[-1] or entity_type
            try:
                doc = self._mongo_coll(coll).find_one({"_id": sid})
            except Exception as exc:
                raise MongoUnavailable(f"extension store read failed: {exc}") from exc
            if doc is None:
                raise ErError(404, "NOT_FOUND", f"entity '{sid}' was never issued")
            history, stored = [], {}
            for f, value in set_fields.items():
                old = doc.get(f)
                if old is None and f not in inferred:
                    stored[f] = value
                    history.append(
                        {"field": f, "old": None, "new": value, "source": ENRICHMENT_ORIGIN}
                    )
                elif old is None and f in inferred:
                    stored[f] = value
                    history.append(
                        {
                            "field": f,
                            "old": None,
                            "new": value,
                            "source": ENRICHMENT_ORIGIN,
                            "reason": "INFERRED_FILL_EMPTY",
                        }
                    )
                else:
                    # same store: the client's own values are the same class
                    # of origin; last-writer-wins on EMPTY-fill only — a
                    # differing non-empty value is rejected with history
                    # (their §5.6 table: ER's priority rules decide).
                    history.append(
                        {
                            "field": f,
                            "old": old,
                            "new": old,
                            "attempted": value,
                            "source": ENRICHMENT_ORIGIN,
                            "reason": "LOWER_PRIORITY_REJECTED",
                        }
                    )
            for f, value in (body.get("references") or {}).items():
                stored[f] = value
                history.append(
                    {"field": f, "old": doc.get(f), "new": value, "source": ENRICHMENT_ORIGIN}
                )
            if aliases:
                # add_aliases was validated against the type's fields above;
                # without this branch it would validate and then silently do
                # nothing. Merge into the doc's list, de-duplicated, order
                # preserved (the ledger entry below carries the full request).
                merged_aliases = list(doc.get("aliases") or [])
                added = [a for a in aliases if a not in merged_aliases]
                if added:
                    stored["aliases"] = merged_aliases + added
                    history.append(
                        {
                            "field": "aliases",
                            "old": merged_aliases,
                            "new": merged_aliases + added,
                            "source": ENRICHMENT_ORIGIN,
                        }
                    )
            version = int(doc.get("_version", current)) + 1
            if stored or body.get("references") or aliases:
                self._mongo_coll(coll).update_one(
                    {"_id": sid}, {"$set": {**stored, "_version": version}}
                )
            self.conn.execute_sql(
                f'INSERT INTO "{s}".er_enrichment_record (record_id, entity_type, attributes, '
                '"references", inferred_fields, extra, source_text, provenance) '
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, NULL, %s::jsonb)",
                (
                    record_id,
                    entity_type,
                    json.dumps(set_fields, default=str),
                    json.dumps(body.get("references") or {}, default=str),
                    list(inferred),
                    json.dumps(body.get("extra") or {}, default=str),
                    json.dumps(prov, default=str),
                ),
            )
            self.conn.execute_sql(
                f'INSERT INTO "{self.conn._schema}"."merge_engine_entity_map" '
                "(entity_type, record_id, cluster_id, first_seen_run, updated_at) "
                "VALUES (%s, %s, %s, %s, now()) ON CONFLICT DO NOTHING",
                (entity_type, record_id, sid, str(prov.get("run_id") or "")),
            )
            self.bump_version(entity_type, sid, version)
            self.log_history(entity_type, sid, history, version=version)
            entity = self.get_entity(entity_type, sid)
            entity["redirected_from"] = redirected_from
            entity["_status"] = 200
            return entity
        # unified PATCH: the enrichment record is the durable input; the
        # destination row is recomputed as THIS cluster's survivorship
        # projection — enrichment loses to any non-null value already in the
        # row (its priority, D-46), fills empty fields otherwise (D-48 for
        # inferred), and every outcome is a history row (D-64).
        conn = self.conn._ensure_connection()
        table = tdef["table"]
        history = []
        with self.conn.atomic():
            cur = conn.cursor()
            cur.execute(
                f'INSERT INTO "{s}".er_enrichment_record (record_id, entity_type, attributes, '
                '"references", inferred_fields, extra, source_text, provenance) '
                "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, NULL, %s::jsonb)",
                (
                    record_id,
                    entity_type,
                    json.dumps(set_fields, default=str),
                    json.dumps(body.get("references") or {}, default=str),
                    list(inferred),
                    json.dumps(body.get("extra") or {}, default=str),
                    json.dumps(prov, default=str),
                ),
            )
            cur.execute(
                f'INSERT INTO "{self.conn._schema}"."merge_engine_entity_map" '
                "(entity_type, record_id, cluster_id, first_seen_run, updated_at) "
                "VALUES (%s, %s, %s, %s, now()) ON CONFLICT DO NOTHING",
                (entity_type, record_id, sid, str(prov.get("run_id") or "")),
            )
            cur.execute(f"SELECT * FROM {table} WHERE id = %s", (sid,))
            existing_cols = [d[0] for d in cur.description]
            existing = dict(zip(existing_cols, cur.fetchone(), strict=False))
            sets: list[str] = []
            params: list[Any] = []
            for f, value in set_fields.items():
                if f not in tdef["fields"]:
                    continue
                old = existing.get(f)
                if old is None:
                    sets.append(f'"{f}" = %s')
                    params.append(value)
                    history.append(
                        {"field": f, "old": None, "new": value, "source": ENRICHMENT_ORIGIN}
                    )
                else:
                    history.append(
                        {
                            "field": f,
                            "old": old,
                            "new": old,
                            "attempted": value,
                            "source": ENRICHMENT_ORIGIN,
                            "reason": "LOWER_PRIORITY_REJECTED",
                        }
                    )
            for f, value in (body.get("references") or {}).items():
                if f in tdef["reference_fields"]:
                    sets.append(f'"{f}" = %s')
                    params.append(value)
                    history.append(
                        {
                            "field": f,
                            "old": existing.get(f),
                            "new": value,
                            "source": ENRICHMENT_ORIGIN,
                        }
                    )
            version = current + 1
            if sets:
                cur.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = %s", (*params, sid))
            cur.execute(
                f'INSERT INTO "{s}".er_entity_version (entity_type, id, version, updated_at) '
                "VALUES (%s, %s, %s, now()) ON CONFLICT (entity_type, id) DO UPDATE "
                "SET version = EXCLUDED.version, updated_at = now()",
                (entity_type, sid, version),
            )
        self.log_history(entity_type, sid, history, version=version)
        self.invalidate(entity_type)
        entity = self.get_entity(entity_type, sid)
        entity["redirected_from"] = redirected_from
        entity["_status"] = 200
        return entity

    def _current_version(self, entity_type: str, entity_id: str) -> int:
        rows = self.conn.execute_sql(
            f'SELECT version FROM "{self.conn._er_schema}".er_entity_version '
            "WHERE entity_type = %s AND id = %s",
            (entity_type, entity_id),
        )
        return int(rows[0][0]) if rows else 1

    def bump_version(self, entity_type: str, entity_id: str, version: int) -> None:
        self.conn.execute_sql(
            f'INSERT INTO "{self.conn._er_schema}".er_entity_version '
            "(entity_type, id, version, updated_at) VALUES (%s, %s, %s, now()) "
            "ON CONFLICT (entity_type, id) DO UPDATE SET version = EXCLUDED.version, "
            "updated_at = now()",
            (entity_type, entity_id, version),
        )

    # ── history writer (P21k) ───────────────────────────────────────────

    def log_history(
        self,
        entity_type: str,
        entity_id: str,
        events: list[dict[str, Any]],
        version: int | None = None,
    ) -> int:
        """Append field-level events (D-64). ``events`` carry field/old/new/
        source/reason/attempted/run_id/provenance; version defaults to the
        entity's current one. COPY-batched by the batch hook (load path); the
        API path is one transaction's worth of rows."""
        if not events:
            return 0
        if version is None:
            version = self._current_version(entity_type, entity_id)
        s = self.conn._er_schema
        conn = self.conn._ensure_connection()
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                f'INSERT INTO "{s}".er_field_history '
                "(entity_type, id, version, field, old, new, source, reason, attempted, "
                "run_id, provenance) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        entity_type,
                        entity_id,
                        version,
                        e["field"],
                        json.dumps(e.get("old"), default=str),
                        json.dumps(e.get("new"), default=str),
                        e.get("source"),
                        e.get("reason"),
                        json.dumps(e.get("attempted"), default=str) if "attempted" in e else None,
                        e.get("run_id"),
                        json.dumps(e.get("provenance"), default=str)
                        if e.get("provenance")
                        else None,
                    )
                    for e in events
                ],
            )
        conn.commit()
        return len(events)

    # ── review queue (ours, not enrichment's — D-69) ───────────────────

    def open_review(
        self, entity_type: str, item: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> str:
        ticket = new_ticket()
        self.conn.execute_sql(
            f'INSERT INTO "{self.conn._er_schema}".er_review '
            "(ticket, entity_type, item, candidates, status) "
            "VALUES (%s, %s, %s::jsonb, %s::jsonb, 'AMBIGUOUS')",
            (
                ticket,
                entity_type,
                json.dumps(item, default=str),
                json.dumps(candidates, default=str),
            ),
        )
        return ticket

    def get_review(self, ticket: str) -> dict[str, Any]:
        rows = self.conn.execute_sql(
            f'SELECT status, decision, decided_id FROM "{self.conn._er_schema}".er_review '
            "WHERE ticket = %s",
            (ticket,),
        )
        if not rows:
            raise ErError(404, "NOT_FOUND", f"no ticket '{ticket}'")
        status, decision, decided_id = rows[0]
        if status == "DONE":
            return {"status": "DONE", "decision": decision, "id": decided_id}
        return {"status": "PENDING"}


class WritePaused(Exception):
    """A batch load holds the write lock — writes answer 503 + Retry-After
    (C7/D-49); reads and search stay up."""


def uuid4hex() -> str:
    return secrets.token_hex(12)


class MongoUnavailable(Exception):
    """Their Mongo is down — extension-type operations answer 503, the
    unified store is unaffected (D-62 condition 2, failure isolation)."""


# ── write path: resolve / PATCH / create (P21e, P21f, P21j, P21l) ──────────


def _lock_key(*parts: str) -> int:
    """A stable bigint advisory key from names (never a magic number)."""
    import zlib

    return zlib.crc32("\x1f".join(parts).encode()) & 0x7FFFFFFF


WRITE_LOCK_KEY = _lock_key("merge-engine", "load-write-lock")


# ── helpers used across the module and by the routes ────────────────────────


def _qualify(table: str, schema: str = "public") -> str:
    return table if "." in table else f"{schema}.{table}"


def data_projection(rec: dict[str, Any], cols: list[str]) -> dict[str, Any]:
    return {c: rec[c] for c in cols if rec.get(c) is not None}


def differing_fields(candidates: list[dict[str, Any]]) -> list[str]:
    """Fields whose values are not shared by every candidate (contract §5.3):
    the evidence a client can still go and find."""
    if len(candidates) < 2:
        return []
    attrs = [c["attributes"] for c in candidates]
    keys = {k for a in attrs for k in a if k != "id"}
    out = []
    for k in sorted(keys):
        vals = {json.dumps(a.get(k), sort_keys=True, default=str) for a in attrs}
        if len(vals) > 1:
            out.append(k)
    return out


def matched_fields(item: dict[str, Any], stored: dict[str, Any]) -> list[str]:
    out = []
    for k, v in (item.get("attributes") or {}).items():
        if v is not None and stored.get(k) is not None and _normalize(v) == _normalize(stored[k]):
            out.append(k)
    return out


def thresholds_from_model(model: MatchModel) -> dict[str, float] | None:
    """The bars the fitting run actually used (C6/D-50: 'thresholds = the
    bars the last run used'). The batch fits against _AUTO_MATCH/_AUTO_EXCLUDE
    defaults; if the stored params carry explicit bands, use those."""
    m = getattr(model, "thresholds", None)
    if isinstance(m, dict) and "auto_match" in m and "review" in m:
        return {"auto_match": float(m["auto_match"]), "review": float(m["review"])}
    return {"auto_match": _AUTO_MATCH, "review": _AUTO_EXCLUDE}


def new_id(entity_type: str, record_id: str) -> str:
    """The SAME id function the batch uses for a singleton cluster
    (deterministic_uuid over (entity_type, member ids), resolve_entities'
    _assign_cluster_id fallback path) — the property P21e needs: the next
    batch run reproduces a service-issued id."""
    return deterministic_uuid(entity_type, record_id)


def build_entity_type_catalog(connector: Any) -> dict[str, dict[str, Any]]:
    """P21b's snapshot source: read_destination_schemas + destination FKs →
    contract type entries. Identity fields prefer the fitted model's field
    set (what the last run actually compared — P21b), else the name-ish/
    identity columns. required = NOT NULL non-key columns; thresholds come
    from er_model when a row exists, else the provisional bars."""
    from src.orchestrator.nodes.load_data import read_destination_schemas

    schemas = read_destination_schemas(connector)
    catalog: dict[str, dict[str, Any]] = {}
    for table, schema in schemas.items():
        # ER's own bookkeeping tables are not entity types (in the isolated
        # live-test schema they share a schema with the data tables).
        if table.startswith("er_") or table.startswith("merge_engine_"):
            continue
        cols = schema.get("columns") or []
        pk = schema.get("primary_key")
        try:
            fks = connector.get_foreign_keys(table)
        except Exception:
            fks = {}
        entity_type = table  # destination table name == contract entity_type
        fields: dict[str, str] = {}
        identity: list[str] = []
        required: list[str] = []
        refs: dict[str, str] = {}
        for c in cols:
            name = c["name"]
            if name == pk:
                continue
            fields[name] = sql_type_to_contract(c.get("type", ""))
            if not c.get("nullable", True):
                required.append(name)
            if name in fks:
                refs[name] = fks[name]  # referenced table (see routes: mapped to type)
            else:
                identity.append(name)
        model_row = connector.execute_sql(
            f'SELECT params FROM "{connector._er_schema}".er_model WHERE entity_type = %s',
            (entity_type,),
        )
        if model_row:
            fitted = (model_row[0][0] or {}).get("fields") or {}
            compared = [f for f in fitted if f in fields]
            if compared:
                identity = compared
        catalog[entity_type] = {
            "table": _qualify(table, getattr(connector, "_schema", "public")),
            "fields": fields,
            "identity_fields": identity,
            "required_fields": required,
            "reference_fields": {k: v for k, v in refs.items()},
            "table_columns": cols,
        }
    return catalog


def sql_type_to_contract(sql_type: str) -> str:
    t = (sql_type or "").upper()
    if "[]" in t or t.startswith("_"):
        return "string[]"
    if "TEXT" in t or "CHAR" in t or "UUID" in t or "JSON" in t or "ENUM" in t:
        return "string"
    if "INT" in t:
        return "integer"
    if any(k in t for k in ("NUMERIC", "DECIMAL", "REAL", "DOUBLE", "FLOAT")):
        return "number"
    if "BOOL" in t:
        return "boolean"
    if "TIMESTAMP" in t or t.split()[0] == "DATE":
        return "datetime" if "TIMESTAMP" in t else "date"
    if "TIME" in t:
        return "datetime"
    return "string"


def new_ticket() -> str:
    return f"rev_{secrets.token_hex(8)}"


__all__ = [
    "ErError",
    "ErService",
    "MongoUnavailable",
    "build_entity_type_catalog",
    "clamp_item_text",
    "deserialize_model",
    "differing_fields",
    "matched_fields",
    "new_id",
    "new_ticket",
    "request_sha256",
    "serialize_model",
    "utcnow",
]
