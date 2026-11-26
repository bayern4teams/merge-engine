"""Shared plumbing for the B.4 Splink workbench (Branch_Plan.md B.4).

Every phase module (``splink_recommender``, ``splink_training``,
``splink_inference``, ``splink_evaluation``) needs the same handful of
low-level operations:
turn raw records into a DataFrame with a unique-id column, assemble a
``SettingsCreator`` from comparisons + blocking rules, build a ``Linker``
over in-process DuckDB, and shape a labels table the way the *installed*
4.0.16 expects it. They live here once rather than in every module, and they
call :func:`splink_compat.require_splink` at the entry of each public
function so the plan's "verify the installed version, refuse the wrong
major" mandate holds for the whole subsystem from one place.

Two design constraints inherited from this codebase (CLAUDE.md):

* **No module-level mutable state.** Everything is a function of its
  arguments; a ``Linker`` is built per call, never cached here.
* **No reverse imports.** This module may import ``splink_compat`` and
  ``resolve_entities`` (same layer, and the latter only inside functions,
  matching ``splink_matcher.py``'s own import style) — nothing upward.

The unique-id column default is ``_record_id_str`` to stay wire-compatible
with the existing ``splink_matcher.py`` (Phase 7.4) and the spool's own
record-id convention, so a run can move between the legacy engine and the
workbench without a second id scheme.
"""

from __future__ import annotations

from typing import Any, Literal

from src.mcp_server.tools.splink_compat import require_splink

# The unique-id column the workbench standardises on. Matches
# splink_matcher.py's own choice so the two Splink paths share one id scheme.
DEFAULT_UNIQUE_ID_COLUMN = "_record_id_str"

# Splink 4.0.16's own link-type literal (SettingsCreator, blocking_analysis).
# Named once here so every phase module shares the same type instead of each
# re-typing the three string values.
LinkType = Literal["link_only", "link_and_dedupe", "dedupe_only"]


def _unique_id_of(rec: dict[str, Any]) -> str:
    """The stable id of one raw record.

    Delegates to ``resolve_entities._record_id`` (the single existing source
    of truth, imported lazily the way ``splink_matcher.py`` does so the import
    stays inside a function and the module stays importable when Splink is
    absent).
    """
    from src.mcp_server.tools.resolve_entities import _record_id

    return str(_record_id(rec))


def records_to_dataframe(
    records: list[dict[str, Any]],
    *,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
    value_columns: list[str] | None = None,
) -> Any:
    """A pandas DataFrame of ``records`` with one added unique-id column.

    ``value_columns`` restricts the columns carried into the DataFrame; when
    omitted, every non-underscore (non-internal) key of the records is
    carried. Underscore-prefixed keys (``_origin_id`` etc.) are spool/lineage
    metadata, not comparison material, and are always dropped — comparing on
    them is the exact failure mode ADR 0004's "never block on the unique id"
    correction was about.

    Raises :class:`ValueError` when two records share a unique id (Splink's
    settings require a genuinely unique column) or when no records are given.
    """
    require_splink()
    import pandas as pd  # type: ignore[import-untyped]  # pandas stubs untyped at this pin

    if not records:
        raise ValueError("records_to_dataframe needs at least one record")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in records:
        uid = _unique_id_of(rec)
        if uid in seen:
            raise ValueError(f"duplicate unique id {uid!r} — records must be distinct")
        seen.add(uid)
        row: dict[str, Any] = {unique_id_column: uid}
        if value_columns is None:
            for key, value in rec.items():
                if str(key).startswith("_"):
                    continue
                row[key] = value
        else:
            for key in value_columns:
                row[key] = rec.get(key)
        rows.append(row)

    df = pd.DataFrame(rows)
    # Splink's comparators operate on text; the legacy engine stringifies
    # everything it compares. Do the same so a mixed-type column (int ids
    # next to str ids) never reaches a comparator as an object column.
    for col in df.columns:
        if col == unique_id_column:
            continue
        df[col] = df[col].map(lambda v: "" if v is None else str(v))
    return df


def make_linker(
    df: Any,
    *,
    comparisons: list[Any],
    blocking_rules_to_generate_predictions: list[Any],
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
    link_type: LinkType = "dedupe_only",
) -> Any:
    """A fresh ``Linker`` over in-process DuckDB for the given DataFrame.

    A new ``DuckDBAPI`` per call — Splink's own docs warn against sharing a
    database handle across linkers, and this codebase already runs one
    in-process DuckDB per spool for the same reason.
    """
    require_splink()
    from splink import DuckDBAPI, Linker, SettingsCreator

    settings = SettingsCreator(
        link_type=link_type,
        unique_id_column_name=unique_id_column,
        comparisons=comparisons,
        blocking_rules_to_generate_predictions=blocking_rules_to_generate_predictions,
    )
    return Linker(df, settings, DuckDBAPI())


def labels_dataframe(
    labelled_pairs: list[tuple[str, str, float]],
    *,
    unique_id_column: str = DEFAULT_UNIQUE_ID_COLUMN,
) -> Any:
    """A labels DataFrame in the *installed* 4.0.16 schema.

    4.0.x labels tables are ``{uid}_l``, ``{uid}_r``, ``clerical_match_score``
    (plus ``{source_dataset}_l/_r`` when a source-dataset column is configured
    — the workbench does not configure one, so those are absent). This is not
    the older 3.x ``is_match`` shape; the plan's own §32-36 description was
    written from 3.x memory, and the installed package was verified to expect
    the 4.0 shape (see the B.4 API-verification note in TODO.md).

    ``labelled_pairs`` is ``[(id_l, id_r, clerical_match_score), ...]`` where
    the clerical score is 1.0 for a confirmed match and 0.0 for a confirmed
    non-match.
    """
    require_splink()
    import pandas as pd

    l_col = f"{unique_id_column}_l"
    r_col = f"{unique_id_column}_r"
    return pd.DataFrame(
        [
            {l_col: a, r_col: b, "clerical_match_score": float(score)}
            for a, b, score in labelled_pairs
        ]
    )


def unique_id_column_of(linker: Any) -> str:
    """The unique-id column *this linker was configured with*.

    Verified installed 4.0.16: it lives at
    ``linker._settings_obj.as_dict()["unique_id_column_name"]`` (not a
    direct Settings attribute). Label tables must be built with this
    column's name suffixed ``_l``/``_r`` — a labels table built from the
    *default* column name fails with a ``Binder Error: Referenced column
    "<default>_l" not found`` whenever the linker was configured with a
    different unique id (which is the case for every caller that does not
    use :data:`DEFAULT_UNIQUE_ID_COLUMN`).
    """
    require_splink()
    try:
        return str(linker._settings_obj.as_dict()["unique_id_column_name"])
    except (AttributeError, KeyError):
        return DEFAULT_UNIQUE_ID_COLUMN


def splink_version_info() -> dict[str, Any]:
    """Thin re-export so phase modules don't import ``splink_compat`` twice
    (once directly for the gate, once for the version record)."""
    return require_splink()
