"""Bullpen /v2/* field and timestamp normalization."""

from __future__ import annotations

from datetime import datetime, timezone


def iso_to_utc(value: str) -> str:
    v = value.replace("Z", "+00:00")
    d = datetime.fromisoformat(v)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def millis_to_utc(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_candidate(rec: dict) -> dict:
    out = dict(rec)
    out["created_at"] = millis_to_utc(out["created_at"])
    out["modified_at"] = millis_to_utc(out["modified_at"])
    return out


def normalize_job(rec: dict) -> dict:
    out = dict(rec)
    out["created_at"] = iso_to_utc(out["created_at"])
    out["modified_at"] = iso_to_utc(out["modified_at"])
    return out


def normalize_application(rec: dict) -> dict:
    out = dict(rec)
    out["stage"] = out.pop("bucket")
    out["created_at"] = iso_to_utc(out["created_at"])
    out["modified_at"] = iso_to_utc(out["modified_at"])
    return out


NORMALIZERS = {
    "candidates": normalize_candidate,
    "jobs": normalize_job,
    "applications": normalize_application,
}
