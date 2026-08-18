"""Saved run profiles (Phase O).

A profile exists because a re-run was not reproducible: two runs of identical
data produced destination schemas of 15 and 13 columns, because the LLM's
field mapping varied. Pinning the decisions is the fix, so these tests care
most about what gets pinned, what never gets written to disk (credentials),
and that a profile cannot quietly point at the wrong server.
"""

from __future__ import annotations

import pytest

from src.orchestrator import profiles as profiles_mod
from src.orchestrator.profiles import (
    PINNED_KEYS,
    apply_profile,
    build_profile,
    capture_from_state,
    connection_key,
    delete_profile,
    find_profile_by_fingerprint,
    list_profiles,
    load_profile,
    overall_fingerprint,
    register_in_index,
    restore_credentials,
    safe_name,
    save_profile,
    stage_cache_get,
    stage_cache_set,
    strip_secrets,
)

ORIGINS = [
    {
        "id": "pagila_pg",
        "type": "postgresql",
        "priority": 2,
        "connection": {
            "host": "localhost",
            "port": 5434,
            "user": "postgres",
            "password": "s3cret",
            "database": "pagila",
        },
    }
]
DESTINATION = {
    "type": "postgresql",
    "host": "localhost",
    "port": 5434,
    "user": "postgres",
    "password": "s3cret",
    "database": "dest",
}


@pytest.fixture(autouse=True)
def _isolated_profile_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles_mod, "PROFILE_DIR", tmp_path / "profiles")


def _state() -> dict:
    return {
        "entity_tables": {"person": [{"origin_id": "pagila_pg", "table": "public.staff"}]},
        "matching_rules": {"person": {"blocking_keys": ["email"]}},
        "conflict_policy": {"email": "prefer_origin"},
        "field_mappings": {"person": {"customer_id": "id"}},
    }


class TestSecretsNeverReachDisk:
    def test_passwords_are_stripped_from_origins(self) -> None:
        profile = build_profile(
            "nightly", user_request="merge people", origins=ORIGINS, destination=DESTINATION
        )
        assert profile["origins"][0]["connection"]["password"] == ""
        assert profile["destination"]["password"] == ""

    def test_the_saved_file_contains_no_secret(self, tmp_path) -> None:
        path = save_profile(
            build_profile(
                "nightly", user_request="merge people", origins=ORIGINS, destination=DESTINATION
            )
        )
        assert "s3cret" not in path.read_text(encoding="utf-8")

    def test_save_strips_even_if_the_caller_did_not(self) -> None:
        profile = build_profile("n", user_request="r", origins=[], destination={})
        profile["origins"] = ORIGINS  # smuggled back in with a live password
        path = save_profile(profile)
        assert "s3cret" not in path.read_text(encoding="utf-8")

    def test_other_credential_shaped_keys_are_stripped_too(self) -> None:
        stripped = strip_secrets({"api_key": "abc", "token": "xyz", "host": "localhost"})
        assert stripped == {"api_key": "", "token": "", "host": "localhost"}

    def test_the_live_config_is_not_mutated(self) -> None:
        build_profile("n", user_request="r", origins=ORIGINS, destination=DESTINATION)
        assert ORIGINS[0]["connection"]["password"] == "s3cret"


class TestPinnedDecisions:
    def test_a_completed_run_pins_every_decision(self) -> None:
        profile = build_profile(
            "n", user_request="r", origins=ORIGINS, destination=DESTINATION, state=_state()
        )
        for key in PINNED_KEYS:
            assert profile[key], f"{key} was not captured"

    def test_the_field_mapping_is_pinned(self) -> None:
        """The specific non-determinism that motivated profiles at all."""
        profile = build_profile(
            "n", user_request="r", origins=ORIGINS, destination=DESTINATION, state=_state()
        )
        assert profile["field_mappings"]["person"]["customer_id"] == "id"

    def test_applying_a_profile_seeds_state(self) -> None:
        profile = build_profile(
            "n", user_request="r", origins=ORIGINS, destination=DESTINATION, state=_state()
        )
        state: dict = {}
        apply_profile(state, profile)
        assert state["entity_tables"]["person"][0]["table"] == "public.staff"
        assert state["profile_name"] == "n"

    def test_a_caller_can_override_one_decision_for_one_run(self) -> None:
        profile = build_profile(
            "n", user_request="r", origins=ORIGINS, destination=DESTINATION, state=_state()
        )
        state = {"field_mappings": {"person": {"customer_id": "customer_id"}}}
        apply_profile(state, profile)
        # The explicit choice survives; everything else still comes from the
        # profile.
        assert state["field_mappings"]["person"]["customer_id"] == "customer_id"
        assert state["matching_rules"]["person"]["blocking_keys"] == ["email"]

    def test_capture_records_what_the_run_decided(self) -> None:
        profile = build_profile("n", user_request="r", origins=ORIGINS, destination=DESTINATION)
        assert profile["field_mappings"] == {}
        capture_from_state(profile, _state())
        assert profile["field_mappings"]["person"] == {"customer_id": "id"}

    def test_capture_ignores_empty_results(self) -> None:
        """A failed run must not blank out decisions that already worked."""
        profile = build_profile(
            "n", user_request="r", origins=ORIGINS, destination=DESTINATION, state=_state()
        )
        capture_from_state(profile, {"field_mappings": {}})
        assert profile["field_mappings"]["person"] == {"customer_id": "id"}


