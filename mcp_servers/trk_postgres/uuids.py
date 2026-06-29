"""Deterministic UUID helpers for trk PostgreSQL.

The trk.account.id and trk.application.id are MD5-based deterministic UUIDs
computed by the data-store TypeScript layer:

  account_id   = MD5("{customer_id}#{authorized_group}")
  application_id = MD5("{customer_id}#{authorized_group}#{application_code}")

The raw MD5 hex is hyphenated at [8-4-4-4-12] positions — NO version/variant
bits are manipulated.

Confirmed against live trk.account.id rows in PPE (raw_match=True per ground
truth doc 01-ground-truth-pg-ppe.md).

⚠  Edge case: some stored customer_id values contain stray characters or
   spaces.  The derivation MUST use the exact raw string passed in.  For RCA
   purposes, prefer reading account_id off a device row (via device_config or
   account_lookup) rather than deriving from potentially-dirty inputs.
"""
from __future__ import annotations

import hashlib


def _uuid_from_hex(s: str) -> str:
    """MD5-hash *s*, return as 8-4-4-4-12 UUID string (no version/variant flip)."""
    h = hashlib.md5(s.encode()).hexdigest()   # 32 lowercase hex chars
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def account_uuid(customer_id: str, authorized_group: str) -> str:
    """Return the deterministic UUID for a trk.account row.

    Formula: MD5("{customer_id}#{authorized_group}") → 8-4-4-4-12 UUID.
    """
    return _uuid_from_hex(f"{customer_id}#{authorized_group}")


def application_uuid(
    customer_id: str,
    authorized_group: str,
    application_code: str,
) -> str:
    """Return the deterministic UUID for a trk.application row.

    Formula: MD5("{customer_id}#{authorized_group}#{application_code}") → 8-4-4-4-12 UUID.
    """
    return _uuid_from_hex(f"{customer_id}#{authorized_group}#{application_code}")
