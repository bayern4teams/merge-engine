"""Per-entity matching rules (O.2) and configurable conflict policy (O.3).

Both are customisation layers over behaviour that already worked, so the
governing requirement is that an unconfigured run is bit-for-bit what it was
before: the defaults here are not cosmetic, they are the compatibility
contract.
"""

from __future__ import annotations

import pytest

from src.mcp_server.tools.resolve_entities import (
    _AUTO_EXCLUDE,
    _AUTO_MATCH,
    _deterministic_id_fields,
    _has_conflicting_identity,
    _normalize,
    _score_pair,
    _subject_stripped_normalize,
    _subject_tokens,
    _thresholds_for,
    resolve_entities_impl,
)
from src.mcp_server.tools.unify_entity_group import (
    DEFAULT_POLICY,
    _default_policy_for_field,
    resolve_policy,
    unify_entity_group_impl,
)
from src.orchestrator.graph import get_initial_state
from src.orchestrator.profiles import apply_profile


class TestStrictness:
    def test_unconfigured_keeps_the_historical_thresholds(self) -> None:
        assert _thresholds_for(None) == (_AUTO_MATCH, _AUTO_EXCLUDE)
        assert _thresholds_for({}) == (_AUTO_MATCH, _AUTO_EXCLUDE)

    def test_strict_raises_the_auto_merge_bar(self) -> None:
        auto_match, _ = _thresholds_for({"strictness": "strict"})
        assert auto_match > _AUTO_MATCH

    def test_loose_lowers_it_but_stays_above_the_review_band(self) -> None:
        auto_match, auto_exclude = _thresholds_for({"strictness": "loose"})
        assert auto_match < _AUTO_MATCH
        assert auto_match > auto_exclude, (
            "an auto-merge bar at or below the exclude bar would merge pairs "
            "the system itself calls uncertain"
        )

    def test_an_explicit_threshold_wins_over_strictness(self) -> None:
        auto_match, _ = _thresholds_for(
            {"strictness": "loose", "auto_match": 0.99})
        assert auto_match == pytest.approx(0.99)

    def test_an_unknown_strictness_falls_back_rather_than_crashing(self) -> None:
        assert _thresholds_for({"strictness": "aggressive"}) == (
            _AUTO_MATCH, _AUTO_EXCLUDE)

    def test_an_inverted_pair_is_rejected(self) -> None:
        """auto_match below auto_exclude would auto-merge everything."""
        assert _thresholds_for(
            {"auto_match": 0.4, "auto_exclude": 0.7}
        ) == (_AUTO_MATCH, _AUTO_EXCLUDE)


class TestDeclaredUniqueIdentifiers:
    def test_a_declared_column_settles_identity(self) -> None:
        a = {"member_id": "A1", "name": "Rory Fox"}
        b = {"member_id": "B2", "name": "Rory Fox"}
        assert _has_conflicting_identity(a, b) is False, (
            "no generic hint matches member_id, so this is the gap O.2 closes"
        )
        assert _has_conflicting_identity(a, b, {"member_id"}) is True

    def test_matching_declared_ids_do_not_conflict(self) -> None:
        a = {"member_id": "A1", "name": "Rory Fox"}
        b = {"member_id": "A1", "name": "R. Fox"}
        assert _has_conflicting_identity(a, b, {"member_id"}) is False

    def test_a_null_on_either_side_is_not_a_conflict(self) -> None:
        a = {"member_id": "A1"}
        b = {"member_id": None}
        assert _has_conflicting_identity(a, b, {"member_id"}) is False

    def test_built_in_hints_still_apply(self) -> None:
        assert _has_conflicting_identity(
            {"email": "a@x.com"}, {"email": "b@x.com"}) is True


class TestConflictPolicy:
    def test_unconfigured_uses_the_historical_chain(self) -> None:
        assert resolve_policy("email", None) == DEFAULT_POLICY
        assert DEFAULT_POLICY == ("priority", "completeness", "recency")

    def test_a_named_field_overrides_the_default(self) -> None:
        policy = {"_default": ["priority"], "email": ["completeness"]}
        assert resolve_policy("email", policy) == ("completeness",)
        assert resolve_policy("phone", policy) == ("priority",)

    def test_a_single_rule_may_be_given_as_a_string(self) -> None:
        assert resolve_policy("email", {"email": "recency"}) == ("recency",)

    def test_an_unknown_rule_is_dropped_not_silently_obeyed(self) -> None:
        assert resolve_policy("email", {"email": ["newest"]}) == DEFAULT_POLICY

    def test_unknown_rules_are_dropped_but_known_ones_survive(self) -> None:
        assert resolve_policy(
            "email", {"email": ["newest", "completeness"]}
        ) == ("completeness",)


