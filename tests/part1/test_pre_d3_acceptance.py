"""PRE-D3 acceptance (LOCAL_VERIFY_REPORT Pre-existing): "Event loop is closed" on the first
JSON-mode LLM call must not disable JSON mode.

Derived from the report's Evidence text and the call-site description only, before any fix.
Defect: ``LLMClient.call`` treats *any* exception from a ``json_object`` call as "the endpoint
rejects response_format", sets ``_json_mode_unsupported`` for the life of the client and
retries without JSON mode. A transport/runtime error (a stale cached async httpx client from a
finished event loop, a connect error, a timeout) is not a rejection.

Targets-the-defect (fail on the old code): test_transport_error_*, test_transient_*,
test_real_second_event_loop_*.  Guards (pass on old code, must keep passing):
test_genuine_rejection_*, test_persistent_error_raises, test_quota_*, test_no_new_numeric_*.
"""

from __future__ import annotations

import ast
import asyncio
import collections
import http.server
import json
import subprocess
import threading
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage

from src.llm.client import LLMClient

ROOT = Path(__file__).resolve().parents[2]
BASE = "9ca3b9a6afce387c07ebb2200614084d7b541723"  # HEAD when PRE-D3 derive started
OK = '{"ok": true}'
_REQ = httpx.Request("POST", "http://stub.invalid/v1/chat/completions")


def _status_error(cls: type[openai.APIStatusError], code: int, msg: str) -> Exception:
    return cls(msg, response=httpx.Response(code, request=_REQ), body={"error": {"message": msg}})


REJECTIONS = {
    "bad_request_400": lambda: _status_error(
        openai.BadRequestError, 400, "response_format json_object is not supported by this model"
    ),
    "unprocessable_422": lambda: _status_error(
        openai.UnprocessableEntityError, 422, "unknown field response_format"
    ),
    "plain_status_error_400": lambda: _status_error(
        openai.APIStatusError, 400, "Invalid parameter: response_format"
    ),
}
TRANSIENT = {
    "event_loop_closed": lambda: RuntimeError("Event loop is closed"),
    "httpx_connect_error": lambda: httpx.ConnectError("All connection attempts failed"),
    "httpx_read_timeout": lambda: httpx.ReadTimeout("timed out"),
    "openai_connection_error": lambda: openai.APIConnectionError(request=_REQ),
    "openai_timeout": lambda: openai.APITimeoutError(request=_REQ),
    "asyncio_timeout": lambda: TimeoutError("deadline"),
}
# Non-rejection HTTP errors that must not be read as "response_format unsupported".
NON_REJECTION_STATUS = {
    "server_error_500": lambda: _status_error(openai.InternalServerError, 500, "upstream failure"),
    "auth_error_401": lambda: _status_error(openai.AuthenticationError, 401, "bad key"),
}


