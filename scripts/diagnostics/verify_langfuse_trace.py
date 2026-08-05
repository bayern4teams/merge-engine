"""Automated live verification that a merge run produced a correct
Langfuse trace (Part 1.5 C.5).

Replaces the two pre-existing pseudo-checks, neither of which asserted
anything about a trace: `test_full_pipeline_manual.py::check_langfuse` is an
HTTP-200 liveness probe on `http://localhost:3000/`, and
`test_boeing_full_pipeline.py::check_langfuse_traces` hits `/api/keys`
(not a real Langfuse route) with a Basic-auth header that doesn't match the
bootstrapped user, and every failure path there is a `warn`, never a `fail`
-- that check could never fail. This script instead queries the real
Langfuse API and asserts the actual span tree.

Six assertions:
1. Exactly ONE trace carries session_id == thread_id (proves Part 1.5 C.3 --
   would have caught the pre-fix "3 root traces" regression).
2. Every node that appears in the run's own `timings` (logs/run_reports/
   <thread_id>.json) shows up as a span in the trace -- cross-checked
   against the app's own independent record, not just "some spans exist".
3. Every GENERATION span has a non-null parent (proves the Part 1.5 C.2
   nesting fix -- this is the assertion that failed before that fix).
4. Every generation carries a model name and non-zero token usage.
5. The custom `resolve.<entity>.*` phase spans from Part 1.5 C.4 are present.
6. Span durations are within a generous tolerance of the run report's own
   `timings`, as a sanity cross-check (not a strict latency budget).

Usage:
    python -m scripts.diagnostics.verify_langfuse_trace <thread_id>
    python -m scripts.diagnostics.verify_langfuse_trace <thread_id> --wait 30
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
RUN_REPORTS_DIR = ROOT / "logs" / "run_reports"

_DURATION_TOLERANCE = 0.5  # +/- 50% - a sanity cross-check, not a strict SLA


def _load_run_report(thread_id: str) -> dict[str, Any] | None:
    path = RUN_REPORTS_DIR / f"{thread_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _observation_seconds(obs: Any) -> float | None:
    start, end = getattr(obs, "start_time", None), getattr(obs, "end_time", None)
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("thread_id", help="The workflow thread_id used as the Langfuse session_id.")
    ap.add_argument(
        "--wait",
        type=float,
        default=10.0,
        help="Seconds to wait before querying, letting a just-flushed batch land server-side.",
    )
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(str(ROOT / ".env"))
    from langfuse import get_client

    client = get_client()
    if not client.auth_check():
        print("FAIL: Langfuse auth_check() failed — is the stack up?", file=sys.stderr)
        return 2

    if args.wait > 0:
        time.sleep(args.wait)

    ok = True

    def check(cond: bool, msg: str) -> bool:
        nonlocal ok
        print(("PASS" if cond else "FAIL") + ": " + msg)
        if not cond:
            ok = False
        return cond

    # ── 1. Exactly one trace for this run ──────────────────────────────
    traces = client.api.trace.list(session_id=args.thread_id, limit=10)
    n_traces = len(traces.data)
    if not check(
        n_traces == 1,
        f"exactly one trace for session {args.thread_id!r} (found {n_traces}) — "
        "more than one means HITL resumes fragmented into separate root traces",
    ):
        if n_traces == 0:
            print("No trace found at all — aborting further checks.", file=sys.stderr)
            return 1
        print(f"Using the most recent of {n_traces} traces for remaining checks.")

    trace_summary = sorted(traces.data, key=lambda t: t.timestamp)[-1]
    trace = client.api.trace.get(trace_summary.id)
    observations = list(trace.observations)
    print(f"\nTrace {trace.id!r}: {len(observations)} observation(s).\n")

    # ── 2. Every timed node appears as a span, cross-checked against the
    #      run's own independent record ────────────────────────────────
    report = _load_run_report(args.thread_id)
    node_names: set[str] = set(report.get("timings", {}).keys()) if report else set()
    obs_by_name: dict[str, list[Any]] = {}
    for obs in observations:
        obs_by_name.setdefault(obs.name, []).append(obs)

    if node_names:
        missing_nodes = node_names - set(obs_by_name)
        check(
            not missing_nodes,
            f"every node in the run report's timings appears as a span "
            f"(checked {len(node_names)}, missing: {sorted(missing_nodes)})",
        )
    else:
        print(
            f"SKIP: no run report at {RUN_REPORTS_DIR / (args.thread_id + '.json')} "
            "— node-coverage check needs it."
        )

    # ── 3 & 4. Generations nest under a real parent and carry model+usage ─
    generations = [o for o in observations if o.type == "GENERATION"]
    check(bool(generations), f"trace has at least one GENERATION span (found {len(generations)})")
    orphans = [g.name for g in generations if not g.parent_observation_id]
    check(
        not orphans,
        f"every generation has a non-null parent (orphans: {orphans}) — "
        "this is the exact assertion that failed before the Part 1.5 C.2 nesting fix",
    )
    no_model = [g.name for g in generations if not g.model]
    check(not no_model, f"every generation carries a model name (missing: {no_model})")
    no_usage = [g.name for g in generations if not (g.usage_details or {})]
    check(not no_usage, f"every generation carries token usage (missing: {no_usage})")

    # ── 5. Custom resolve.<entity>.* phase spans present ───────────────
    # Part 1.5 B.3: blocking/fit_model/scoring used to be 3 separate async
    # spans, until _resolve_core became one synchronous function dispatched
    # either inline or to a ProcessPoolExecutor worker — a worker has no
    # event loop to open async span context managers on, so all three
    # collapsed into one "resolve.<entity>.match" span (its internal timing
    # breakdown carried as span attributes instead of child spans; see
    # resolve_entities.py's _resolve_core docstring). "clustering" still
    # runs in the parent process and keeps its own span.
    custom = [o for o in observations if o.name.startswith("resolve.")]
    check(
        bool(custom),
        f"custom resolve.<entity>.* phase spans present (found {len(custom)}) — "
        "these are the Part 1.5 C.4/B.3 match+clustering waterfall",
    )
    for phase in ("match", "clustering"):
        matching = [o for o in custom if o.name.endswith(f".{phase}")]
        check(bool(matching), f"  at least one '.{phase}' span present ({len(matching)} found)")

    # ── 6. Durations roughly match the run report's own timings ────────
    if node_names:
        mismatches = []
        for name in node_names & set(obs_by_name):
            reported = report["timings"].get(name)
            span_seconds = _observation_seconds(obs_by_name[name][0])
            if reported is None or span_seconds is None or reported <= 0:
                continue
            ratio = span_seconds / reported
            if not (1 - _DURATION_TOLERANCE <= ratio <= 1 + _DURATION_TOLERANCE):
                mismatches.append(f"{name}: report={reported:.1f}s trace={span_seconds:.1f}s")
        check(
            not mismatches,
            f"span durations within {_DURATION_TOLERANCE:.0%} of the run report's own "
            f"timings (mismatches: {mismatches})",
        )

    print(f"\n{'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'} for trace {trace.id!r}")
    print(f"View it at: {client.get_trace_url(trace_id=trace.id)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