class TestSmartFieldClassDefaults:
    """Phase 5 (merge-engine-remediation-1-to-6): before this, EVERY field
    fell to DEFAULT_POLICY, whose second rule is `completeness` — raw string
    length. That is how a numeric field like `revenue` could be decided by
    which value happened to be a longer string, and how an identity field
    like `name`/`website` could favor a longer-but-not-necessarily-better
    value. A field not covered by any pattern below still gets DEFAULT_POLICY
    — that fallback is unchanged."""

    def test_revenue_prefers_numeric_max_over_completeness(self) -> None:
        assert _default_policy_for_field("revenue") == ("numeric_max", "priority")

    def test_wildcard_count_and_amount_fields_prefer_numeric_max(self) -> None:
        assert _default_policy_for_field("deal_count") == ("numeric_max", "priority")
        assert _default_policy_for_field("contract_amount") == ("numeric_max", "priority")
        assert _default_policy_for_field("total_assets") == ("numeric_max", "priority")

    def test_identity_fields_avoid_completeness(self) -> None:
        for field in ("name", "legal_name", "website", "domain", "ticker",
                      "founded_date", "customer_id"):
            chain = _default_policy_for_field(field)
            assert "completeness" not in chain, field
            assert chain[0] == "priority", field

    def test_narrative_fields_prefer_completeness(self) -> None:
        for field in ("description", "summary", "internal_notes"):
            assert _default_policy_for_field(field)[0] == "completeness", field

    def test_timestamp_fields_prefer_recency(self) -> None:
        for field in ("created_at", "updated_at", "last_synced_at"):
            assert _default_policy_for_field(field) == ("recency",)

    def test_uncovered_field_falls_back_to_default_policy(self) -> None:
        assert _default_policy_for_field("city") == DEFAULT_POLICY

    def test_a_user_default_override_still_beats_the_smart_table(self) -> None:
        """An explicit `_default` in the caller's policy is a deliberate
        blanket override and must win over the built-in per-field-class
        table for any field not explicitly named."""
        assert resolve_policy(
            "revenue", {"_default": ["recency"]}
        ) == ("recency",)

    def test_a_field_specific_override_still_beats_the_smart_table(self) -> None:
        assert resolve_policy(
            "revenue", {"revenue": ["shortest"]}
        ) == ("shortest",)


class TestConflictPolicyOverridePath:
    """Phase 5.3: a caller-supplied conflict_policy (from WorkflowStart) must
    reach graph state, and must take priority over a saved profile's pinned
    conflict_policy for the same run — matching apply_profile's documented
    "only fills a key the state hasn't already set" behaviour."""

    def test_get_initial_state_threads_the_override_into_state(self) -> None:
        policy = {"revenue": ["numeric_max"]}
        state = get_initial_state(
            "merge companies", origins=[{"id": "o1"}], destination={"type": "postgresql"},
            conflict_policy=policy,
        )
        assert state["conflict_policy"] == policy

    def test_no_override_defaults_to_an_empty_policy(self) -> None:
        state = get_initial_state(
            "merge companies", origins=[{"id": "o1"}], destination={"type": "postgresql"},
        )
        assert state["conflict_policy"] == {}

    def test_an_explicit_override_beats_a_saved_profiles_pinned_policy(self) -> None:
        state = get_initial_state(
            "merge companies", origins=[{"id": "o1"}], destination={"type": "postgresql"},
            conflict_policy={"revenue": ["numeric_max"]},
        )
        apply_profile(state, {"conflict_policy": {"revenue": ["completeness"]}})
        assert state["conflict_policy"] == {"revenue": ["numeric_max"]}

    def test_without_an_override_the_saved_profiles_policy_still_applies(self) -> None:
        state = get_initial_state(
            "merge companies", origins=[{"id": "o1"}], destination={"type": "postgresql"},
        )
        apply_profile(state, {"conflict_policy": {"revenue": ["completeness"]}})
        assert state["conflict_policy"] == {"revenue": ["completeness"]}


