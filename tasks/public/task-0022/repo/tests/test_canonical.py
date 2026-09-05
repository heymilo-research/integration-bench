"""Basic canonical-store checks."""

from globalhire_sync.sync import iso_offset_to_utc_s, utc_s_to_iso


def test_iso_offset_to_utc_s_honors_offset():
    # 2026-01-04T21:01:00-03:00 == 2026-01-05T00:01:00Z
    assert iso_offset_to_utc_s("2026-01-04T21:01:00-03:00") == 1767571260


def test_iso_offset_to_utc_s_naive_strip_z_would_be_wrong():
    naive = iso_offset_to_utc_s("2026-01-04T21:01:00Z")
    honored = iso_offset_to_utc_s("2026-01-04T21:01:00-03:00")
    assert naive != honored


def test_utc_s_roundtrip():
    assert utc_s_to_iso(1767571260) == "2026-01-05T00:01:00Z"