class _Stub:
    """Chat model stand-in. Records, per invocation, whether JSON mode was requested."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[bool] = []  # json mode requested?
        self.models_built = 0

    def _run(self, json_mode: bool) -> AIMessage:
        self.calls.append(json_mode)
        assert len(self.calls) <= 12, "runaway retry loop"
        step = self.script.pop(0) if self.script else OK
        if isinstance(step, BaseException):
            raise step
        return AIMessage(content=step)

    async def ainvoke(self, messages: Any, config: Any = None) -> AIMessage:
        return self._run(False)

    def bind(self, **kw: Any) -> Any:
        stub = self

        class Bound:
            async def ainvoke(self, messages: Any, config: Any = None) -> AIMessage:
                return stub._run(kw.get("response_format") == {"type": "json_object"})

        return Bound()


def _client(script: list[Any], *, chain: tuple[str, ...] = ()) -> tuple[LLMClient, _Stub]:
    c = LLMClient(
        {
            "provider": "openai",
            "model": "m1",
            "api_key": "k",
            "fallback_models": list(chain),
            "cache_enabled": False,
            "persistent_cache_enabled": False,
        }
    )
    stub = _Stub(script)

    def _get() -> _Stub:
        stub.models_built += 1
        return stub

    c._get_model = _get  # type: ignore[method-assign]
    return c, stub


async def _try(c: LLMClient, prompt: str) -> Any:
    try:
        return await c.call("sys", prompt, json_mode=True)
    except Exception as exc:
        return exc


# --- (a) transport/runtime error must not disable JSON mode -----------------------------------


@pytest.mark.parametrize("name", [*TRANSIENT, *NON_REJECTION_STATUS])
def test_transport_error_does_not_set_json_mode_unsupported(name: str) -> None:
    err = {**TRANSIENT, **NON_REJECTION_STATUS}[name]()
    c, stub = _client([err])

    async def go() -> None:
        await _try(c, "first")
        assert c._json_mode_unsupported is False, (
            f"{name} was misread as a response_format rejection"
        )
        n = len(stub.calls)
        assert await c.call("sys", "second", json_mode=True) == OK
        assert stub.calls[n:] == [True], "a later call must still request JSON mode"

    asyncio.run(go())


# --- (b) genuine rejection keeps today's behaviour --------------------------------------------


@pytest.mark.parametrize("name", list(REJECTIONS))
def test_genuine_rejection_sets_flag_and_succeeds_without_json_mode(name: str) -> None:
    c, stub = _client([REJECTIONS[name]()])

    async def go() -> None:
        assert await c.call("sys", "first", json_mode=True) == OK
        assert c._json_mode_unsupported is True
        assert stub.calls == [True, False], "retry must drop JSON mode"
        assert await c.call("sys", "second", json_mode=True) == OK
        assert stub.calls[2:] == [False], "after a real rejection JSON mode stays off"

    asyncio.run(go())


def test_genuine_rejection_via_call_json_returns_parsed_dict() -> None:
    c, _ = _client([REJECTIONS["bad_request_400"]()])
    assert asyncio.run(c.call_json("sys", "q")) == {"ok": True}


# --- (c) transient then success: answer returned, JSON mode kept ------------------------------


@pytest.mark.parametrize("name", list(TRANSIENT))
def test_transient_error_then_success_returns_answer_and_keeps_json_mode(name: str) -> None:
    c, stub = _client([TRANSIENT[name](), '{"v": 7}'])

    async def go() -> None:
        assert await c.call_json("sys", "first") == {"v": 7}, "the call must not be lost"
        assert c._json_mode_unsupported is False
        n = len(stub.calls)
        assert await c.call_json("sys", "second") == {"ok": True}
        assert stub.calls[n:] == [True]

    asyncio.run(go())


# --- (d) persistent non-rejection error still raises; quota still advances the chain ----------


@pytest.mark.parametrize("name", ["event_loop_closed", "httpx_connect_error"])
def test_persistent_error_raises(name: str) -> None:
    """Guard: callers keep the heuristic fallback, so a hard failure must surface."""
    c, stub = _client([TRANSIENT[name]() for _ in range(12)])
    with pytest.raises(Exception) as ei:
        asyncio.run(c.call("sys", "q", json_mode=True))
    assert not isinstance(ei.value, AssertionError), "retried without bound"
    assert stub.calls, "the model was never called"


@pytest.mark.parametrize("name", ["event_loop_closed", "httpx_connect_error"])
def test_persistent_error_does_not_set_json_mode_unsupported(name: str) -> None:
    c, _ = _client([TRANSIENT[name]() for _ in range(12)])
    with pytest.raises(Exception):
        asyncio.run(c.call("sys", "q", json_mode=True))
    assert c._json_mode_unsupported is False


def test_persistent_error_raises_through_call_json() -> None:
    c, _ = _client([RuntimeError("Event loop is closed") for _ in range(12)])
    with pytest.raises(RuntimeError, match="Event loop is closed"):
        asyncio.run(c.call_json("sys", "q"))


def _quota_client(chain: tuple[str, ...], failing: set[str]) -> tuple[LLMClient, _Stub]:
    """Every call made while ``model_name`` is in ``failing`` raises a quota error."""
    c, stub = _client([], chain=chain)
    stub.script = _QuotaScript(c, failing)  # type: ignore[assignment]
    return c, stub


class _QuotaScript(list):  # type: ignore[type-arg]
    def __init__(self, client: LLMClient, failing: set[str]) -> None:
        super().__init__([0])  # non-empty so _Stub.pop(0) is consulted
        self.client, self.failing = client, failing

    def pop(self, index: int = 0) -> Any:
        if self.client.model_name in self.failing:
            return RuntimeError("Error code: 429 - insufficient_quota")
        return OK


@pytest.mark.parametrize("json_mode", [False, True])
def test_quota_error_advances_model_chain(json_mode: bool) -> None:
    """Guard: a quota error on m1 moves to m2 and returns m2's answer (as today)."""
    c, _ = _quota_client(("m2",), {"m1"})
    assert asyncio.run(c.call("sys", "q", json_mode=json_mode)) == OK
    assert c.model_name == "m2" and c._model_chain_idx == 1