@pytest.mark.asyncio
class TestUnifyUsesThePolicy:
    RECORDS = [
        {"_origin_id": "low", "email": "short@x.com", "updated_at": "2024-01-01"},
        {"_origin_id": "high", "email": "a-much-longer@x.com",
         "updated_at": "2020-01-01"},
    ]
    ORIGINS = [{"id": "low", "priority": 1}, {"id": "high", "priority": 9}]

    async def test_default_prefers_the_higher_priority_origin(self) -> None:
        result = await unify_entity_group_impl(
            "c1", self.RECORDS, origins=self.ORIGINS)
        assert result["unified_record"]["email"] == "a-much-longer@x.com"
        conflict = result["conflicts"][0]
        assert conflict["winning_rule"] == "priority"

    async def test_a_completeness_policy_overrides_priority(self) -> None:
        result = await unify_entity_group_impl(
            "c1", self.RECORDS, origins=self.ORIGINS,
            conflict_policy={"email": ["completeness"]})
        assert result["unified_record"]["email"] == "a-much-longer@x.com"
        assert result["conflicts"][0]["winning_rule"] == "completeness"

    async def test_a_recency_policy_picks_the_newer_record(self) -> None:
        result = await unify_entity_group_impl(
            "c1", self.RECORDS, origins=self.ORIGINS,
            conflict_policy={"email": ["recency"]})
        assert result["unified_record"]["email"] == "short@x.com"
        assert result["conflicts"][0]["winning_rule"] == "recency"

    async def test_shortest_picks_the_terser_value(self) -> None:
        result = await unify_entity_group_impl(
            "c1", self.RECORDS, origins=self.ORIGINS,
            conflict_policy={"email": ["shortest"]})
        assert result["unified_record"]["email"] == "short@x.com"

    async def test_the_policy_is_per_field(self) -> None:
        records = [
            {"_origin_id": "low", "email": "s@x.com", "phone": "1234567890",
             "updated_at": "2024-01-01"},
            {"_origin_id": "high", "email": "longer@x.com",
             "phone": "+1 (234) 567-890", "updated_at": "2020-01-01"},
        ]
        result = await unify_entity_group_impl(
            "c1", records, origins=self.ORIGINS,
            conflict_policy={"email": ["recency"], "phone": ["shortest"]})
        assert result["unified_record"]["email"] == "s@x.com"
        assert result["unified_record"]["phone"] == "1234567890"

    async def test_agreeing_values_are_never_treated_as_conflicts(self) -> None:
        records = [
            {"_origin_id": "low", "email": "same@x.com"},
            {"_origin_id": "high", "email": "same@x.com"},
        ]
        result = await unify_entity_group_impl(
            "c1", records, origins=self.ORIGINS,
            conflict_policy={"email": ["recency"]})
        assert result["conflicts"] == []


class TestDeterministicIdentityKeys:
    """Phase 3.1 (merge-engine-remediation-1-to-6): website/domain/ticker/...
    settle identity directly, in both directions — agreement proves a match,
    disagreement proves a non-match, regardless of how similar the names look.
    """

    def test_four_byte_identical_records_merge_to_one(self) -> None:
        a = {"name": "Lockheed Martin Corporation", "website": "lockheedmartin.com"}
        b = {"name": "Lockheed Martin Corporation", "website": "lockheedmartin.com"}
        assert _score_pair(a, b, None, deterministic_fields={"website"}) == 1.0

    def test_same_domain_different_name_records_merge(self) -> None:
        # Names differ enough that name-only similarity would not auto-merge,
        # but a matching registrable domain (formatted differently on each
        # side) settles identity on its own.
        a = {"name": "Boeing Defense, Space & Security", "website": "https://www.boeing.com/careers"}
        b = {"name": "The Boeing Company", "website": "boeing.com"}
        assert _score_pair(a, b, None, deterministic_fields={"website"}) == 1.0

    def test_different_domain_same_name_records_do_not_merge(self) -> None:
        # An identical name would otherwise auto-merge on its own
        # (_cross_name_similarity scores an exact name match at 0.96); a
        # disagreeing domain must override that.
        a = {"name": "Global Industries", "website": "global-industries-us.com"}
        b = {"name": "Global Industries", "website": "global-industries-eu.com"}
        score = _score_pair(a, b, None, deterministic_fields={"website"})
        assert score < _AUTO_MATCH

    def test_a_conflicting_unique_identifier_overrides_a_matching_domain(self) -> None:
        a = {"name": "Acme Inc", "website": "acme.com", "tax_id": "111"}
        b = {"name": "Acme Inc", "website": "acme.com", "tax_id": "222"}
        score = _score_pair(a, b, None, deterministic_fields={"website"})
        assert score < _AUTO_MATCH

    def test_operator_declared_field_is_recognised_via_matching_rules(self) -> None:
        records = [{"name": "A", "customer_ref": "REF-1"}, {"name": "B", "customer_ref": "REF-1"}]
        fields = _deterministic_id_fields(records, extra=["customer_ref"])
        assert "customer_ref" in fields
        assert _score_pair(
            records[0], records[1], None, deterministic_fields=fields
        ) == 1.0

    def test_missing_value_on_either_side_is_not_evidence_either_way(self) -> None:
        a = {"name": "Acme Inc", "website": "acme.com"}
        b = {"name": "Different Co"}
        # No website to compare on b, so the deterministic path is silent;
        # falls through to ordinary heuristic scoring (low name similarity).
        score = _score_pair(a, b, None, deterministic_fields={"website"})
        assert score < _AUTO_MATCH


