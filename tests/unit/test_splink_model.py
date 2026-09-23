"""Unit tests for the B.4 Splink version store (S-N, `splink_model`).

These test the *file-store* logic — load/list/duplicate/compare/activate/
rollback/archive/set_status — by seeding a temp store with a synthetic
payload directly, so no EM training and no live Linker are required. That
keeps them in the fast `tests/unit` gate (no `slow`/`live` marker). The
round-trip-verify and load_active paths (which build a real Linker) are
covered by the eval gate, not here.

Also covers the pure `splink_compat` version gate and the workbench's
blocking-rule normaliser, which are pure functions.
"""

from __future__ import annotations

import json

import pytest

from src.mcp_server.tools import splink_compat
from src.mcp_server.tools import splink_model as model
from src.mcp_server.tools.splink_workbench import _normalise_rule


def _seed(store: str, name: str, version: int, payload: dict, meta: dict | None = None) -> None:
    """Write a version's files directly, bypassing save_version (which needs a Linker)."""
    vdir = model._version_dir(store, name, version)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "model.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (vdir / "metadata.json").write_text(json.dumps(meta or {}, indent=2), encoding="utf-8")


def _verified_meta(version: int, parent: int | None = None) -> dict:
    return {
        "model_name": "m",
        "version": version,
        "parent_version": parent,
        "status": "evaluated",
        "round_trip_verified": True,
        "splink_version": "4.0.16",
    }


# ── splink_compat: the version gate is pure ───────────────────────────────


class TestSplinkCompat:
    def test_required_major_is_four(self) -> None:
        assert splink_compat.REQUIRED_MAJOR == 4

    def test_check_passes_on_major_four(self) -> None:
        # The venv has 4.0.16 installed, so the gate must not raise.
        if splink_compat.splink_available():
            splink_compat.check_splink()  # must not raise

    def test_require_splink_returns_info_dict(self) -> None:
        info = splink_compat.require_splink()
        assert "available" in info and "required_major" in info
        assert info["required_major"] == 4


# ── workbench: blocking-rule normaliser is pure ────────────────────────────


class TestNormaliseRule:
    def test_bare_equality_gets_l_r_prefixes(self) -> None:
        assert _normalise_rule("name = name") == 'l."name" = r."name"'

    def test_already_prefixed_is_untouched(self) -> None:
        assert _normalise_rule('l."name" = r."name"') == 'l."name" = r."name"'

    def test_quoted_bare_equality(self) -> None:
        # The UI may quote column names; the result is idempotently prefixed.
        assert _normalise_rule('"name" = "name"') == 'l."name" = r."name"'

    def test_and_of_bare_equalities(self) -> None:
        out = _normalise_rule("name = name and email = email")
        assert out == 'l."name" = r."name" AND l."email" = r."email"'

    def test_non_equality_rule_untouched(self) -> None:
        # A fuzzy/levenshtein rule is not a bare equality — leave it alone.
        assert (
            _normalise_rule("levenshtein(l.name, r.name) < 2") == "levenshtein(l.name, r.name) < 2"
        )


# ── splink_model: store layout helpers ────────────────────────────────────


class TestStoreLayout:
    def test_safe_name_rejects_path_traversal(self) -> None:
        with pytest.raises(ValueError):
            model._safe_name("a/b")
        with pytest.raises(ValueError):
            model._safe_name("a b")
        with pytest.raises(ValueError):
            model._safe_name("")

    def test_next_version_is_one_for_empty(self, tmp_path) -> None:
        assert model._next_version(str(tmp_path), "m") == 1

    def test_next_version_increments(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {}, {})
        _seed(store, "m", 3, {}, {})
        assert model._next_version(store, "m") == 4


# ── splink_model: load & list ─────────────────────────────────────────────


class TestLoadList:
    def test_load_version_merges_payload_and_metadata(self, tmp_path) -> None:
        store = str(tmp_path)
        payload = {"link_type": "dedupe", "comparisons": [{"comparison_name": "name"}]}
        _seed(store, "m", 2, payload, _verified_meta(2))
        v = model.load_version(store, "m", 2)
        assert v["version"] == 2
        assert v["payload"] == payload
        assert v["metadata"]["round_trip_verified"] is True

    def test_load_missing_version_raises_named(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError) as exc:
            model.load_version(str(tmp_path), "m", 99)
        assert "v99" in str(exc.value)

    def test_list_versions_oldest_first_with_flags(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 3, {}, _verified_meta(3))
        _seed(store, "m", 1, {}, _verified_meta(1))
        _seed(store, "m", 2, {}, _verified_meta(2))
        versions = model.list_versions(store, "m")
        assert [v["version"] for v in versions] == [1, 2, 3]

    def test_list_versions_marks_active(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {}, _verified_meta(1))
        _seed(store, "m", 2, {}, _verified_meta(2))
        model.activate_version(store, "m", 2)
        versions = model.list_versions(store, "m")
        active = {v["version"]: v["is_active"] for v in versions}
        assert active[2] is True and active[1] is False

    def test_list_versions_empty_model(self, tmp_path) -> None:
        assert model.list_versions(str(tmp_path), "nope") == []


# ── splink_model: duplicate (the "edit" primitive) ────────────────────────


