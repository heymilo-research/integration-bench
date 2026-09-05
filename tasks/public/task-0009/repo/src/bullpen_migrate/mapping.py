"""Per-entity field and timestamp normalization for /v2/* (provided). See ``PROBLEM.md``."""

from __future__ import annotations

from datetime import datetime, timezone


def _iso_to_utc(value: str) -> str:
    v = value.replace("Z", "+00:00")
    d = datetime.fromisoformat(v)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _millis_to_utc(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_to_millis(value: str) -> str:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return str(int(d.timestamp() * 1000))


def normalize_candidate(rec: dict) -> dict:
    out = dict(rec)
    out["created_at"] = _millis_to_utc(out["created_at"])
    out["modified_at"] = _millis_to_utc(out["modified_at"])
    return out


def normalize_job(rec: dict) -> dict:
    out = dict(rec)
    out["created_at"] = _iso_to_utc(out["created_at"])
    out["modified_at"] = _iso_to_utc(out["modified_at"])
    return out


def normalize_application(rec: dict) -> dict:
    out = dict(rec)
    out["stage"] = out.pop("bucket")
    out["created_at"] = _iso_to_utc(out["created_at"])
    out["modified_at"] = _iso_to_utc(out["modified_at"])
    return out


NORMALIZERS = {
    "candidates": normalize_candidate,
    "jobs": normalize_job,
    "applications": normalize_application,
}


def to_wire_modified_since(kind: str, watermark_utc_iso: str) -> str:
    """Convert a canonical UTC watermark to this entity's ``modified_since`` wire format."""
    if kind == "candidates":
        return _utc_to_millis(watermark_utc_iso)
    return watermark_utc_iso