class TestCredentialRestore:
    def test_passwords_come_back_from_the_live_config(self) -> None:
        saved = strip_secrets(ORIGINS)
        restored, missing = restore_credentials(saved, ORIGINS)
        assert restored[0]["connection"]["password"] == "s3cret"
        assert missing == []

    def test_matching_is_by_coordinates_not_position(self) -> None:
        """Reordering the live origins must not repoint a saved origin."""
        other = {
            "id": "other",
            "type": "postgresql",
            "priority": 1,
            "connection": {
                "host": "otherhost",
                "port": 5432,
                "user": "postgres",
                "password": "WRONG",
                "database": "other",
            },
        }
        saved = strip_secrets(ORIGINS)
        restored, _ = restore_credentials(saved, [other] + ORIGINS)
        assert restored[0]["connection"]["password"] == "s3cret"

    def test_an_unmatched_origin_is_reported_not_guessed(self) -> None:
        saved = strip_secrets(ORIGINS)
        restored, missing = restore_credentials(saved, [])
        assert missing, "a profile with no credentials must not look runnable"
        assert restored[0]["connection"]["password"] == ""

    def test_connection_key_ignores_credentials(self) -> None:
        a = {
            "type": "postgresql",
            "connection": {"host": "h", "port": 1, "database": "d", "user": "u", "password": "one"},
        }
        b = {
            "type": "postgresql",
            "connection": {"host": "h", "port": 1, "database": "d", "user": "u", "password": "two"},
        }
        assert connection_key(a) == connection_key(b)


class TestProfileStore:
    def test_round_trip(self) -> None:
        save_profile(
            build_profile(
                "Nightly Merge",
                user_request="merge people",
                origins=ORIGINS,
                destination=DESTINATION,
                state=_state(),
            )
        )
        loaded = load_profile("Nightly Merge")
        assert loaded is not None
        assert loaded["user_request"] == "merge people"

    def test_listing_summarises_what_is_pinned(self) -> None:
        save_profile(
            build_profile(
                "n", user_request="r", origins=ORIGINS, destination=DESTINATION, state=_state()
            )
        )
        listed = list_profiles()
        assert len(listed) == 1
        assert listed[0]["entities"] == ["person"]
        assert set(listed[0]["pins"]) == set(PINNED_KEYS)

    def test_resaving_keeps_the_original_created_at(self) -> None:
        first = load_profile_after_save("n")
        again = load_profile_after_save("n")
        assert again["created_at"] == first["created_at"]

    def test_a_corrupt_profile_does_not_hide_the_others(self) -> None:
        save_profile(
            build_profile("good", user_request="r", origins=ORIGINS, destination=DESTINATION)
        )
        (profiles_mod.PROFILE_DIR / "broken.json").write_text("{not json")
        assert [p["name"] for p in list_profiles()] == ["good"]

    def test_the_fingerprint_index_file_is_not_listed_as_a_profile(self) -> None:
        # register_in_index() (Phase A4) writes _index.json into the same
        # directory as real profiles -- a bare `*.json` glob would surface
        # it in the UI's profile dropdown as a selectable (but bogus) run.
        save_profile(
            build_profile("good", user_request="r", origins=ORIGINS, destination=DESTINATION)
        )
        register_in_index("some-fingerprint", "good")
        assert [p["name"] for p in list_profiles()] == ["good"]

    def test_missing_profile_returns_none(self) -> None:
        assert load_profile("never-saved") is None

    def test_delete(self) -> None:
        save_profile(build_profile("n", user_request="r", origins=ORIGINS, destination=DESTINATION))
        assert delete_profile("n") is True
        assert delete_profile("n") is False

    def test_a_name_cannot_escape_the_profile_directory(self) -> None:
        assert "/" not in safe_name("../../etc/passwd")
        assert ".." not in safe_name("../../etc/passwd")

    def test_a_name_with_no_usable_characters_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            safe_name("///")

    def test_names_differing_only_by_punctuation_do_not_collide_silently(
        self,
    ) -> None:
        save_profile(build_profile("night merge", user_request="a", origins=[], destination={}))
        save_profile(build_profile("night merge", user_request="b", origins=[], destination={}))
        assert len(list_profiles()) == 1
        assert load_profile("night merge")["user_request"] == "b"