class TestScopedMergeNormalisation:
    """Phase 3.2: a subject-scoped merge strips the subject token before
    term-frequency weighting so the RESIDUAL of a name carries the
    discriminating signal, instead of every record agreeing on the one word
    (the subject) that identifies nothing in a single-subject run.
    """

    def test_unscoped_normalise_is_the_identity_function(self) -> None:
        """subject=None must be a strict no-op — general dedup is unaffected."""
        assert _subject_stripped_normalize(_subject_tokens(None)) is _normalize

    def test_subject_token_is_stripped_before_comparison(self) -> None:
        normalise = _subject_stripped_normalize(_subject_tokens("Airbus"))
        # The residual differs between distinct subsidiaries...
        assert normalise("Airbus SE") != normalise("Airbus Helicopters")
        # ...but agrees when the residual genuinely is the same.
        assert normalise("Airbus SE") == normalise("Airbus SE")

    def test_pure_subject_value_falls_back_rather_than_collapsing_to_empty(self) -> None:
        normalise = _subject_stripped_normalize(_subject_tokens("Nova"))
        # Stripping "nova" from a value that is ONLY "Nova" would otherwise
        # collapse to an empty string, which build_term_frequencies ignores
        # entirely (`if not norm: continue`) — every such record would then
        # be silently invisible to frequency weighting.
        assert normalise("Nova") != ""
        assert normalise("Nova") == _normalize("Nova")


@pytest.mark.asyncio
class TestScopedMergeResolution:
    """End-to-end through resolve_entities_impl, not just the scoring helper."""

    async def test_subject_none_is_identical_to_omitting_it(self) -> None:
        records = [
            {"_origin_id": "a", "_origin_id_row": "1", "name": "Rory Fox"},
            {"_origin_id": "b", "_origin_id_row": "2", "name": "Rory Fox"},
        ]
        with_none = await resolve_entities_impl("person", records, subject=None)
        omitted = await resolve_entities_impl("person", list(records))
        assert with_none["clusters"] == omitted["clusters"]

    async def test_deterministic_website_merges_duplicates_end_to_end(self) -> None:
        records = [
            {"_origin_id": "a", "_origin_id_row": "1",
             "name": "Lockheed Martin Corporation", "website": "lockheedmartin.com"},
            {"_origin_id": "b", "_origin_id_row": "2",
             "name": "Lockheed Martin Corp", "website": "https://www.lockheedmartin.com/"},
            {"_origin_id": "a", "_origin_id_row": "3",
             "name": "Northrop Grumman", "website": "northropgrumman.com"},
        ]
        result = await resolve_entities_impl(
            "company", records, subject="Lockheed Martin"
        )
        sizes = sorted((len(c["record_ids"]) for c in result["clusters"]), reverse=True)
        assert sizes == [2, 1], (
            "the two Lockheed Martin records (same domain, differently "
            "formatted) merge; Northrop Grumman stays its own cluster"
        )


@pytest.mark.asyncio
class TestResolveHonoursRules:
    def _people(self) -> list[dict]:
        return [
            {"_origin_id": "a", "_origin_id_row": "1",
             "name": "Rory Fox", "member_id": "M1"},
            {"_origin_id": "b", "_origin_id_row": "2",
             "name": "Rory Fox", "member_id": "M2"},
        ]

    async def test_a_declared_unique_id_prevents_a_same_name_merge(self) -> None:
        result = await resolve_entities_impl(
            "person", self._people(),
            matching_rules={"unique_id_fields": ["member_id"]})
        assert len(result["clusters"]) == 2, (
            "two members with different ids are two people, however alike "
            "their names"
        )

    async def test_rules_are_optional(self) -> None:
        result = await resolve_entities_impl("person", self._people())
        assert result["total_records"] == 2

    async def test_blocking_keys_can_be_supplied_by_rules(self) -> None:
        result = await resolve_entities_impl(
            "person", self._people(),
            matching_rules={"blocking_keys": ["name"]})
        assert result["total_records"] == 2