class TestDuplicate:
    def test_duplicate_sets_parent_and_draft_status(self, tmp_path) -> None:
        store = str(tmp_path)
        payload = {"link_type": "dedupe", "unique_id_column_name": "id"}
        _seed(store, "m", 1, payload, _verified_meta(1))
        out = model.duplicate_version(store, "m", 1)
        assert out["version"] == 2
        assert out["parent_version"] == 1
        v2 = model.load_version(store, "m", 2)
        assert v2["payload"] == payload  # byte-identical copy
        assert v2["metadata"]["status"] == "draft"
        assert v2["metadata"]["parent_version"] == 1

    def test_duplicate_overrides_metadata(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {"a": 1}, _verified_meta(1))
        out = model.duplicate_version(store, "m", 1, metadata_override={"note": "x"})
        v = model.load_version(store, "m", out["version"])
        assert v["metadata"]["note"] == "x"


# ── splink_model: compare (structural diff of two payloads) ───────────────


class TestCompare:
    def test_identical_payloads_have_zero_diff(self, tmp_path) -> None:
        store = str(tmp_path)
        payload = {"link_type": "dedupe", "core_model_parameters": {"prior": 0.1}}
        _seed(store, "m", 1, payload, {})
        _seed(store, "m", 2, dict(payload), {})
        out = model.compare_versions(store, "m", 1, 2)
        assert out["n_changed_leaves"] == 0
        assert out["diffs"] == []

    def test_changed_leaf_is_reported_with_both_values(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {"prior": 0.1, "same": "x"}, {})
        _seed(store, "m", 2, {"prior": 0.3, "same": "x"}, {})
        out = model.compare_versions(store, "m", 1, 2)
        assert out["n_changed_leaves"] == 1
        (d,) = out["diffs"]
        assert d["path"] == "prior"
        assert d["version_a"] == 0.1 and d["version_b"] == 0.3

    def test_nested_path_diff(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {"cmp": [{"m": 0.9, "u": 0.1}]}, {})
        _seed(store, "m", 2, {"cmp": [{"m": 0.8, "u": 0.1}]}, {})
        out = model.compare_versions(store, "m", 1, 2)
        paths = [d["path"] for d in out["diffs"]]
        assert "cmp[0].m" in paths
        assert "cmp[0].u" not in paths  # unchanged


# ── splink_model: activate / rollback / archive / set_status ──────────────


class TestLifecycle:
    def test_activate_writes_pointer_and_history(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {}, _verified_meta(1))
        out = model.activate_version(store, "m", 1)
        assert out["activated"] == 1 and out["previous_active"] is None
        assert model._active_pointer(store, "m").exists()

    def test_activate_demotes_previous_to_evaluated(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {}, _verified_meta(1))
        _seed(store, "m", 2, {}, _verified_meta(2))
        model.activate_version(store, "m", 1)
        model.activate_version(store, "m", 2)
        assert model.load_version(store, "m", 2)["metadata"]["status"] == "active"
        assert model.load_version(store, "m", 1)["metadata"]["status"] == "evaluated"

    def test_activate_refuses_unverified_version(self, tmp_path) -> None:
        store = str(tmp_path)
        bad = _verified_meta(1)
        bad["round_trip_verified"] = False
        bad["status"] = "draft"
        _seed(store, "m", 1, {}, bad)
        with pytest.raises(ValueError):
            model.activate_version(store, "m", 1)

    def test_activate_refuses_archived_version(self, tmp_path) -> None:
        store = str(tmp_path)
        meta = _verified_meta(1)
        meta["status"] = "archived"
        _seed(store, "m", 1, {}, meta)
        with pytest.raises(ValueError):
            model.activate_version(store, "m", 1)

    def test_rollback_restores_previous_activation(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {}, _verified_meta(1))
        _seed(store, "m", 2, {}, _verified_meta(2))
        model.activate_version(store, "m", 1)
        model.activate_version(store, "m", 2)
        out = model.rollback(store, "m")
        assert out["rolled_back_from"] == 2
        assert out["activated"] == 1

    def test_rollback_with_no_history_raises(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {}, _verified_meta(1))
        with pytest.raises(FileNotFoundError):
            model.rollback(store, "m")

    def test_rollback_with_single_activation_raises(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {}, _verified_meta(1))
        model.activate_version(store, "m", 1)  # only one entry in history
        with pytest.raises(ValueError):
            model.rollback(store, "m")

    def test_archive_clears_active_pointer(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {}, _verified_meta(1))
        model.activate_version(store, "m", 1)
        out = model.archive_version(store, "m", 1)
        assert out["was_active"] is True
        assert not model._active_pointer(store, "m").exists()

    def test_set_status_forward_only(self, tmp_path) -> None:
        store = str(tmp_path)
        meta = _verified_meta(1)
        meta["status"] = "trained"
        _seed(store, "m", 1, {}, meta)
        # trained -> evaluated is legal
        assert model.set_status(store, "m", 1, "evaluated")["status"] == "evaluated"
        # evaluated -> trained is NOT (backward)
        with pytest.raises(ValueError):
            model.set_status(store, "m", 1, "trained")

    def test_set_status_rejects_unknown(self, tmp_path) -> None:
        store = str(tmp_path)
        _seed(store, "m", 1, {}, _verified_meta(1))
        with pytest.raises(ValueError):
            model.set_status(store, "m", 1, "bogus")
