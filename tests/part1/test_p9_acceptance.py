"""P9 acceptance (derived from the report's Evidence + Fix text only; no P9 code seen).

Report P9: every gate decision is discarded at the end of the run; run n+1 re-asks everything run n
answered.  Fix: persist gate decisions to a FILE keyed by a STABLE HASH of the question (entity pair,
proposed structure, field mapping); on the next run decisions whose key matches are applied WITHOUT
asking and the COUNT of auto-applied decisions is reported.  Ceiling: a file has no concurrency
control / schema / query surface.  The file is a backfill source for Part 3 (so it must keep the
question, not only a hash of it).  Plan 9 P9: run 2 with identical questions asks ZERO; the hash is
stable across processes (different PYTHONHASHSEED); writes are atomic; a corrupt file degrades to
"ask again", not a crash.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p9_acceptance.py -q -p no:cacheprovider

ADAPTATION POINTS (the report fixes semantics, not interfaces; one place each):
  MODULE        dotted path of the decision-memory module (env P9_MODULE overrides; default below).
  question_key(question: Mapping) -> str          stable hex digest of a JSON-able question
  DecisionMemory(path)                            file-backed store, location = `path` (str | Path)
      .lookup(question) -> decision | None        None == miss.  A HIT increments .applied_count
      .record(question, decision) -> None         durable on return (a new DecisionMemory(path) sees it)
      .applied_count : int                        hits since THIS instance was created
  The gate hook (human_nodes.py / graph.py) is the lead's and is NOT tested here.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = os.environ.get("P9_MODULE", "src.orchestrator.decision_memory")
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mod():
    try:
        m = importlib.import_module(MODULE)
    except ImportError as e:  # clean failure, not a collection error
        pytest.fail(f"decision-memory module {MODULE!r} not importable: {e}")
    for name in ("question_key", "DecisionMemory"):
        assert hasattr(m, name), f"{MODULE} must define {name}"
    return m


# ── neutral fixtures: the three question kinds the report names ──────────────────────────────
def pair_q(a="7", b="9") -> dict:
    return {
        "kind": "entity_pair",
        "left": {"table": "alpha", "id": a, "name": "Zed Ltd"},
        "right": {"table": "beta", "id": b, "name": "Zed Limited"},
    }


def structure_q(role="junction") -> dict:
    return {
        "kind": "structure",
        "table": "gamma",
        "proposed": {"role": role, "links": ["alpha", "beta"]},
    }


def mapping_q(dest="d_col") -> dict:
    return {"kind": "field_mapping", "entity": "widget", "source": "s_col", "dest": dest}


QUESTIONS = [pair_q(), pair_q("8", "10"), structure_q(), mapping_q(), mapping_q("e_col")]


def run_gate(mem, questions, answer=lambda q: {"approved": True, "n": len(json.dumps(q))}):
    """Stand-in for the lead's hook: ask only what memory cannot answer. -> (asked, decisions)."""
    asked, out = [], []
    for q in questions:
        d = mem.lookup(q)
        if d is None:
            asked.append(q)
            d = answer(q)
            mem.record(q, d)
        out.append(d)
    return asked, out


class _NoMemory:
    """The OLD behaviour: every decision discarded at the end of the run."""

    applied_count = 0

    def lookup(self, q):
        return None

    def record(self, q, d):
        pass


# ── 1. run 2 asks zero, count reported ───────────────────────────────────────────────────────
def test_old_behaviour_reasks_everything():
    """Guard: the harness discriminates.  With no memory run 2 asks N; the checks below need 0."""
    asked, _ = run_gate(_NoMemory(), QUESTIONS)
    asked2, _ = run_gate(_NoMemory(), QUESTIONS)
    assert len(asked2) == len(QUESTIONS)


