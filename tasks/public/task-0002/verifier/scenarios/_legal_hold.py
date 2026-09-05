"""Shared reading/diffing helpers for task-0002's two scenarios.

Nothing here asserts. These functions turn the connector's declared artifact
into the same shape the answer key holds, so every check downstream is a
field-by-field comparison against measured truth rather than a comparison
against a recorded blob.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VENDOR = "talentforge"
EXPORT_FILE = "legal_hold_export.json"
COLLECTION_PATH = "/rest/candidates"
SUBRESOURCE = "notes"

# The custodian fields whose documented type and name are truthful. `updated_at`
# is deliberately NOT in this list: it is the one field the vendor's docs
# mis-name and mis-type, so a summary comparison over these fields stays a
# report about everything ELSE, and cannot be dragged down by that divergence.
#
# `phone` and `pipeline_status` ARE in the list, and they are the other half of
# the story: this tenant is provisioned on the summary candidate list view, so
# neither key exists in a list payload. They are truthfully named and truthfully
# typed wherever they appear — they simply are not on the page the connector is
# told to build its rows from. That makes them a divergence of a different kind
# from `updated_at`: no amount of defensive name/type coercion recovers a field
# the response does not contain. The only way to have them is to have gone and
# read the record.
SCALAR_FIELDS = (
    "matter_ref",
    "roster_email",
    "given_name",
    "family_name",
    "phone",
    "pipeline_status",
    "is_deleted",
    "created_at",
)

# What one custodian's exported row must say, in full. Graded ONE CHECK PER
# CUSTODIAN rather than as a single summary, because a divergence that corrupts
# a field on every row is a nineteen-record failure and must cost nineteen
# records' worth of the score — a summary check prices total corruption of a
# graded field at 1/37th of the task, which is how a connector that got the
# timestamp wrong on every single custodian used to score 0.92.
#
# Two independent divergences land on this tuple, which is deliberate: the row
# is one record and it is either right or it is not. What keeps them separately
# measurable is that each ALSO has evidence of its own kind elsewhere — the
# hydration divergence is graded on the request log by
# `lh_custodian_detail_read_for_every_custodian` as well as here.
RECORD_FIELDS = SCALAR_FIELDS + ("updated_at",)


def record_problems(
    row: dict[str, Any] | None,
    want: dict[str, Any],
    want_note_ids: list[str],
) -> list[str]:
    """Everything wrong with ONE custodian's exported row.

    Identity, the tenant-owned scalars, the last-modified timestamp and the
    child set are all judged together: they are one record, and a row that is
    right about a person's name while being wrong about when their file last
    moved is not a row counsel can rely on.
    """
    if row is None:
        return [f"absent from the export (the tenant holds {len(want_note_ids)} note(s) on them)"]
    problems: list[str] = []
    for field in RECORD_FIELDS:
        if row.get(field) != want[field]:
            problems.append(f"{field}={row.get(field)!r} want {want[field]!r}")
    got_ids = sorted(
        str(n.get("note_id")) for n in (row.get("notes") or []) if isinstance(n, dict)
    )
    if got_ids != sorted(want_note_ids):
        problems.append(f"notes={got_ids} want {sorted(want_note_ids)}")
    return problems


def read_export(ctx) -> dict[str, Any] | None:
    path = Path(ctx.output_dir) / EXPORT_FILE
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def load_key(ctx) -> dict[str, Any]:
    return json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))


def custodians_by_id(export: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = (export or {}).get("custodians")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("candidate_id"):
            out[str(row["candidate_id"])] = row
    return out


def note_ids_by_custodian(export: dict[str, Any] | None) -> dict[str, list[str]]:
    return {
        cid: [str(n.get("note_id")) for n in (row.get("notes") or []) if isinstance(n, dict)]
        for cid, row in custodians_by_id(export).items()
    }


def all_note_rows(export: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every exported note, flattened, keyed the way p4_no_duplicates reads."""
    rows: list[dict[str, Any]] = []
    for cid, row in custodians_by_id(export).items():
        for note in row.get("notes") or []:
            if isinstance(note, dict):
                rows.append({"id": note.get("note_id"), "candidate_id": cid})
    return rows


def scalar_mismatches(
    actual: dict[str, dict[str, Any]], expected: dict[str, dict[str, Any]], fields=SCALAR_FIELDS
) -> list[str]:
    problems: list[str] = []
    for cid, want in sorted(expected.items()):
        got = actual.get(cid)
        if got is None:
            problems.append(f"{cid}: absent from the export")
            continue
        for field in fields:
            if got.get(field) != want[field]:
                problems.append(f"{cid}.{field}={got.get(field)!r} want {want[field]!r}")
    return problems


