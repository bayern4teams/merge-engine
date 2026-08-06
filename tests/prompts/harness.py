"""Prompt test harness for merge-engine LLM stages.

Golden cases live in ``tests/prompts/golden/<stage>.json``. Each stage's
prompt is scored against expected outputs derived from the seeded test
databases.

Usage
-----
    python -m tests.prompts.harness                # offline contract checks
    python -m tests.prompts.harness --live         # hit the real model, print scorecard
    python -m tests.prompts.harness --stage field_mapping --live

Offline mode (CI-safe, no network) validates that every golden file is
well-formed and that each stage's system/human prompt exists and carries
the placeholders the production code substitutes.

Live mode builds prompts exactly as the production call sites do (same
templates, same interpolation helpers), calls the configured model, and
prints a per-stage scorecard: accuracy, malformed-JSON count, and
latency. Runs are Langfuse-traced automatically when LANGFUSE_PUBLIC_KEY
is set (self-hosted stack — docker/langfuse-compose.yml).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

_GOLDEN_DIR = Path(__file__).parent / "golden"

_STAGES = (
    "request_parsing",
    "dependency_analysis",
    "entity_table_selection",
    "scope_planning",
    "entity_resolution",
    "field_mapping",
)

# Placeholders the production call sites substitute into each stage's
# human template (system templates carry no placeholders).
_REQUIRED_PLACEHOLDERS = {
    "request_parsing": ["{user_request}"],
    "dependency_analysis": ["{target_entities}", "{schema_info}"],
    "entity_table_selection": ["{target_entities}", "{table_catalog}"],
    "scope_planning": ["{subject}", "{subject_entity}", "{target_entities}", "{table_catalog}"],
    "entity_resolution": ["{entity_type}", "{pair_count}", "{pairs_block}"],
    "field_mapping": [
        "{entity_type}",
        "{dest_table}",
        "{destination_schema}",
        "{source_fields_block}",
    ],
}


def _load_golden(stage: str) -> dict[str, Any]:
    path = _GOLDEN_DIR / f"{stage}.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ── Offline contract checks ──────────────────────────────────────────


def check_contracts(stages: list[str]) -> list[str]:
    """Validate golden files + prompt templates. Returns list of problems."""
    from src.llm import PROMPT_TEMPLATES

    problems: list[str] = []
    for stage in stages:
        prompts = PROMPT_TEMPLATES.get(stage, {})
        if not prompts.get("system"):
            problems.append(f"{stage}: system prompt missing/empty")
        human = prompts.get("human", "")
        if not human:
            problems.append(f"{stage}: human prompt missing/empty")
        for ph in _REQUIRED_PLACEHOLDERS[stage]:
            if ph not in human:
                problems.append(f"{stage}: human prompt missing placeholder {ph}")

        try:
            golden = _load_golden(stage)
        except Exception as exc:
            problems.append(f"{stage}: golden file unreadable: {exc}")
            continue
        cases = golden.get("cases", [])
        if not cases:
            problems.append(f"{stage}: golden file has no cases")
        for case in cases:
            cid = case.get("id", "<missing id>")
            if "input" not in case or "expected" not in case:
                problems.append(f"{stage}/{cid}: case must have input and expected")
    return problems


# ── Live prompt construction (mirrors production call sites) ─────────


def _build_prompt(stage: str, case: dict[str, Any]) -> tuple[str, str]:
    from src.llm import PROMPT_TEMPLATES

    prompts = PROMPT_TEMPLATES[stage]
    system = prompts["system"]
    human = prompts["human"]
    inp = case["input"]

    if stage == "request_parsing":
        return system, human.replace("{user_request}", inp["user_request"])

    if stage == "dependency_analysis":
        return system, (
            human.replace("{target_entities}", json.dumps(inp["target_entities"])).replace(
                "{schema_info}", inp["schema_info"]
            )
        )

    if stage == "entity_table_selection":
        user_prompt = human.replace("{target_entities}", json.dumps(inp["target_entities"])).replace(
            "{table_catalog}", inp["table_catalog"]
        )
        user_request = inp.get("user_request", "")
        if user_request:
            user_prompt += f"\n\n(The user's original request was: {user_request})\n"
        return system, user_prompt

    if stage == "dependency_analysis":
        user_prompt = human.replace("{target_entities}", json.dumps(inp["target_entities"])).replace(
            "{schema_info}", inp["schema_info"]
        )
        if inp.get("retry_hint"):
            user_prompt += f"\n\n[HUMAN FEEDBACK ON PREVIOUS RERUN]: {inp['retry_hint']}\nPlease address this feedback in your new output."
        return system, user_prompt

    if stage == "scope_planning":
        user_prompt = (
            human.replace("{subject}", inp["subject"])
            .replace("{subject_entity}", inp["subject_entity"])
            .replace("{target_entities}", json.dumps(inp["target_entities"]))
            .replace("{table_catalog}", inp["table_catalog"])
        )
        if inp.get("retry_hint"):
            user_prompt += f"\n\n[HUMAN FEEDBACK ON PREVIOUS RERUN]: {inp['retry_hint']}\nPlease address this feedback in your new output."
        return system, user_prompt

    if stage == "entity_resolution":
        from src.mcp_server.tools.resolve_entities import _format_pairs_block

        borderline = [
            (f"a{i}", f"b{i}", pair["a"], pair["b"], 0.8) for i, pair in enumerate(inp["pairs"], 1)
        ]
        user_prompt = (
            human.replace("{entity_type}", inp["entity_type"])
            .replace("{pair_count}", str(len(borderline)))
            .replace("{pairs_block}", _format_pairs_block(borderline))
        )
        if inp.get("retry_hint"):
            user_prompt += f"\n\n[HUMAN FEEDBACK ON PREVIOUS RERUN]: {inp['retry_hint']}\nPlease address this feedback in your new output."
        return system, user_prompt

    if stage == "field_mapping":
        from src.mcp_server.tools.map_fields import (
            _RESERVED_DEST_COLUMNS,
            _format_source_fields_block,
        )

        fields = [f["name"] for f in inp["fields"]]
        unified_schema = {
            f["name"]: {"type": f["type"], "sample_values": f.get("sample_values", [])}
            for f in inp["fields"]
        }
        # Imported, not re-listed: map_fields_impl withholds these
        # loader-managed columns from the model, and a harness that offered
        # them would score a prompt production never sends. It did — the
        # model dutifully mapped an origin surrogate key onto `_source_ids`.
        dest_cols_str = "\n".join(
            f"- {c['name']} ({c.get('type', 'unknown')})"
            for c in inp["destination_schema"]
            if c["name"] not in _RESERVED_DEST_COLUMNS
        )
        return system, (
            human.replace("{entity_type}", inp["entity_type"])
            .replace("{dest_table}", inp["dest_table"])
            .replace("{destination_schema}", dest_cols_str)
            .replace("{source_fields_block}", _format_source_fields_block(fields, unified_schema))
        )

    raise ValueError(f"unknown stage: {stage}")


# ── Scoring ──────────────────────────────────────────────────────────


def _score(stage: str, case: dict[str, Any], result: Any) -> tuple[bool, str]:
    """Score one live result against the golden expectation."""
    expected = case["expected"]
    if not isinstance(result, dict):
        return False, f"non-dict result: {type(result).__name__}"

    if stage == "request_parsing":
        got = set(result.get("entity_types", []) or [])
        want = set(expected["entity_types"])
        ok = got == want
        detail = f"got={sorted(got)} want={sorted(want)}"
        if ok and "explicit_entity_types" in expected:
            got_explicit = set(result.get("explicit_entity_types", []) or [])
            want_explicit = set(expected["explicit_entity_types"])
            ok = got_explicit == want_explicit
            detail = f"explicit got={sorted(got_explicit)} want={sorted(want_explicit)}"
        return ok, detail

    if stage == "dependency_analysis":
        tree = result.get("dependency_tree", {}) or {}
        missing = [
            f"{dep}->{pre}"
            for dep, pre in expected.get("expected_edges", [])
            if pre not in (tree.get(dep) or [])
        ]
        wrong = [
            f"{dep}->{pre}"
            for dep, pre in expected.get("expected_absent", [])
            if pre in (tree.get(dep) or [])
        ]
        ok = not missing and not wrong
        return ok, f"missing={missing} unexpected={wrong}" if not ok else "all edges correct"

    if stage == "entity_table_selection":
        tables = result.get("entity_tables", {}) or {}

        def _pairs(entity: str) -> set[tuple[str, str]]:
            return {
                (str(item.get("origin_id")), str(item.get("table")))
                for item in (tables.get(entity) or [])
                if isinstance(item, dict)
            }

        missing: list[str] = []
        unexpected: list[str] = []
        for entity, wanted in (expected.get("entity_tables_must_include") or {}).items():
            got = _pairs(entity)
            for origin_id, table in wanted:
                if (origin_id, table) not in got:
                    missing.append(f"{entity}:{origin_id}.{table}")
        for entity, unwanted in (expected.get("entity_tables_must_exclude") or {}).items():
            got = _pairs(entity)
            for origin_id, table in unwanted:
                if (origin_id, table) in got:
                    unexpected.append(f"{entity}:{origin_id}.{table}")
        ok = not missing and not unexpected
        return ok, f"missing={missing} unexpected={unexpected}" if not ok else "all tables correct"

    if stage == "scope_planning":
        entities = result.get("entities", {}) or {}

        def _entries(entity: str) -> list[dict[str, Any]]:
            return [e for e in (entities.get(entity) or []) if isinstance(e, dict)]

        missing = []
        for entity, wanted in (expected.get("must_include") or {}).items():
            got = _entries(entity)
            for want_entry in wanted:
                found = any(
                    str(g.get("origin_id")) == want_entry["origin_id"]
                    and str(g.get("table")) == want_entry["table"]
                    and str(g.get("mode")) == want_entry["mode"]
                    and (
                        "link_entity" not in want_entry
                        or str(g.get("link_entity")) == want_entry["link_entity"]
                    )
                    for g in got
                )
                if not found:
                    missing.append(
                        f"{entity}:{want_entry['origin_id']}.{want_entry['table']}[{want_entry['mode']}]"
                    )
        ok = not missing
        return ok, f"missing={missing}" if not ok else "all scope entries correct"

    if stage == "entity_resolution":
        judgments = result.get("judgments", []) or []
        by_id = {j.get("pair_id"): bool(j.get("match")) for j in judgments if isinstance(j, dict)}
        want = expected["matches"]
        got = [by_id.get(i) for i in range(1, len(want) + 1)]
        return got == want, f"got={got} want={want}"

    if stage == "field_mapping":
        details: list[str] = []
        ok = True
        for field, exp in expected.items():
            dec = result.get(field)
            if not isinstance(dec, dict):
                ok = False
                details.append(f"{field}: no decision")
                continue
            if dec.get("action") != exp["action"]:
                ok = False
                details.append(f"{field}: action={dec.get('action')} want={exp['action']}")
            elif exp["action"] == "map" and dec.get("dest_column") != exp["dest_column"]:
                ok = False
                details.append(f"{field}: dest={dec.get('dest_column')} want={exp['dest_column']}")
        return ok, "; ".join(details) if details else "all fields correct"

    raise ValueError(f"unknown stage: {stage}")


# ── Live run ─────────────────────────────────────────────────────────


async def run_live(stages: list[str]) -> int:
    from src.llm import LLMClient
    from src.utils.config_loader import load_config

    cfg = load_config(None)
    llm_config = {
        "provider": cfg.llm.provider,
        "model": cfg.llm.model,
        "api_key": cfg.llm.api_key,
        "base_url": cfg.llm.base_url,
        "temperature": cfg.llm.temperature,
        "structured_output": cfg.llm.structured_output,
    }
    client = LLMClient(llm_config)

    overall_failures = 0
    print(f"\nModel: {cfg.llm.provider}/{cfg.llm.model}\n")

    for stage in stages:
        golden = _load_golden(stage)
        cases = golden["cases"]
        passed = 0
        malformed = 0
        latencies: list[float] = []
        lines: list[str] = []

        for case in cases:
            system, human = _build_prompt(stage, case)
            start = time.perf_counter()
            try:
                result = await client.call_json(
                    system_prompt=system,
                    user_prompt=human,
                )
            except json.JSONDecodeError:
                malformed += 1
                lines.append(f"  ✗ {case['id']}: malformed JSON after retry")
                continue
            except Exception as exc:
                lines.append(f"  ✗ {case['id']}: call failed: {exc}")
                continue
            finally:
                latencies.append(time.perf_counter() - start)

            ok, detail = _score(stage, case, result)
            if ok:
                passed += 1
                lines.append(f"  ✓ {case['id']}")
            else:
                lines.append(f"  ✗ {case['id']}: {detail}")

        overall_failures += len(cases) - passed
        avg_ms = (sum(latencies) / len(latencies) * 1000) if latencies else 0.0
        print(f"── {stage} ──")
        print("\n".join(lines))
        print(
            f"  score: {passed}/{len(cases)}  malformed-JSON: {malformed}  "
            f"avg latency: {avg_ms:.0f} ms\n"
        )

    print(f"TOTAL failures: {overall_failures}")
    # Short-lived script: flush buffered spans so Langfuse receives them.
    try:
        from src.llm.tracing import flush

        flush()
    except Exception:
        pass
    return 1 if overall_failures else 0


def main() -> int:
    # Windows consoles often default to cp1252, which can't print the
    # scorecard glyphs.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=_STAGES, help="run a single stage")
    parser.add_argument(
        "--live",
        action="store_true",
        help="call the real model and print a scorecard",
    )
    args = parser.parse_args()
    stages = [args.stage] if args.stage else list(_STAGES)

    problems = check_contracts(stages)
    if problems:
        print("Contract problems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"Contract checks OK for: {', '.join(stages)}")

    if args.live:
        return asyncio.run(run_live(stages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
