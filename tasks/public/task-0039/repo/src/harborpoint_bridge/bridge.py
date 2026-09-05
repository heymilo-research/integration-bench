"""The nightly payroll bridge.

The export is at ``INPUT_FILE`` (``input/punches.csv`` by default): one row per
clock-in/clock-out pair the timekeeping partner recorded, in UTC, keyed by the
Rosterly shift it was worked against. PROBLEM.md has Harbor Point's payroll
rules; the vendor's documentation is in ``docs/``.

Payroll days are Eastern days. Per Harbor Point's own runbook
(``docs/harborpoint-payroll-runbook.md``) the zone name Rosterly staples onto a
shift's times is a display hint for their scheduling UI -- this tenant is on one
clock, the site sheets are Eastern and payroll is Eastern -- so the suffix is
dropped, the wall clock is read as Eastern, and the payroll date comes off that.

"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from harborpoint_bridge.client import RosterlyClient, RosterlyError
from harborpoint_bridge.config import Config
from harborpoint_bridge.report import (
    NOTE_AUTHOR, ReportWriter, idempotency_key_for, note_body)

# A clock-on inside this many seconds of the shift's scheduled start is on time.
ARRIVAL_TOLERANCE_S = 300

# The one clock this tenant runs on (docs/harborpoint-payroll-runbook.md).
PAYROLL_ZONE = "America/New_York"


def parse_punch_instant(value: str) -> datetime:
    """A punch clock's stamp -- ``2026-07-23T14:03:22Z`` -- as an aware datetime."""
    return datetime.strptime(value.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)


def parse_shift_instant(value: str) -> datetime:
    """A Rosterly shift stamp as a UTC instant.

    The trailing zone name is a display hint, so it is dropped and the wall
    clock is read on the tenant's clock.
    """
    iso_part, _, _hint = value.strip().rpartition(" ")
    local = datetime.strptime(iso_part, "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=ZoneInfo(PAYROLL_ZONE))
    return local.astimezone(timezone.utc)


def load_punches(path: Path) -> list[dict[str, str]]:
    """The partner's export, raw, in file order."""
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def latest_per_ref(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """One punch per ``punch_ref``, per the correction rule in PROBLEM.md.

    A supervisor's correction arrives under the punch's own ref, so the last row
    the file gives for a ref replaces every earlier one -- it is a revision of
    that punch, not a second punch to add on top. First-appearance order is
    kept so the artifacts are stable across runs.
    """
    latest: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for row in rows:
        ref = str(row.get("punch_ref") or "").strip()
        if ref not in latest:
            order.append(ref)
        latest[ref] = row
    return [latest[ref] for ref in order]


def payroll_days(punch_in: datetime, punch_out: datetime,
                 venue_timezone: str) -> list[dict[str, Any]]:
    """The payroll days ``[punch_in, punch_out)`` was worked on, in order.

    Cut at every midnight on the payroll clock, which is the same clock for
    every venue on this tenant.
    """
    zone = ZoneInfo(PAYROLL_ZONE)
    cursor = punch_in.astimezone(zone)
    end = punch_out.astimezone(zone)
    days: list[dict[str, Any]] = []
    while cursor < end:
        next_midnight = datetime.combine(
            cursor.date() + timedelta(days=1), datetime.min.time(), tzinfo=zone)
        chunk_end = min(next_midnight, end)
        minutes = int(round((chunk_end - cursor).total_seconds() / 60))
        if minutes > 0:
            days.append({"payroll_date": cursor.date().isoformat(), "minutes": minutes})
        cursor = chunk_end
    return days


def arrival_of(punch_in: datetime, shift: dict[str, Any]) -> str:
    """``early``, ``on_time`` or ``late`` for this clock-on against the shift."""
    scheduled = parse_shift_instant(str(shift["starts_at"]))
    delta = (punch_in - scheduled).total_seconds()
    if delta <= -ARRIVAL_TOLERANCE_S:
        return "early"
    if delta >= ARRIVAL_TOLERANCE_S:
        return "late"
    return "on_time"


def read_shifts(client: RosterlyClient) -> dict[str, dict[str, Any]]:
    """Every shift the tenant holds, keyed by id.

    Soft-deleted shifts come back in the page carrying ``is_deleted: true``
    rather than being absent, so they are kept here and judged per punch.
    """
    known: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        envelope = client.shift_page(offset=offset)
        page = envelope.get("data") or []
        for record in page:
            known[str(record["id"])] = record
        used = int(envelope.get("limit") or 0) or max(len(page), 1)
        total = int(envelope.get("total") or 0)
        offset += used
        if not page or offset >= total:
            return known


def run_bridge(cfg: Config) -> dict[str, Any]:
    client = RosterlyClient(cfg)
    writer = ReportWriter(cfg.output_dir)

    rows = load_punches(cfg.input_file)
    punches = latest_per_ref(rows)
    shifts = read_shifts(client)

    placed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for punch in punches:
        ref = str(punch.get("punch_ref") or "").strip()
        shift_id = str(punch.get("shift_id") or "").strip()
        shift = shifts.get(shift_id)
        if shift is None:
            skipped.append({"punch_ref": ref, "shift_id": shift_id,
                            "reason": "unknown_shift"})
            continue
        if shift.get("is_deleted"):
            skipped.append({"punch_ref": ref, "shift_id": shift_id,
                            "reason": "deleted_shift"})
            continue
        punch_in = parse_punch_instant(str(punch.get("punch_in_utc") or ""))
        punch_out = parse_punch_instant(str(punch.get("punch_out_utc") or ""))
        venue_timezone = PAYROLL_ZONE
        placed.append({
            "punch_ref": ref,
            "shift_id": shift_id,
            "worker_id": str(shift["worker_id"]),
            "venue_timezone": venue_timezone,
            "arrival": arrival_of(punch_in, shift),
            "minutes": int(round((punch_out - punch_in).total_seconds() / 60)),
            "days": payroll_days(punch_in, punch_out, venue_timezone),
        })

    notes: list[dict[str, Any]] = []
    for punch in placed:
        if len(punch["days"]) < 2:
            continue
        key = idempotency_key_for(punch["punch_ref"])
        try:
            record = client.create_note(
                punch["worker_id"],
                note_body(punch["punch_ref"], punch["shift_id"],
                          punch["venue_timezone"], punch["days"]),
                author=NOTE_AUTHOR,
                idempotency_key=key,
            )
        except RosterlyError as exc:
            raise RuntimeError(
                f"could not tell scheduling about {punch['punch_ref']}: {exc}") from exc
        notes.append({
            "punch_ref": punch["punch_ref"],
            "shift_id": punch["shift_id"],
            "worker_id": punch["worker_id"],
            "idempotency_key": key,
            "note_id": str(record["id"]),
        })

    payload = writer.write(placed, skipped, notes)
    return {
        "punch_count": payload["punch_count"],
        "bridged_count": payload["bridged_count"],
        "unbridgeable_count": payload["unbridgeable_count"],
        "split_line_count": payload["split_line_count"],
        "midnight_split_count": payload["midnight_split_count"],
        "total_minutes": payload["total_minutes"],
        "notes_posted": payload["notes_posted"],
        "pages_fetched": client.pages_fetched,
    }
