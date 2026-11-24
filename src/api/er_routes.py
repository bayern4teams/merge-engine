"""/v1 ER-service routes (P21h) — the contract surface of docs/er_contract_v13.

Mount is deliberately dumb: auth, JSON plumbing, status codes. All
mechanics live in :mod:`src.orchestrator.er_service` so the live acceptance
tests can drive the same code without HTTP.

Auth (contract §3): every request carries ``X-API-Key``; missing/wrong → 401
before any DB touch. The key comes ONLY from the environment
(``ER_API_KEY``, .env-expansion like every other secret — it never enters a
tracked file). The matching read-only DB role snippet lives in
``docs/er_readonly_role.sql``; the service itself connects as the app role
because it WRITES (idempotency, ledger, history).
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from src.orchestrator.er_service import ErError, ErService, MongoUnavailable, WritePaused

logger = logging.getLogger("merge-engine.er_routes")

# Built once per process; reset via reset_service() for tests.
_service: ErService | None = None


def get_service() -> ErService:
    global _service
    if _service is None:
        _service = build_service()
    return _service


def reset_service() -> None:
    global _service
    _service = None


def build_service() -> ErService:
    """Destination connector from the app config + their Mongo from env.

    Mongo absence is NOT an error at build time: D-62 isolation means a
    missing/unreachable extension store degrades only extension-type
    operations at request time. So ``ER_ENRICHMENT_MONGO_URI`` unset →
    service without extension capability.
    """
    from src.mcp_server.tools.load_data import _get_destination_connector

    connector = _get_destination_connector({})
    ensure = getattr(connector, "ensure_er_side_tables", None)
    if ensure is not None:
        ensure()

    mongo_db = None
    uri = os.environ.get("ER_ENRICHMENT_MONGO_URI", "")
    if uri:
        try:
            import pymongo

            client: Any = pymongo.MongoClient(uri, serverSelectionTimeoutMS=2000)
            db_name = os.environ.get("ER_ENRICHMENT_MONGO_DB", "enrichment_entities")
            mongo_db = client[db_name]
        except Exception as exc:
            logger.warning("extension Mongo unavailable at startup: %s", exc)
    return ErService(connector, mongo_db=mongo_db)


def _check_api_key(x_api_key: str | None) -> None:
    expected = os.environ.get("ER_API_KEY", "")
    if not expected:
        # Fail closed: a deployment that forgot the key must not ship an
        # open write endpoint to the network.
        raise HTTPException(status_code=503, detail="ER service not configured (ER_API_KEY)")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


def _service_errors() -> tuple[type[Exception], ...]:
    return (ErError,)


def _error_response(exc: ErError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body)


def build_er_router() -> APIRouter:
    router = APIRouter(prefix="/v1")

    def _guard(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            status, payload = fn(*args, **kwargs)
        except ErError as exc:
            return _error_response(exc)
        return JSONResponse(status_code=status, content=payload)

    @router.get("/health")
    def health(request: Request, x_api_key: str | None = Header(default=None)) -> Any:
        _check_api_key(x_api_key)
        return {"status": "ok"}

    @router.get("/entity-types")
    def entity_types(x_api_key: str | None = Header(default=None)) -> Any:
        _check_api_key(x_api_key)
        try:
            return get_service().entity_types_payload()
        except ErError as exc:
            return _error_response(exc)

    @router.put("/entity-types/{entity_type}")
    def register_type(
        entity_type: str,
        body: dict[str, Any],
        x_api_key: str | None = Header(default=None),
    ) -> Any:
        _check_api_key(x_api_key)
        return _guard(get_service().register_extension_type, entity_type, body)

    @router.post("/entities:search")
    def search(body: dict[str, Any], x_api_key: str | None = Header(default=None)) -> Any:
        _check_api_key(x_api_key)
        try:
            status, payload = get_service().run_search(body)
        except MongoUnavailable as exc:
            return JSONResponse(
                status_code=503,
                content={"error": "EXTENSION_STORE_DOWN", "message": str(exc)},
            )
        except ErError as exc:
            return _error_response(exc)
        return JSONResponse(status_code=status, content=payload)

    @router.post("/entities:resolve")
    def resolve(body: dict[str, Any], x_api_key: str | None = Header(default=None)) -> Any:
        _check_api_key(x_api_key)
        try:
            status, payload = get_service().run_resolve(body)
        except MongoUnavailable as exc:
            return JSONResponse(
                status_code=503,
                content={"error": "EXTENSION_STORE_DOWN", "message": str(exc)},
                headers={"Retry-After": "30"},
            )
        except ErError as exc:
            return _error_response(exc)
        # D-49: tell them WHEN to resend the whole batch.
        headers = {"Retry-After": "30"} if status == 503 else None
        return JSONResponse(status_code=status, content=payload, headers=headers)

    @router.get("/reviews/{ticket}")
    def review(ticket: str, x_api_key: str | None = Header(default=None)) -> Any:
        _check_api_key(x_api_key)
        try:
            return get_service().get_review(ticket)
        except ErError as exc:
            return _error_response(exc)

    @router.get("/entities/{entity_type}/{entity_id}")
    def get_entity(
        entity_type: str, entity_id: str, x_api_key: str | None = Header(default=None)
    ) -> Any:
        _check_api_key(x_api_key)
        try:
            return get_service().get_entity(entity_type, entity_id)
        except ErError as exc:
            return _error_response(exc)

    @router.get("/entities/{entity_type}/{entity_id}/history")
    def history(
        entity_type: str,
        entity_id: str,
        since_version: int | None = None,
        x_api_key: str | None = Header(default=None),
    ) -> Any:
        _check_api_key(x_api_key)
        try:
            return get_service().get_history(entity_type, entity_id, since_version)
        except ErError as exc:
            return _error_response(exc)

    @router.patch("/entities/{entity_type}/{entity_id}")
    def patch_entity(
        entity_type: str,
        entity_id: str,
        body: dict[str, Any],
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> Any:
        _check_api_key(x_api_key)
        raw_match = request.headers.get("if-match")
        if_match = None
        if raw_match is not None:
            try:
                if_match = int(raw_match.strip('"'))
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": "VALIDATION_FAILED", "message": "If-Match must be an int"},
                )
        service = get_service()
        # A PATCH is a write: same pause discipline as resolve (D-49).
        try:
            service.acquire_write_locks({entity_type})
        except WritePaused:
            return JSONResponse(
                status_code=503,
                content={"error": "LOAD_RUNNING", "message": "a batch load is running"},
                headers={"Retry-After": "30"},
            )
        try:
            try:
                result = service.patch_entity(entity_type, entity_id, body, if_match)
            except MongoUnavailable as exc:
                return JSONResponse(
                    status_code=503,
                    content={"error": "EXTENSION_STORE_DOWN", "message": str(exc)},
                    headers={"Retry-After": "30"},
                )
        except ErError as exc:
            return _error_response(exc)
        finally:
            service.release_write_locks()
        headers = {"ETag": f'"{result.get("version", 1)}"'}
        return JSONResponse(status_code=200, content=result, headers=headers)

    return router


__all__ = ["build_er_router", "build_service", "get_service", "reset_service"]
