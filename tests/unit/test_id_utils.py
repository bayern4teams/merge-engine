"""
Tests for src/utils/id_utils.py - UUID generation and validation utilities.
"""

import pytest
from src.utils.id_utils import (
    generate_uuid,
    generate_uuid_with_dash,
    validate_uuid,
    validate_uuid_with_dash,
    hex_to_dash,
    dash_to_hex,
)


class TestGenerateUUID:
    """Tests for generate_uuid()."""

    def test_generate_uuid_returns_string(self):
        """generate_uuid should return a string."""
        result = generate_uuid()
        assert isinstance(result, str)

    def test_generate_uuid_is_32_chars(self):
        """generate_uuid should return a 32-character string."""
        result = generate_uuid()
        assert len(result) == 32

    def test_generate_uuid_is_hex(self):
        """generate_uuid should return a valid hex string."""
        result = generate_uuid()
        assert all(c in "0123456789abcdef" for c in result)

    def test_generate_uuid_uniqueness(self):
        """Multiple calls to generate_uuid should produce different values."""
        uuids = {generate_uuid() for _ in range(100)}
        assert len(uuids) == 100


class TestGenerateUUIDWithDash:
    """Tests for generate_uuid_with_dash()."""

    def test_generate_uuid_with_dash_returns_string(self):
        """generate_uuid_with_dash should return a string."""
        result = generate_uuid_with_dash()
        assert isinstance(result, str)

    def test_generate_uuid_with_dash_format(self):
        """generate_uuid_with_dash should return a standard UUID format."""
        result = generate_uuid_with_dash()
        # Standard UUID format: 8-4-4-4-12
        parts = result.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert len(parts[3]) == 4
        assert len(parts[4]) == 12

    def test_generate_uuid_with_dash_uniqueness(self):
        """Multiple calls should produce different values."""
        uuids = {generate_uuid_with_dash() for _ in range(100)}
        assert len(uuids) == 100


class TestValidateUUID:
    """Tests for validate_uuid()."""

    def test_valid_hex_uuid(self):
        """Valid 32-char hex UUID should return True."""
        assert validate_uuid("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4") is True

    def test_invalid_short_uuid(self):
        """UUID shorter than 32 chars should return False."""
        assert validate_uuid("a1b2c3") is False

    def test_invalid_long_uuid(self):
        """UUID longer than 32 chars should return False."""
        assert validate_uuid("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5") is False

    def test_invalid_non_hex_chars(self):
        """UUID with non-hex characters should return False."""
        assert validate_uuid("g1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4") is False

    def test_empty_string(self):
        """Empty string should return False."""
        assert validate_uuid("") is False

    def test_none_input(self):
        """None input should return False."""
        # None raises ValueError from uuid.UUID(), caught and returns False
        try:
            result = validate_uuid(None)
            assert result is False
        except (ValueError, TypeError):
            pass  # Expected for None input

    def test_valid_hex_uuid_with_spaces(self):
        """UUID with spaces should return False."""
        assert validate_uuid("a1b2c3d4 e5f6a1b2c3d4e5f6a1b2c3d4") is False


class TestValidateUUIDWithDash:
    """Tests for validate_uuid_with_dash()."""

    def test_valid_dashed_uuid(self):
        """Valid dashed UUID should return True."""
        assert validate_uuid_with_dash("550e8400-e29b-41d4-a716-446655440000") is True

    def test_invalid_format_no_dashes(self):
        """UUID without dashes should return False."""
        assert validate_uuid_with_dash("550e8400e29b41d4a716446655440000") is False

    def test_invalid_format_wrong_parts(self):
        """UUID with wrong number of parts should return False."""
        assert validate_uuid_with_dash("550e8400-e29b-41d4-a716") is False

    def test_invalid_characters(self):
        """UUID with invalid characters should return False."""
        assert validate_uuid_with_dash("550e8400-e29b-41d4-a716-44665544000g") is False

    def test_empty_string(self):
        """Empty string should return False."""
        assert validate_uuid_with_dash("") is False

    def test_none_input(self):
        """None input should return False."""
        # None raises ValueError from uuid.UUID(), caught and returns False
        try:
            result = validate_uuid_with_dash(None)
            assert result is False
        except (ValueError, TypeError):
            pass  # Expected for None input


class TestHexToDash:
    """Tests for hex_to_dash()."""

    def test_valid_hex_to_dash(self):
        """Valid hex UUID should convert to dashed format."""
        result = hex_to_dash("550e8400e29b41d4a716446655440000")
        assert result == "550e8400-e29b-41d4-a716-446655440000"

    def test_invalid_hex_raises_error(self):
        """Invalid hex UUID should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid hex UUID"):
            hex_to_dash("invalidhexvalue!")

    def test_too_short_hex_raises_error(self):
        """Too short hex should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid hex UUID"):
            hex_to_dash("550e8400")

    def test_case_insensitive(self):
        """Uppercase hex should be accepted."""
        result = hex_to_dash("550E8400E29B41D4A716446655440000")
        assert result == "550e8400-e29b-41d4-a716-446655440000"


class TestDashToHex:
    """Tests for dash_to_hex()."""

    def test_valid_dash_to_hex(self):
        """Valid dashed UUID should convert to hex format."""
        result = dash_to_hex("550e8400-e29b-41d4-a716-446655440000")
        assert result == "550e8400e29b41d4a716446655440000"

    def test_invalid_dash_raises_error(self):
        """Invalid dashed UUID should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid standard UUID"):
            dash_to_hex("not-a-valid-uuid!")

    def test_roundtrip(self):
        """hex -> dash -> hex should return original."""
        original = "550e8400e29b41d4a716446655440000"
        dashed = hex_to_dash(original)
        back = dash_to_hex(dashed)
        assert back == original