"""PRE-D3 attack checks (commit 41bd7cb: per-event-loop chat model + own httpx client; only a
400/422 ``APIStatusError`` disables JSON mode).

Passing tests pin behaviour that was probed and found acceptable.  ``xfail(strict=True)`` tests are
real defects of the fix found by the attack: they pass CI today and turn red (XPASS) the moment the
defect is fixed, which is the cue to delete the marker.
"""

from __future__ import annotations

import asyncio
import gc
import os
import subprocess
import sys
import threading
import warnings
from collections.abc import Iterator
from typing import Any

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage

import src.llm.client as client_mod
from src.llm.client import LLMClient

_SERVER = r"""
import http.server, json
OK = '{"ok": true}'
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path.startswith("/redir"):
            self.send_response(307)
            self.send_header("Location", self.path.replace("/redir", "/v1"))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "m1",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": OK}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
print(s.server_port, flush=True)
s.serve_forever()
"""


@pytest.fixture(scope="module")
def port() -> Iterator[int]:
    """Stub OpenAI endpoint in a SUBPROCESS, so every socket in this process is a client socket."""
    p = subprocess.Popen([sys.executable, "-c", _SERVER], stdout=subprocess.PIPE, text=True)
    assert p.stdout is not None
    try:
        yield int(p.stdout.readline())
    finally:
        p.kill()
        p.wait()
        p.stdout.close()


def _real(port: int, path: str = "v1", **extra: Any) -> LLMClient:
    return LLMClient(
        {
            "provider": "openai",
            "model": "m1",
            "api_key": "k",
            "base_url": f"http://127.0.0.1:{port}/{path}",
            "timeout": 5,
            "cache_enabled": False,
            "persistent_cache_enabled": False,
            **extra,
        }
    )


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")


# ---- stub chat model (no network) ----------------------------------------------------------


class _Chat:
    built: list[_Chat] = []
    gate: Any = None  # optional hook(owner_thread_name) run inside the constructor

    def __init__(self, **kw: Any) -> None:
        self.kw = kw
        self.owner = threading.current_thread().name
        if _Chat.gate:
            _Chat.gate(self.owner)
        _Chat.built.append(self)

    async def ainvoke(self, messages: Any, config: Any = None) -> AIMessage:
        return AIMessage(content='{"ok": true}')

    def bind(self, **kw: Any) -> _Chat:
        return self


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch) -> type[_Chat]:
    _Chat.built = []
    _Chat.gate = None
    monkeypatch.setattr(client_mod, "ChatOpenAI", _Chat)
    monkeypatch.setattr(client_mod, "ChatAnthropic", _Chat)
    return _Chat


def _stub_client(provider: str = "openai", **extra: Any) -> LLMClient:
    return LLMClient(
        {
            "provider": provider,
            "model": "m1",
            "api_key": "k",
            "timeout": 5,
            "cache_enabled": False,
            "persistent_cache_enabled": False,
            **extra,
        }
    )


# ---- 1. a non-format 400 -------------------------------------------------------------------