def load_profile_after_save(name: str) -> dict:
    save_profile(build_profile(name, user_request="r", origins=ORIGINS, destination=DESTINATION))
    loaded = load_profile(name)
    assert loaded is not None
    return loaded


_SCHEMAS = {
    "pagila_pg": {
        "tables": [
            {
                "name": "staff",
                "full_name": "public.staff",
                "columns": [{"name": "id", "type": "int"}, {"name": "email", "type": "text"}],
            }
        ]
    }
}


class TestOverallFingerprintAutoDetection:
    """Phase A4: a profile is found by (origins, destination, schema)
    coordinates, not just selected by name -- this is what lets a repeat
    run skip re-deciding things without the user having to remember or
    pick a profile from a dropdown."""

    def test_saving_a_profile_with_schemas_registers_it_for_auto_detection(self) -> None:
        profile = build_profile(
            "nightly",
            user_request="merge people",
            origins=ORIGINS,
            destination=DESTINATION,
            state={**_state(), "schemas": _SCHEMAS},
        )
        save_profile(profile)

        fp = overall_fingerprint(ORIGINS, _SCHEMAS, DESTINATION)
        found = find_profile_by_fingerprint(fp)
        assert found is not None
        assert found["name"] == "nightly"

    def test_a_profile_saved_without_schemas_is_not_auto_detectable(self) -> None:
        """build_profile is also used to pre-configure a profile before a
        run has ever completed -- no schemas yet means nothing to fingerprint
        against, so it correctly stays name-only until capture_from_state
        (post-run) fills the fingerprint in."""
        profile = build_profile(
            "manual", user_request="r", origins=ORIGINS, destination=DESTINATION
        )
        save_profile(profile)
        fp = overall_fingerprint(ORIGINS, _SCHEMAS, DESTINATION)
        assert find_profile_by_fingerprint(fp) is None

    def test_a_different_schema_does_not_match_the_saved_fingerprint(self) -> None:
        profile = build_profile(
            "nightly",
            user_request="r",
            origins=ORIGINS,
            destination=DESTINATION,
            state={**_state(), "schemas": _SCHEMAS},
        )
        save_profile(profile)

        drifted_schemas = {
            "pagila_pg": {
                "tables": [
                    {
                        "name": "staff",
                        "full_name": "public.staff",
                        "columns": [{"name": "id", "type": "int"}],  # "email" column gone
                    }
                ]
            }
        }
        fp = overall_fingerprint(ORIGINS, drifted_schemas, DESTINATION)
        assert find_profile_by_fingerprint(fp) is None

    def test_re_saving_the_same_shape_under_a_new_name_wins_auto_detection(self) -> None:
        """Last-write-wins: the most recently saved profile for a shape is
        what auto-detection offers, not whichever was created first."""
        state = {**_state(), "schemas": _SCHEMAS}
        save_profile(
            build_profile(
                "first", user_request="a", origins=ORIGINS, destination=DESTINATION, state=state
            )
        )
        save_profile(
            build_profile(
                "second", user_request="b", origins=ORIGINS, destination=DESTINATION, state=state
            )
        )
        fp = overall_fingerprint(ORIGINS, _SCHEMAS, DESTINATION)
        assert find_profile_by_fingerprint(fp)["name"] == "second"

    def test_deleting_the_indexed_profile_makes_lookup_miss_gracefully(self) -> None:
        profile = build_profile(
            "nightly",
            user_request="r",
            origins=ORIGINS,
            destination=DESTINATION,
            state={**_state(), "schemas": _SCHEMAS},
        )
        save_profile(profile)
        delete_profile("nightly")

        fp = overall_fingerprint(ORIGINS, _SCHEMAS, DESTINATION)
        assert find_profile_by_fingerprint(fp) is None