def test_quota_error_does_not_set_json_mode_unsupported() -> None:
    c, stub = _quota_client(("m2",), {"m1"})
    assert asyncio.run(c.call("sys", "q", json_mode=True)) == OK
    assert c._json_mode_unsupported is False, "a quota error says nothing about response_format"
    assert stub.calls[-1] is True, "the fallback model is asked for JSON mode too"


def test_quota_error_with_exhausted_chain_raises() -> None:
    c, _ = _quota_client(("m2",), {"m1", "m2"})
    with pytest.raises(RuntimeError, match="insufficient_quota"):
        asyncio.run(c.call("sys", "q", json_mode=True))
    assert c.model_name == "m2", "advanced to the end of the chain before giving up"


# --- (e) real reproduction: two sequential event loops, real ChatOpenAI -----------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, so the pool holds a connection between loops
    bodies: list[dict[str, Any]] = []

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length", "0"))
        self.bodies.append(json.loads(self.rfile.read(n) or b"{}"))
        body = json.dumps(
            {
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m1",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": OK},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: Any) -> None:  # silence
        pass


def test_real_second_event_loop_first_call_keeps_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding recorded either way: see the module docstring of the acceptance note.

    Loop 1 and loop 2 each run one ``call_json`` through ONE client (as the pipeline does across
    graph nodes). Neither may flip ``_json_mode_unsupported``; both requests must carry
    ``response_format=json_object`` and loop 2 must get its answer.
    """
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    _Handler.bodies = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = LLMClient(
            {
                "provider": "openai",
                "model": "m1",
                "api_key": "k",
                "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                "timeout": 5,
                "cache_enabled": False,
                "persistent_cache_enabled": False,
            }
        )
        assert asyncio.run(c.call_json("sys", "loop one")) == {"ok": True}
        flag1 = c._json_mode_unsupported
        assert asyncio.run(c.call_json("sys", "loop two")) == {"ok": True}
        flag2 = c._json_mode_unsupported
    finally:
        srv.shutdown()
        srv.server_close()
    fmts = [b.get("response_format") for b in _Handler.bodies]
    assert flag1 is False and flag2 is False, f"JSON mode disabled by loop reuse ({flag1}, {flag2})"
    assert fmts == [{"type": "json_object"}] * 2, fmts


# --- (f) plan 2.4: no new numeric literal in the client.py diff -------------------------------

# 0/1 are structural (index, step); 400/422 are the HTTP protocol's "client rejected the request"
# statuses, not fitted values. Anything else (a sleep, a retry count, a threshold) is new.
_ALLOWED = {0, 1, 400, 422}


def _numbers(src: str) -> collections.Counter[Any]:
    return collections.Counter(
        n.value
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Constant)
        and isinstance(n.value, int | float)
        and not isinstance(n.value, bool)
    )


def test_no_new_numeric_literal_in_client_diff() -> None:
    old = subprocess.run(
        ["git", "show", f"{BASE}:src/llm/client.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    new = (ROOT / "src/llm/client.py").read_text()
    added = _numbers(new) - _numbers(old)
    fitted = {v: k for v, k in added.items() if v not in _ALLOWED}
    assert not fitted, f"new numeric literals in src/llm/client.py (value: extra count): {fitted}"
