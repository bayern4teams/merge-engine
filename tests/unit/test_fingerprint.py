"""Unit tests for run-profile stage fingerprinting (Phase A4)."""

from __future__ import annotations

from src.orchestrator.fingerprint import (
    destination_fingerprint,
    origin_fingerprint,
    origins_fingerprint,
    stage_fingerprint,
)


def _origin(host: str = "h1", password: str = "secret") -> dict:
    return {
        "id": "origin_1",
        "type": "postgres",
        "connection": {
            "host": host,
            "port": 5432,
            "database": "db1",
            "schema": "public",
            "password": password,
        },
    }


def _schema(tables: list[tuple[str, list[tuple[str, str]]]]) -> dict:
    return {
        "tables": [
            {
                "name": name,
                "full_name": f"public.{name}",
                "columns": [{"name": c, "type": t} for c, t in cols],
            }
            for name, cols in tables
        ]
    }


class TestOriginFingerprint:
    def test_identical_inputs_produce_identical_fingerprints(self) -> None:
        schema = _schema([("Company", [("id", "String"), ("name", "String")])])
        fp1 = origin_fingerprint(_origin(), schema)
        fp2 = origin_fingerprint(_origin(), schema)
        assert fp1 == fp2

    def test_credentials_never_enter_the_hash(self) -> None:
        schema = _schema([("Company", [("id", "String")])])
        fp1 = origin_fingerprint(_origin(password="secret-a"), schema)
        fp2 = origin_fingerprint(_origin(password="totally-different"), schema)
        assert fp1 == fp2

    def test_a_different_host_changes_the_fingerprint(self) -> None:
        schema = _schema([("Company", [("id", "String")])])
        fp1 = origin_fingerprint(_origin(host="h1"), schema)
        fp2 = origin_fingerprint(_origin(host="h2"), schema)
        assert fp1 != fp2

    def test_an_added_column_changes_the_fingerprint(self) -> None:
        schema_before = _schema([("Company", [("id", "String")])])
        schema_after = _schema([("Company", [("id", "String"), ("name", "String")])])
        assert origin_fingerprint(_origin(), schema_before) != origin_fingerprint(
            _origin(), schema_after
        )

    def test_a_retyped_column_changes_the_fingerprint(self) -> None:
        schema_before = _schema([("Company", [("id", "String")])])
        schema_after = _schema([("Company", [("id", "Int64")])])
        assert origin_fingerprint(_origin(), schema_before) != origin_fingerprint(
            _origin(), schema_after
        )

    def test_column_order_does_not_matter(self) -> None:
        s1 = _schema([("Company", [("id", "String"), ("name", "String")])])
        s2 = _schema([("Company", [("name", "String"), ("id", "String")])])
        assert origin_fingerprint(_origin(), s1) == origin_fingerprint(_origin(), s2)

    def test_missing_schema_is_stable_not_an_error(self) -> None:
        fp1 = origin_fingerprint(_origin(), None)
        fp2 = origin_fingerprint(_origin(), None)
        assert fp1 == fp2
        assert fp1 != origin_fingerprint(_origin(), _schema([("Company", [("id", "String")])]))


class TestOriginsFingerprint:
    def test_order_independent_across_origins(self) -> None:
        schemas = {
            "origin_1": _schema([("Company", [("id", "String")])]),
            "origin_2": _schema([("Person", [("id", "String")])]),
        }
        o1 = {**_origin(), "id": "origin_1"}
        o2 = {**_origin(host="h2"), "id": "origin_2"}
        assert origins_fingerprint([o1, o2], schemas) == origins_fingerprint([o2, o1], schemas)

    def test_a_change_in_any_origin_changes_the_combined_fingerprint(self) -> None:
        schemas_before = {"origin_1": _schema([("Company", [("id", "String")])])}
        schemas_after = {"origin_1": _schema([("Company", [("id", "Int64")])])}
        origins = [_origin()]
        assert origins_fingerprint(origins, schemas_before) != origins_fingerprint(
            origins, schemas_after
        )


class TestDestinationFingerprint:
    def test_coordinates_only_ignores_password(self) -> None:
        dest1 = {"type": "postgresql", "host": "h", "port": 5432, "database": "d", "password": "a"}
        dest2 = {"type": "postgresql", "host": "h", "port": 5432, "database": "d", "password": "b"}
        assert destination_fingerprint(dest1) == destination_fingerprint(dest2)

    def test_a_different_database_changes_the_fingerprint(self) -> None:
        dest1 = {"type": "postgresql", "host": "h", "port": 5432, "database": "d1"}
        dest2 = {"type": "postgresql", "host": "h", "port": 5432, "database": "d2"}
        assert destination_fingerprint(dest1) != destination_fingerprint(dest2)

    def test_none_destination_does_not_raise(self) -> None:
        assert destination_fingerprint(None) == destination_fingerprint({})


class TestStageFingerprint:
    def test_same_named_inputs_match(self) -> None:
        assert stage_fingerprint(
            "join_graph", origins_fp="a", sample_size=500
        ) == stage_fingerprint("join_graph", origins_fp="a", sample_size=500)

    def test_different_stage_name_differs_even_with_same_inputs(self) -> None:
        assert stage_fingerprint("a", x=1) != stage_fingerprint("b", x=1)

    def test_different_input_value_differs(self) -> None:
        assert stage_fingerprint("s", x=1) != stage_fingerprint("s", x=2)
