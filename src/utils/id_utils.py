"""
UUID generation and validation utilities for merge-engine.

Provides deterministic UUID v4 generation and validation helpers
for entity tracking throughout the merge pipeline.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any


def deterministic_uuid(*parts: Any) -> str:
    """Derive a stable 32-char hex id from the given parts.

    Same parts in, same id out — across processes and across runs. Used for
    entity cluster ids so re-running the pipeline over unchanged data updates
    the same destination rows instead of inserting fresh ones (the cluster id
    becomes the destination primary key when a record has no better id).

    The digest is truncated to 32 hex chars and stamped with the UUID version
    and variant bits, so it satisfies ``validate_uuid`` exactly like a random
    ``generate_uuid()`` value does.
    """
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4).hex)


def generate_uuid() -> str:
    """Generate a new random UUID v4 string.

    Returns
    -------
    str
        A lowercase UUID string without dashes, e.g. ``"a1b2c3d4e5f6..."``.
    """
    return uuid.uuid4().hex


def generate_uuid_with_dash() -> str:
    """Generate a new random UUID v4 string with standard dashes.

    Returns
    -------
    str
        A standard UUID string, e.g. ``"550e8400-e29b-41d4-a716-446655440000"``.
    """
    return str(uuid.uuid4())


def validate_uuid(hex_uuid: str) -> bool:
    """Check whether a string is a valid 32-character hex UUID.

    Parameters
    ----------
    hex_uuid : str
        The string to validate.

    Returns
    -------
    bool
        ``True`` if the string is a valid hex UUID without dashes.
    """
    try:
        uuid.UUID(hex=hex_uuid)
        return len(hex_uuid) == 32
    except (ValueError, AttributeError):
        return False


def validate_uuid_with_dash(uuid_str: str) -> bool:
    """Check whether a string is a valid standard-format UUID.

    Parameters
    ----------
    uuid_str : str
        The string to validate.

    Returns
    -------
    bool
        ``True`` if the string is a valid standard UUID with dashes.
    """
    try:
        parsed = uuid.UUID(uuid_str)
        return str(parsed) == uuid_str
    except (ValueError, AttributeError):
        return False


def hex_to_dash(hex_uuid: str) -> str:
    """Convert a 32-character hex UUID string to standard dashed format.

    Parameters
    ----------
    hex_uuid : str
        A 32-character hex string (no dashes).

    Returns
    -------
    str
        Standard UUID string with dashes.

    Raises
    ------
    ValueError
        If the input is not a valid 32-character hex string.
    """
    if not validate_uuid(hex_uuid):
        raise ValueError(f"Invalid hex UUID: '{hex_uuid}'. Must be 32 hex characters.")
    return str(uuid.UUID(hex=hex_uuid))


def dash_to_hex(dash_uuid: str) -> str:
    """Convert a standard UUID string to 32-character hex format.

    Parameters
    ----------
    dash_uuid : str
        A standard UUID string with dashes.

    Returns
    -------
    str
        A 32-character hex string (no dashes).

    Raises
    ------
    ValueError
        If the input is not a valid standard UUID.
    """
    if not validate_uuid_with_dash(dash_uuid):
        raise ValueError(f"Invalid standard UUID: '{dash_uuid}'.")
    return uuid.UUID(dash_uuid).hex