def test_run2_identical_questions_asks_zero_and_reports_count(mod, tmp_path):
    p = tmp_path / "decisions.json"
    m1 = mod.DecisionMemory(p)
    asked1, d1 = run_gate(m1, QUESTIONS)
    assert len(asked1) == len(QUESTIONS) and m1.applied_count == 0
    m2 = mod.DecisionMemory(p)  # a new run == a new instance on the same file
    asked2, d2 = run_gate(m2, QUESTIONS, answer=lambda q: pytest.fail("asked in run 2"))
    assert asked2 == []
    assert m2.applied_count == len(QUESTIONS)
    assert d2 == d1


def test_run2_with_one_new_question_asks_exactly_that_one(mod, tmp_path):
    p = tmp_path / "d.json"
    run_gate(mod.DecisionMemory(p), QUESTIONS)
    new = mapping_q("never_seen")
    m2 = mod.DecisionMemory(p)
    asked, _ = run_gate(m2, QUESTIONS + [new])
    assert asked == [new] and m2.applied_count == len(QUESTIONS)


def test_decisions_accumulate_across_runs(mod, tmp_path):
    """Run 2's file keeps run 1's answers (no overwrite with only this run's decisions)."""
    p = tmp_path / "d.json"
    run_gate(mod.DecisionMemory(p), QUESTIONS[:2])
    run_gate(mod.DecisionMemory(p), QUESTIONS[2:])
    m3 = mod.DecisionMemory(p)
    asked, _ = run_gate(m3, QUESTIONS)
    assert asked == [] and m3.applied_count == len(QUESTIONS)


@pytest.mark.parametrize("decision", [False, 0, "", [], {}, {"approved": False}, "reject", "é☃"])
def test_falsy_and_unicode_decisions_roundtrip_and_are_not_a_miss(mod, tmp_path, decision):
    p = tmp_path / "d.json"
    mod.DecisionMemory(p).record(pair_q(), decision)
    m = mod.DecisionMemory(p)
    got = m.lookup(pair_q())
    assert got == decision and type(got) is type(decision)
    assert m.applied_count == 1


def test_revised_decision_wins(mod, tmp_path):
    p = tmp_path / "d.json"
    m = mod.DecisionMemory(p)
    m.record(mapping_q(), "keep")
    m.record(mapping_q(), "reject")
    assert mod.DecisionMemory(p).lookup(mapping_q()) == "reject"


def test_miss_does_not_count_as_applied(mod, tmp_path):
    m = mod.DecisionMemory(tmp_path / "d.json")
    assert m.lookup(pair_q()) is None and m.applied_count == 0


# ── 2. the key: stable across processes, order-free, sensitive to every change ──────────────
_CHILD = (
    "import json,sys\n"
    "from {mod} import question_key\n"
    "print(question_key(json.loads(sys.argv[1])))\n"
)


