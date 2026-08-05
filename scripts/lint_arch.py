"""Architectural lint (Phase 0 Sec 0.5).

"Telling agents 'don't do X' in a markdown file is a suggestion; making X
trigger a build failure is a rule." Each check below is mechanical, not a
style opinion, and every violation message states the rule, the offending
line, and the concrete fix - so a failure is actionable without re-reading
this file.

Checks:
1. Layering: src/api -> src/orchestrator -> src/mcp_server/tools ->
   src/mcp_server/connectors -> src/utils. No reverse import. src/llm may be
   imported by anything below src/api, but must never import upward itself.
2. No new module-level mutable state in src/mcp_server/tools/ - a dict/list/
   set assigned at module scope AND later mutated inside a function is
   shared across every concurrent call (this is exactly the
   _LAST_BLOCKING_STATS bug: two concurrent entity resolutions each report
   the other's blocking stats). Read-only lookup tables (assigned once,
   never written to afterward) are not flagged.
3. No compile()/build_graph() call at module scope (forces a real build on
   every import, including every test collection). re.compile(...) is
   normal precompiled-regex practice and is excluded.
4. House style: every file directly under src/orchestrator/nodes/ (except
   __init__.py) must log both "ENTER <name>" and "EXIT <name>" via
   logger.debug.

Findings are checked against arch-lint-baseline.txt, the same ratchet used
for mypy-baseline.txt: a NEW violation fails the check; an existing,
already-known one does not block every run on debt this pass didn't create.
Regenerate the baseline with scripts/update_arch_lint_baseline.py after
fixing something (to record the shrinkage) - never to silently grow it.

Usage: python scripts/lint_arch.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
BASELINE_PATH = ROOT / "arch-lint-baseline.txt"

# ── Layering ────────────────────────────────────────────────────────────

_LAYER_ORDER = [
    "src.api",
    "src.orchestrator",
    "src.mcp_server.tools",
    "src.mcp_server.connectors",
    "src.utils",
]


def _layer_of(module_path: str) -> int | None:
    for idx, prefix in enumerate(_LAYER_ORDER):
        if module_path == prefix or module_path.startswith(prefix + "."):
            return idx
    return None


def _module_name(file_path: Path) -> str:
    rel = file_path.relative_to(ROOT).with_suffix("")
    return ".".join(rel.parts)


def _imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append((node.module, node.lineno))
    return out


def check_layering(errors: list[str]) -> None:
    for py_file in SRC.rglob("*.py"):
        mod = _module_name(py_file)
        is_llm_module = mod == "src.llm" or mod.startswith("src.llm.")
        this_layer = _layer_of(mod)
        for imported, lineno in _imported_modules(ast.parse(py_file.read_text(encoding="utf-8"))):
            if not imported.startswith("src."):
                continue
            imported_is_llm = imported == "src.llm" or imported.startswith("src.llm.")

            if is_llm_module:
                # llm may import its own submodules and src.utils freely; it
                # must never import upward into api/orchestrator/tools/connectors.
                if imported_is_llm or imported.startswith("src.utils"):
                    continue
                errors.append(
                    f"{py_file.relative_to(ROOT)}:{lineno}: rule 'src/llm must "
                    f"not import orchestrator/tools/connectors/api' - {mod} "
                    f"imports {imported}. Fix: move the shared code down into "
                    f"src/utils instead, or pass it in as a parameter."
                )
                continue

            if imported_is_llm:
                continue  # anything below src/api may import src/llm

            imported_layer = _layer_of(imported)
            if this_layer is None or imported_layer is None:
                continue
            if imported_layer < this_layer:
                errors.append(
                    f"{py_file.relative_to(ROOT)}:{lineno}: rule 'no reverse "
                    f"layer import ({_LAYER_ORDER[this_layer]} -> "
                    f"{_LAYER_ORDER[imported_layer]} is backwards)' - {mod} "
                    f"imports {imported}. Fix: move the shared code down into "
                    f"a lower layer ({_LAYER_ORDER[this_layer]} or below) "
                    f"instead of reaching up into {_LAYER_ORDER[imported_layer]}."
                )


# ── Module-level mutable state in mcp_server/tools, written to at runtime ─

_MUTABLE_LITERAL_TYPES = (ast.Dict, ast.List, ast.Set)


def _names_mutated_in_functions(tree: ast.Module) -> set[str]:
    """Module-level names reassigned or subscript/attr-mutated inside a
    function body - i.e. genuinely *stateful*, not just a constant table
    initialized once at import time."""
    mutated: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            targets: list[ast.expr] = []
            if isinstance(inner, ast.Assign):
                targets = inner.targets
            elif isinstance(inner, ast.AugAssign):
                targets = [inner.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    mutated.add(t.id)
                elif isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                    mutated.add(t.value.id)  # e.g. _STATS["key"] = value
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                recv = inner.func.value
                if isinstance(recv, ast.Name) and inner.func.attr in (
                    "append",
                    "update",
                    "clear",
                    "pop",
                    "add",
                    "extend",
                    "remove",
                ):
                    mutated.add(recv.id)
    return mutated


def check_no_new_module_mutable_state(errors: list[str]) -> None:
    tools_dir = SRC / "mcp_server" / "tools"
    for py_file in tools_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        mutated_names = _names_mutated_in_functions(tree)
        for node in tree.body:  # module scope only, not nested in functions/classes
            if not isinstance(node, ast.AnnAssign | ast.Assign):
                continue
            value = node.value
            if not isinstance(value, _MUTABLE_LITERAL_TYPES):
                continue
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            for t in targets:
                if not isinstance(t, ast.Name) or t.id not in mutated_names:
                    continue
                errors.append(
                    f"{py_file.relative_to(ROOT)}:{node.lineno}: rule 'no new "
                    f"module-level mutable state in src/mcp_server/tools/' - "
                    f"'{t.id}' is a dict/list/set initialized at module scope "
                    f"AND mutated inside a function - shared across every "
                    f"concurrent call to this module (two concurrent runs "
                    f"would corrupt each other's state, as _LAST_BLOCKING_STATS "
                    f"already did once). Fix: return this value through the "
                    f"tool's own result dict instead of a module global."
                )


# ── No compile()/build_graph() at module scope ──────────────────────────


def check_no_module_scope_compile(errors: list[str]) -> None:
    for py_file in SRC.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in tree.body:
            expr = node.value if isinstance(node, ast.Assign | ast.Expr) else None
            if not isinstance(expr, ast.Call):
                continue
            callee = expr.func
            if isinstance(callee, ast.Name) and callee.id == "build_graph":
                name = "build_graph"
            elif (
                isinstance(callee, ast.Attribute)
                and callee.attr == "compile"
                # re.compile(...)/pattern-module precompilation is normal
                # practice, not the langgraph StateGraph.compile() this rule
                # targets - excluded by receiver name.
                and not (isinstance(callee.value, ast.Name) and callee.value.id == "re")
            ):
                name = "compile"
            else:
                continue
            errors.append(
                f"{py_file.relative_to(ROOT)}:{node.lineno}: rule 'no "
                f"compile()/build_graph() call at module scope' - a top-level "
                f"call to '{name}(...)' runs on every import, including every "
                f"test collection. Fix: move it behind a function or module "
                f"__getattr__ so it only runs on first actual use."
            )


# ── House style: ENTER/EXIT logging in every orchestrator node ─────────


def check_node_enter_exit_logging(errors: list[str]) -> None:
    nodes_dir = SRC / "orchestrator" / "nodes"
    for py_file in nodes_dir.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        has_enter = 'logger.debug("ENTER' in text or "logger.debug('ENTER" in text
        has_exit = 'logger.debug("EXIT' in text or "logger.debug('EXIT" in text
        if not has_enter or not has_exit:
            missing = []
            if not has_enter:
                missing.append("ENTER")
            if not has_exit:
                missing.append("EXIT")
            errors.append(
                f"{py_file.relative_to(ROOT)}: rule 'every file under "
                f"src/orchestrator/nodes/ must log both ENTER and EXIT' - "
                f"missing {'/'.join(missing)}. Fix: add "
                f'logger.debug("{missing[0]} <node_name>") at the '
                f"{'start' if 'ENTER' in missing else 'end'} of the node "
                f"function."
            )


def load_baseline() -> set[str]:
    if not BASELINE_PATH.exists():
        return set()
    return {
        line
        for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }


def main() -> int:
    errors: list[str] = []
    check_layering(errors)
    check_no_new_module_mutable_state(errors)
    check_no_module_scope_compile(errors)
    check_node_enter_exit_logging(errors)

    baseline = load_baseline()
    new_errors = [e for e in errors if e not in baseline]

    if new_errors:
        for e in new_errors:
            print(e)
        print(
            f"\n{len(new_errors)} NEW architectural lint violation(s) "
            f"(plus {len(errors) - len(new_errors)} already in "
            f"arch-lint-baseline.txt, not re-reported)."
        )
        return 1
    if errors:
        print(
            f"0 new architectural lint violations "
            f"({len(errors)} pre-existing, tracked in arch-lint-baseline.txt)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