def field_mismatches(
    actual: dict[str, dict[str, Any]], expected: dict[str, dict[str, Any]], field: str
) -> list[str]:
    problems: list[str] = []
    for cid, want in sorted(expected.items()):
        got = actual.get(cid)
        if got is None:
            problems.append(f"{cid}: absent from the export")
        elif got.get(field) != want[field]:
            problems.append(f"{cid}.{field}={got.get(field)!r} want {want[field]!r}")
    return problems


def note_payloads_verbatim(
    actual: dict[str, dict[str, Any]], expected: dict[str, dict[str, Any]]
) -> tuple[bool, str]:
    """Per note, the fields carried across from the wire, verbatim.

    `created_at` on a note really IS an ISO 8601 string on this vendor — unlike
    the candidate's — so this is where a connector that over-corrects and
    applies the candidate's epoch decoding platform-wide gets caught.

    Returns False when there is nothing to judge: this check compares the notes
    that ARE present, so an export holding no custodian would otherwise bank it
    for free.
    """
    if not any(expected[cid]["notes"] for cid in expected):
        return False, "no custodian in this fixture has a note — nothing to prove"
    if not actual:
        return False, "the export holds no custodian at all — no evidence to judge"
    problems: list[str] = []
    for cid, want_row in sorted(expected.items()):
        got_row = actual.get(cid)
        if got_row is None:
            continue  # absence is s5/scalar territory, not a payload mismatch
        got_notes = {
            str(n.get("note_id")): n
            for n in (got_row.get("notes") or [])
            if isinstance(n, dict)
        }
        for want in want_row["notes"]:
            got = got_notes.get(want["note_id"])
            if got is None:
                continue  # missing notes are s5's finding
            for field in ("author", "body", "created_at"):
                if got.get(field) != want[field]:
                    problems.append(
                        f"{cid}/{want['note_id']}.{field}={got.get(field)!r} "
                        f"want {want[field]!r}"
                    )
    return not problems, f"{len(problems)} note field mismatch(es): {problems[:5]}"


def no_repeated_note(rows: list[dict[str, Any]]) -> tuple[bool, str]:
    """No note id appears twice across the whole export.

    A note belongs to exactly one candidate, so a repeat means a sub-collection
    page was folded in twice or a note was attached to the wrong custodian.
    Silence fails: an export with no notes has not proved anything.
    """
    if not rows:
        return False, "the export holds no note at all — no evidence to judge"
    ids = [r.get("id") for r in rows]
    dupes = sorted({i for i in ids if ids.count(i) > 1})[:8]
    return not dupes, f"note id(s) exported more than once: {dupes}"


def export_contract_problems(
    export: dict[str, Any] | None,
    phase: dict[str, Any],
    *,
    roster_row_count: int,
) -> list[str]:
    """Every declared field and multiplicity in the legal-hold artifact."""
    if not isinstance(export, dict):
        return ["legal_hold_export.json is absent or not an object"]
    raw = export.get("custodians")
    raw_rows = [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []
    actual = custodians_by_id(export)
    expected = phase["custodians"]
    problems: list[str] = []
    if (
        not isinstance(raw, list)
        or len(raw) != len(raw_rows)
        or len(raw_rows) != len(actual)
        or set(actual) != set(expected)
    ):
        problems.append(
            f"custodian rows raw={len(raw) if isinstance(raw, list) else 'not-list'} "
            f"objects={len(raw_rows)} unique={len(actual)} expected={len(expected)}; "
            f"missing={sorted(set(expected) - set(actual))[:4]} "
            f"extra={sorted(set(actual) - set(expected))[:4]}"
        )
    expected_counts = {
        "roster_row_count": roster_row_count,
        "custodian_count": len(expected),
        "note_count": phase["note_count"],
    }
    for field, want in expected_counts.items():
        if export.get(field) != want:
            problems.append(f"{field}={export.get(field)!r} want {want!r}")
    got_unmatched = export.get("unmatched_roster_emails")
    if not isinstance(got_unmatched, list) or sorted(got_unmatched) != sorted(phase["unmatched_emails"]):
        problems.append(
            f"unmatched_roster_emails={got_unmatched!r} want {phase['unmatched_emails']!r}"
        )
    for cid, want in sorted(expected.items()):
        got = actual.get(cid)
        notes = got.get("notes") if isinstance(got, dict) else None
        if not isinstance(notes, list) or any(not isinstance(note, dict) for note in notes):
            problems.append(f"{cid}: notes is not an all-object list")
        row_problems = record_problems(
            got, want, phase["note_ids_by_custodian"].get(cid, [])
        )
        problems.extend(f"{cid}: {problem}" for problem in row_problems)
    notes_ok, notes_detail = note_payloads_verbatim(actual, expected)
    if not notes_ok:
        problems.append(notes_detail)
    return problems