def _child_key(mod_name, qjson, seed, cwd=None):
    env = {
        **os.environ,
        "PYTHONHASHSEED": seed,
        "PYTHONPATH": os.pathsep.join(
            [str(REPO)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
        ),
    }
    r = subprocess.run(
        [sys.executable, "-c", _CHILD.format(mod=mod_name), qjson],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or REPO),
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_key_is_a_hex_digest(mod):
    k = mod.question_key(pair_q())
    assert isinstance(k, str) and re.fullmatch(r"[0-9a-f]{16,}", k), k  # >=64 bits: judgement


def test_key_stable_across_processes_and_hashseeds(mod, tmp_path):
    q = pair_q()
    here = mod.question_key(q)
    keys = {_child_key(MODULE, json.dumps(q), s) for s in ("0", "1", "42", "12345", "random")}
    assert keys == {here}, keys
    # and independent of cwd
    assert _child_key(MODULE, json.dumps(q), "7", cwd=tmp_path) == here


def test_key_ignores_dict_insertion_order_at_every_depth(mod):
    q = structure_q()
    rev = {
        k: ({kk: v[kk] for kk in reversed(list(v))} if isinstance(v, dict) else v)
        for k, v in reversed(list(q.items()))
    }
    assert list(rev) != list(q)
    assert mod.question_key(rev) == mod.question_key(q)
    # across processes too: the JSON the child parses carries the reversed order
    assert _child_key(MODULE, json.dumps(rev), "3") == mod.question_key(q)


def _leaves(o, path=()):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _leaves(v, path + (k,))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _leaves(v, path + (i,))
    else:
        yield path, o


def _set(o, path, val):
    import copy

    o = copy.deepcopy(o)
    cur = o
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = val
    return o


@pytest.mark.parametrize("q", [pair_q(), structure_q(), mapping_q()])
def test_changing_any_single_field_changes_the_key_and_misses(mod, q, tmp_path):
    base = mod.question_key(q)
    m = mod.DecisionMemory(tmp_path / "d.json")
    m.record(q, "decided")
    n = 0
    for path, val in _leaves(q):
        changed = _set(q, path, str(val) + "x")
        assert mod.question_key(changed) != base, f"field {path} ignored by the key"
        assert m.lookup(changed) is None, f"changed {path} matched an old decision"
        n += 1
    assert n >= 3 and m.applied_count == 0
    # adding / removing a field is also a changed question
    assert mod.question_key({**q, "extra": 1}) != base
    assert mod.question_key({k: v for k, v in list(q.items())[1:]}) != base


def test_key_separates_ambiguous_concatenations_and_types(mod):
    ks = [
        mod.question_key(x)
        for x in (
            {"a": "bc"},
            {"ab": "c"},
            {"a": ["b", "c"]},
            {"a": ["bc"]},
            {"a": 1},
            {"a": "1"},
            {"a": None},
            {"a": "None"},
            {"a": True},
            {"a": []},
            {"a": {}},
            {},
        )
    ]
    assert len(set(ks)) == len(ks)


def test_key_is_generic_over_question_shape(mod):
    """No question-specific field names inside the mechanism: arbitrary random schemas all work
    and never collide (a key built from named fields would KeyError or collide here)."""
    rng = random.Random(9)
    names = lambda: "".join(rng.choice("abcdefghijk") for _ in range(rng.randint(3, 8)))  # noqa: E731
    qs = [
        {names(): {names(): [names(), rng.randint(0, 10**6)]}, names(): names()} for _ in range(300)
    ]
    ks = {mod.question_key(q) for q in qs}
    assert len(ks) == len({json.dumps(q, sort_keys=True) for q in qs})


def test_question_is_not_mutated(mod, tmp_path):
    import copy

    q = structure_q()
    c = copy.deepcopy(q)
    mod.question_key(q)
    m = mod.DecisionMemory(tmp_path / "d.json")
    m.record(q, "x")
    m.lookup(q)
    assert q == c


# ── 3. file: location, content, atomic writes ───────────────────────────────────────────────
def test_location_is_configurable_and_nothing_written_elsewhere(mod, tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    p = tmp_path / "somewhere" / "else" / "memory.json"  # parent dirs do not exist: judgement
    mod.DecisionMemory(p).record(pair_q(), "yes")
    assert p.is_file() and p.stat().st_size > 0
    assert list(cwd.iterdir()) == []  # not written next to the cwd
    q2 = tmp_path / "other.json"
    assert mod.DecisionMemory(q2).lookup(pair_q()) is None  # a different file is a different memory


def test_file_keeps_the_question_for_part3_backfill(mod, tmp_path):
    """A hash alone cannot be backfilled into a decision store; question + decision must be on file."""
    p = tmp_path / "d.json"
    q = {"kind": "entity_pair", "left": "zq-left-marker-81", "right": "zq-right-marker-82"}
    mod.DecisionMemory(p).record(q, {"verdict": "zq-decision-marker-83"})
    text = p.read_text(encoding="utf-8")
    for marker in ("zq-left-marker-81", "zq-right-marker-82", "zq-decision-marker-83"):
        assert marker in text, f"{marker} not on file (judgement: file is Part 3's backfill source)"


def test_write_goes_via_tmp_file_in_same_dir_then_os_replace(mod, tmp_path, monkeypatch):
    p = tmp_path / "d.json"
    mod.DecisionMemory(p).record(pair_q(), "old")
    old = p.read_bytes()
    calls = []
    real = os.replace

    def spy(src, dst, *a, **k):
        src, dst = Path(src), Path(dst)
        calls.append((src, dst))
        assert dst.read_bytes() == old, "final file touched before the replace"
        assert src.exists() and src != dst
        assert src.parent.resolve() == dst.parent.resolve(), "tmp must be on the same filesystem"
        json.loads(src.read_text(encoding="utf-8"))  # tmp already holds the COMPLETE new content
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", spy)
    mod.DecisionMemory(p).record(mapping_q(), "new")
    assert len(calls) == 1 and calls[0][1].resolve() == p.resolve()
    assert [f.name for f in tmp_path.iterdir()] == [p.name], "stray tmp file left behind"
    m = mod.DecisionMemory(p)
    assert m.lookup(pair_q()) == "old" and m.lookup(mapping_q()) == "new"


def test_crash_at_replace_leaves_old_file_intact(mod, tmp_path, monkeypatch):
    p = tmp_path / "d.json"
    mod.DecisionMemory(p).record(pair_q(), "old")
    old = p.read_bytes()

    def boom(*a, **k):
        raise OSError("simulated crash between tmp write and replace")

    monkeypatch.setattr(os, "replace", boom)
    try:
        mod.DecisionMemory(p).record(mapping_q(), "lost")  # raising or swallowing both acceptable
    except OSError:
        pass
    monkeypatch.undo()
    assert p.read_bytes() == old
    m = mod.DecisionMemory(p)
    assert m.lookup(pair_q()) == "old" and m.lookup(mapping_q()) is None


def test_crash_while_writing_tmp_leaves_old_file_intact(mod, tmp_path, monkeypatch):
    """Kill the serialiser mid-write: the final file must not be the half-written one."""
    p = tmp_path / "d.json"
    mod.DecisionMemory(p).record(pair_q(), "old")
    old = p.read_bytes()

    class Unserialisable:  # json.dump raises part-way through the new content
        pass

    try:
        mod.DecisionMemory(p).record(mapping_q(), Unserialisable())
    except (TypeError, ValueError, OSError):
        pass
    assert p.read_bytes() == old
    assert mod.DecisionMemory(p).lookup(pair_q()) == "old"


# ── 4. corrupt / truncated / empty / missing file degrades to "ask again" ───────────────────
def _good_bytes(mod, tmp_path):
    p = tmp_path / "good.json"
    run_gate(mod.DecisionMemory(p), QUESTIONS)
    return p.read_bytes()


def _corruptions(good: bytes):
    return {
        "missing": None,
        "empty": b"",
        "whitespace": b"  \n",
        "truncated_half": good[: len(good) // 2],
        "truncated_by_one": good[:-1],
        "garbage_text": b"not json at all {{{",
        "binary_non_utf8": bytes(range(128, 256)) * 4,
        "nul_bytes": b"\x00" * 64,
        "json_null": b"null",
        "json_list": b"[1, 2, 3]",
        "json_number": b"42",
        "json_string": b'"hello"',
        "json_empty_obj": b"{}",
    }


CORRUPTION_NAMES = list(_corruptions(b"x" * 10))


@pytest.mark.parametrize("name", CORRUPTION_NAMES)
def test_bad_file_means_ask_again_never_raise(mod, tmp_path, name):
    good = _good_bytes(mod, tmp_path)
    p = tmp_path / "bad.json"
    data = _corruptions(good)[name]
    if data is not None:
        p.write_bytes(data)
    m = mod.DecisionMemory(p)  # constructing must not raise
    for q in QUESTIONS:
        assert m.lookup(q) is None
    assert m.applied_count == 0
    # and it self-heals: asking again records, the next run applies
    asked, _ = run_gate(m, QUESTIONS)
    assert len(asked) == len(QUESTIONS)
    m2 = mod.DecisionMemory(p)
    asked2, _ = run_gate(m2, QUESTIONS, answer=lambda q: pytest.fail("asked after self-heal"))
    assert asked2 == [] and m2.applied_count == len(QUESTIONS)


def test_path_is_a_directory_degrades_not_crashes(mod, tmp_path):
    d = tmp_path / "isdir"
    d.mkdir()
    m = mod.DecisionMemory(d)
    assert m.lookup(pair_q()) is None
    try:
        m.record(pair_q(), "x")  # may raise OSError on write; a lookup must never
    except OSError:
        pass


def test_corrupt_entries_inside_valid_json_are_not_applied_wrongly(mod, tmp_path):
    """Truncating the file at every byte offset must never raise and never return a decision
    different from the one recorded (a prefix may lose entries; it must not invent one)."""
    good = _good_bytes(mod, tmp_path)
    expected = {mod.question_key(q): None for q in QUESTIONS}
    ref = mod.DecisionMemory(tmp_path / "good.json")
    for q in QUESTIONS:
        expected[mod.question_key(q)] = ref.lookup(q)
    p = tmp_path / "cut.json"
    for cut in range(0, len(good), max(1, len(good) // 40)):
        p.write_bytes(good[:cut])
        m = mod.DecisionMemory(p)
        for q in QUESTIONS:
            got = m.lookup(q)
            assert got is None or got == expected[mod.question_key(q)]


# ── 5. 2.4: no fitted constants, no corpus literals ─────────────────────────────────────────
CORPUS_LITERALS = (
    r"\bFrance\b|\bUS\b|\bSAS\b|Berhad|\bGlobal\b|\bGroup\b|Holdings|companyname|main_entity_id"
    r"|company_relation|\bstaff\b|testdiffbot|companyexperience|\bpagila\b|\bindustry\b"
)


def _module_source(mod) -> tuple[Path, str]:
    f = Path(mod.__file__)
    return f, f.read_text(encoding="utf-8")


def test_no_corpus_literals_in_module(mod):
    f, src = _module_source(mod)
    hits = [(i + 1, ln) for i, ln in enumerate(src.splitlines()) if re.search(CORPUS_LITERALS, ln)]
    assert hits == [], f"corpus literal in {f.name}: {hits}"


def test_no_fitted_numeric_constants(mod):
    """Every numeric literal in the module must be a structural 0/1/2/-1 (indent, slicing,
    index).  Anything else ('would a count over the corpus produce this?') needs a named,
    justified constant: report it to the lead, do not silently pass."""
    _, src = _module_source(mod)
    bad = []
    for n in ast.walk(ast.parse(src)):
        if (
            isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)
            and n.value not in (0, 1, 2, -1)
        ):
            bad.append((n.lineno, n.value))
    assert bad == [], f"numeric literals needing justification: {bad}"


def test_no_hand_written_name_lists_and_no_hash_builtin(mod):
    _, src = _module_source(mod)
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, (ast.List, ast.Set, ast.Tuple)):
            strs = [e for e in n.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            assert len(strs) < 5, f"hand-written string list at line {n.lineno}"
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "hash":
            pytest.fail(f"builtin hash() at line {n.lineno}: salted per process (PYTHONHASHSEED)")
    assert "os.replace" in src or ".replace(" in src, "atomic write must use os.replace"


def test_module_has_no_module_level_mutable_state(mod):
    """CLAUDE.md: no module-level mutable state (two runs would cross-contaminate decisions)."""
    _, src = _module_source(mod)
    for n in ast.parse(src).body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            v = n.value
            assert not isinstance(v, (ast.Dict, ast.List, ast.Set)) and not (
                isinstance(v, ast.Call)
                and getattr(v.func, "id", "") in {"dict", "list", "set", "defaultdict"}
            ), f"module-level mutable at line {n.lineno}"


def test_module_stays_in_layering(mod):
    """Layering: the module must not import api/orchestrator-above (it is a leaf helper)."""
    _, src = _module_source(mod)
    for n in ast.walk(ast.parse(src)):
        names = []
        if isinstance(n, ast.ImportFrom) and n.module:
            names = [n.module]
        elif isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        for nm in names:
            assert not nm.startswith("src.api"), nm