def test_context_length_400_still_disables_json_mode_known_limitation(
    chat: type[_Chat], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not worse than before (any error used to disable it) but still a false positive: a 400
    that is not about response_format is read as a verdict on json_object for the client's
    lifetime.  Pinned so the limitation is visible; fixing it means sniffing the message/param."""
    req = httpx.Request("POST", "http://x/v1/chat/completions")
    err = openai.BadRequestError(
        "maximum context length exceeded",
        response=httpx.Response(400, request=req),
        body={"error": {"code": "context_length_exceeded"}},
    )
    c = _stub_client()
    calls = {"n": 0}

    async def boom(self: Any, messages: Any, config: Any = None) -> AIMessage:
        calls["n"] += 1
        raise err

    monkeypatch.setattr(_Chat, "ainvoke", boom)

    async def go() -> None:
        with pytest.raises(openai.BadRequestError):
            await c.call("s", "very long", json_mode=True)

    asyncio.run(go())
    assert calls["n"] == 2  # the doomed retry without JSON mode still happens
    assert c._json_mode_unsupported is True  # ...and the flag is set for good


# ---- 2. per-loop rebuild: cost and preserved state ------------------------------------------


def test_one_build_per_loop_not_per_call(chat: type[_Chat]) -> None:
    c = _stub_client()

    async def five() -> None:
        for _ in range(5):
            await c.call("s", "p", json_mode=True)

    asyncio.run(five())
    assert len(chat.built) == 1
    for _ in range(19):
        asyncio.run(five())
    assert len(chat.built) == 20  # one per asyncio.run: the price of the fix, not per call
    assert all("http_async_client" in m.kw for m in chat.built)
    assert len({id(m.kw["http_async_client"]) for m in chat.built}) == 20


def test_fallback_chain_index_and_flag_survive_rebuild(chat: type[_Chat]) -> None:
    c = _stub_client(fallback_models=["m2", "m3"])
    asyncio.run(c.call("s", "p", json_mode=True))
    assert c._advance_model() is True  # quota fallback happens mid-run
    c._json_mode_unsupported = True
    asyncio.run(c.call("s", "q", json_mode=True))  # new loop => rebuild
    assert c.model_name == "m2" and c._model_chain_idx == 1
    assert chat.built[-1].kw["model"] == "m2"  # the rebuild uses the advanced model, not m1
    assert c._json_mode_unsupported is True


def test_sync_build_then_loop_rebuild_and_in_flight_model_is_stable(chat: type[_Chat]) -> None:
    c = _stub_client()
    m0 = c._get_model()  # no running loop
    assert "http_async_client" not in m0.kw  # falls back to langchain's default client
    assert c._get_model() is m0

    async def go() -> None:
        m1 = c._get_model()
        assert m1 is not m0 and "http_async_client" in m1.kw
        assert c._get_model() is m1

    asyncio.run(go())


# ---- 3. unclosed httpx.AsyncClient per loop --------------------------------------------------


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc")
def test_200_sequential_loops_do_not_accumulate_sockets(port: int) -> None:
    def sockets() -> int:
        n = 0
        for f in os.listdir("/proc/self/fd"):
            try:
                n += os.readlink(f"/proc/self/fd/{f}").startswith("socket:")
            except OSError:
                pass
        return n

    c = _real(port)
    gc.collect()
    base, peak = sockets(), 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)  # one per loop is expected (see verdict)
        for i in range(200):
            assert asyncio.run(c.call_json("s", f"p{i}")) == {"ok": True}
            peak = max(peak, sockets())
        gc.collect()
    assert c._json_mode_unsupported is False
    assert sockets() <= base + 2, (base, sockets())
    # Bounded only because the cyclic GC reclaims the unclosed clients: far below one per loop.
    assert peak < base + 100, (base, peak)


# ---- 4. threads: two loops, one LLMClient -----------------------------------------------------


def test_in_flight_call_keeps_its_own_model_when_another_thread_rebuilds(
    chat: type[_Chat], monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _stub_client()
    started, release, seen = threading.Event(), threading.Event(), {}

    async def slow(self: _Chat, messages: Any, config: Any = None) -> AIMessage:
        seen["owner"] = self.owner
        started.set()
        await asyncio.get_running_loop().run_in_executor(None, release.wait, 5)
        return AIMessage(content='{"ok": true}')

    monkeypatch.setattr(_Chat, "ainvoke", slow)
    ta = threading.Thread(target=lambda: asyncio.run(c.call("s", "p", json_mode=True)), name="A")
    ta.start()
    assert started.wait(5)
    # A is blocked inside ainvoke on its model; B now swaps the shared slot.
    tb = threading.Thread(target=lambda: asyncio.run(_touch(c)), name="B")
    tb.start()
    tb.join(5)
    release.set()
    ta.join(5)
    assert seen["owner"] == "A"  # A's in-flight call used A's model, not B's replacement
    assert [m.owner for m in chat.built] == ["A", "B"]


async def _touch(c: LLMClient) -> Any:
    return c._get_model()


def test_concurrent_cold_builds_leave_model_bound_to_the_right_loop(chat: type[_Chat]) -> None:
    """Interleaving: B sets _model_loop=B and starts building; A runs a whole _get_model
    (_model_loop=A, _model=M_A); B finishes (_model=M_B).  State is (M_B, loop A): A's next
    _get_model() returns B's model, whose httpx client belongs to B's (soon closed) loop -- the
    exact PRE-D3 failure, reintroduced.  The window is the ChatOpenAI constructor."""
    c = _stub_client()
    b_in, a_done = threading.Event(), threading.Event()

    def gate(owner: str) -> None:
        if owner == "B":
            b_in.set()
            a_done.wait(5)

    chat.gate = gate
    b_done = threading.Event()
    out: dict[str, Any] = {}

    def thread_b() -> None:
        asyncio.run(_touch(c))
        b_done.set()

    def thread_a() -> None:
        async def go() -> None:
            b_in.wait(5)
            await _touch(c)
            a_done.set()
            await asyncio.get_running_loop().run_in_executor(None, b_done.wait, 5)
            out["again"] = c._get_model()

        asyncio.run(go())

    tb, ta = (
        threading.Thread(target=thread_b, name="B"),
        threading.Thread(target=thread_a, name="A"),
    )
    tb.start()
    ta.start()
    tb.join(10)
    ta.join(10)
    assert out["again"].owner == "A", f"A was handed {out['again'].owner}'s model"


def test_two_threads_alternating_rebuild_on_every_call_cost_note(chat: type[_Chat]) -> None:
    """Two long-lived loops (the workflow manager's background threads) sharing one client
    thrash the single model slot: every call rebuilds.  Measured, not judged -- see verdict."""
    c = _stub_client()
    loops, threads = [], []
    for name in ("A", "B"):
        lp = asyncio.new_event_loop()
        t = threading.Thread(target=lp.run_forever, name=name, daemon=True)
        t.start()
        loops.append(lp)
        threads.append(t)
    try:
        for i in range(10):
            lp = loops[i % 2]
            asyncio.run_coroutine_threadsafe(c.call("s", "p", json_mode=True), lp).result(5)
    finally:
        for lp in loops:
            lp.call_soon_threadsafe(lp.stop)
        for t in threads:
            t.join(5)
        for lp in loops:
            lp.close()
    assert len(chat.built) == 10


# ---- 5. timeout semantics ---------------------------------------------------------------------


def _client_timeout(c: LLMClient) -> Any:
    async def go() -> Any:
        t = c._get_model().async_client._client._client.timeout
        # openai ships its own httpx build: compare the four limits, not the class
        return httpx.Timeout(connect=t.connect, read=t.read, write=t.write, pool=t.pool)

    return asyncio.run(go())


def test_timeout_float_reaches_httpx_client() -> None:
    assert _client_timeout(_stub_client(timeout="7")) == httpx.Timeout(7.0)  # config str -> float


@pytest.mark.parametrize("value", [None, httpx.Timeout(3.0, connect=1.0)])
def test_timeout_none_and_httpx_timeout_objects_are_passed_through(value: Any) -> None:
    c = _stub_client()
    c.timeout = value  # not reachable via config (float(...)), but attribute is public
    got = _client_timeout(c)
    assert got == (httpx.Timeout(None) if value is None else value)


# ---- 6. anthropic untouched ---------------------------------------------------------------------


def test_anthropic_gets_no_httpx_client_and_same_kwargs(chat: type[_Chat]) -> None:
    c = _stub_client("anthropic")
    asyncio.run(c.call("s", "p"))
    assert set(chat.built[0].kw) == {"model", "api_key", "temperature", "timeout", "max_retries"}


# ---- 7. regressions from replacing langchain's default async client -------------------------------


def test_redirecting_endpoint_still_works(port: int) -> None:
    """The cached langchain/openai default client is an ``openai.DefaultAsyncHttpxClient``
    (follow_redirects=True).  The bare ``httpx.AsyncClient`` is not: a gateway that 307s a POST
    (http->https, trailing-slash, reverse proxy) worked before and now raises APIStatusError."""
    c = _real(port, path="redir")
    assert asyncio.run(c.call_json("s", "p")) == {"ok": True}
    assert c._json_mode_unsupported is False


def test_connection_pool_limits_match_openai_defaults(port: int) -> None:
    async def pool() -> int:
        return (
            _real(port)._get_model().async_client._client._client._transport._pool._max_connections
        )

    assert asyncio.run(pool()) == openai.DefaultAsyncHttpxClient()._transport._pool._max_connections