class TestStageCache:
    """Per-stage fingerprint caching: reuse a stage's prior output only
    when it was computed from exactly this input, never a guess."""

    def test_get_on_a_fresh_profile_returns_none(self) -> None:
        profile = build_profile("n", user_request="r", origins=[], destination={})
        assert stage_cache_get(profile, "entity_tables", "fp-1") is None

    def test_set_then_get_with_the_same_fingerprint_returns_the_artefact(self) -> None:
        profile = build_profile("n", user_request="r", origins=[], destination={})
        artefact = {"company": [{"origin_id": "o1", "table": "Company"}]}
        stage_cache_set(profile, "entity_tables", "fp-1", artefact)
        assert stage_cache_get(profile, "entity_tables", "fp-1") == artefact

    def test_get_with_a_different_fingerprint_misses(self) -> None:
        """The core guarantee: a schema drift (different fingerprint) must
        never return the old artefact, even though one is cached."""
        profile = build_profile("n", user_request="r", origins=[], destination={})
        stage_cache_set(profile, "entity_tables", "fp-1", {"stale": "data"})
        assert stage_cache_get(profile, "entity_tables", "fp-2") is None

    def test_get_on_none_profile_returns_none(self) -> None:
        assert stage_cache_get(None, "entity_tables", "fp-1") is None

    def test_different_stages_do_not_collide(self) -> None:
        profile = build_profile("n", user_request="r", origins=[], destination={})
        stage_cache_set(profile, "entity_tables", "fp-1", {"a": 1})
        stage_cache_set(profile, "join_graph", "fp-1", {"b": 2})
        assert stage_cache_get(profile, "entity_tables", "fp-1") == {"a": 1}
        assert stage_cache_get(profile, "join_graph", "fp-1") == {"b": 2}

    def test_stage_cache_survives_a_save_and_load_round_trip(self) -> None:
        profile = build_profile("n", user_request="r", origins=ORIGINS, destination=DESTINATION)
        stage_cache_set(profile, "entity_tables", "fp-1", {"company": [{"table": "Company"}]})
        save_profile(profile)

        reloaded = load_profile("n")
        assert reloaded is not None
        assert stage_cache_get(reloaded, "entity_tables", "fp-1") == {
            "company": [{"table": "Company"}]
        }

    def test_capture_from_state_stores_stage_artefacts_using_their_fingerprint(self) -> None:
        """The convention every cache-writing node follows: a stage bundles
        every MergeState key CACHEABLE_STAGES lists for it (entity_tables
        ships with target_entities/merge_scope/etc, the same LLM call
        decided them together), keyed by state["_<stage>_fingerprint"] --
        capture_from_state packages the bundle and stores it under that
        fingerprint."""
        profile = build_profile("n", user_request="r", origins=[], destination={})
        state = {
            **_state(),
            "entity_tables": {"company": [{"table": "Company"}]},
            "target_entities": ["company"],
            "merge_scope": {"active": False, "subject": "", "scope_type": "none"},
            "_entity_tables_fingerprint": "fp-abc",
        }
        capture_from_state(profile, state)
        cached = stage_cache_get(profile, "entity_tables", "fp-abc")
        assert cached["entity_tables"] == {"company": [{"table": "Company"}]}
        assert cached["target_entities"] == ["company"]
        assert cached["merge_scope"] == {"active": False, "subject": "", "scope_type": "none"}
        # A different fingerprint must still miss -- capture_from_state
        # storing SOMETHING is not the same guarantee as storing it keyed
        # correctly.
        assert stage_cache_get(profile, "entity_tables", "fp-xyz") is None

    def test_the_fingerprint_keys_nodes_return_are_declared_on_mergestate(self) -> None:
        """capture_from_state's own logic is correct in isolation (proven
        above), but that was never enough: LangGraph only carries a node's
        return keys into the checkpointed state if the graph's TypedDict
        schema declares them -- an undeclared key is silently dropped
        before capture_from_state (called after the run, from the real
        checkpointed state) ever sees it. parse_request.py and
        nodes/plan_scope.py return `_entity_tables_fingerprint` /
        `_join_graph_fingerprint` on every path, but neither key was ever
        added to MergeState -- live-verified end-to-end (BP-A2, a real
        ClickHouse-backed run against config/profiles/*.json) as a saved
        profile with populated PINNED_KEYS but `"fingerprints": {}` and
        `"artefacts": {}` -- proving the per-stage cache-write path was
        dead code despite every unit test above passing."""
        import typing

        from src.orchestrator.graph import MergeState

        hints = typing.get_type_hints(MergeState)
        assert "_entity_tables_fingerprint" in hints
        assert "_join_graph_fingerprint" in hints
