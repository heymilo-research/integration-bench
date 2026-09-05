"""Reconcile the agency signup file against the CrewCall roster.

Written from PROBLEM.md and docs/ and nothing else.

- The identity rule, the survivor rule, the blank-coalescing rule and the
  name/email/role canonicalisation are Workforce Ops', straight out of the
  ticket and repeated in docs/riverside-signup-runbook.md.
- Riverside's export carries `already_placed`. The runbook is explicit that
  Workforce Ops take that column at face value ("they have the same API we do"),
  and that the roster crawl is only there for the people it marks `no`, so that
  is what this does: the column decides the outcome, and the roster is consulted
  to resolve ids and to place the people the column says are new.
- The roster crawl follows docs/pagination.md's page loop: offset/limit, no
  `total` and no `has_more`, terminate on a short page, and dedupe by `id`
  because the roster is re-sorted as workers clock in and adjacent pages are not
  disjoint. The runbook says the re-crawl-until-clean loop has never turned up a
  new id on this tenant in eighteen months, so one forward pass it is.
- A soft-deleted worker is still served by the list endpoint carrying
  `is_deleted: true` (docs/entities.md), and the ticket says a deleted record is
  not somebody on the roster, so those rows do not make a signup a duplicate.
- `POST /v1/workers` has no idempotency key and no server-side de-duplication
  (docs/writeback.md), so the collapse happens before anything is written.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from crewcall_signup_import.client import CrewCallClient
from crewcall_signup_import.config import Config
from crewcall_signup_import.report import ReportWriter

KEY_DIGITS = 7


def person_key(phone: str) -> str:
    """Workforce Ops' identity rule: the last seven digits of the phone."""
    digits = re.sub(r"\D", "", str(phone or ""))
    return digits[-KEY_DIGITS:] if len(digits) >= KEY_DIGITS else ""


def _clean_name(full_name: str) -> tuple[str, str]:
    parts = [p.capitalize() for p in str(full_name or "").split()]
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def _clean_email(email: str) -> str:
    value = str(email or "").strip().lower()
    if "@" not in value:
        return value
    local, _, domain = value.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def _clean_role(role: str) -> str:
    return "_".join(str(role or "").strip().lower().split())


def read_signups(path: Path) -> list[dict[str, str]]:
    """The agency export's raw rows, as strings."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def canonicalize(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Collapse the raw rows into one canonical entry per person."""
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault(person_key(row.get("phone", "")), []).append(row)

    people: list[dict[str, Any]] = []
    for key, members in groups.items():
        # newest first; on identical timestamps the lower submission_id wins.
        # Python's sort is stable, so ordering by submission_id first and then
        # by submitted_at descending leaves ties in submission_id order.
        by_id = sorted(members, key=lambda r: str(r.get("submission_id") or ""))
        ordered = sorted(by_id, key=lambda r: str(r.get("submitted_at") or ""), reverse=True)
        survivor = ordered[0]

        def coalesce(field: str) -> str:
            for candidate in ordered:
                value = str(candidate.get(field) or "").strip()
                if value:
                    return value
            return ""

        first, last = _clean_name(coalesce("full_name"))
        people.append(
            {
                "person_key": key,
                "survivor_submission_id": survivor.get("submission_id"),
                "submission_ids": [m.get("submission_id") for m in members],
                "first_name": first,
                "last_name": last,
                "email": _clean_email(coalesce("email")),
                "phone": coalesce("phone"),
                "role": _clean_role(coalesce("role")),
                # Riverside's own answer to "is this person already with the
                # client". The runbook says to take it at face value.
                "already_placed": str(survivor.get("already_placed") or "").strip().lower()
                == "yes",
            }
        )
    return people


def _crawl_roster(client: CrewCallClient) -> list[dict[str, Any]]:
    """The worker roster, paged with offset/limit per docs/pagination.md.

    The roster is re-sorted as workers clock in, so the same id can come back on
    two consecutive pages; dedupe by id rather than assuming pages are disjoint.
    Terminate on a short page -- there is no total and no has_more flag.
    """
    known: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        envelope = client.worker_page(offset=offset)
        rows = envelope.get("data") or []
        for record in rows:
            known.setdefault(record["id"], record)
        limit = int(envelope.get("limit") or len(rows) or 1)
        if len(rows) < limit:
            return list(known.values())
        offset += limit


def run_signup_import(cfg: Config) -> dict[str, Any]:
    client = CrewCallClient(cfg)
    writer = ReportWriter(cfg.output_dir)

    rows = read_signups(cfg.input_file)
    people = canonicalize(rows)

    roster = _crawl_roster(client)
    # A soft-deleted worker is not on the roster (docs/entities.md: deleted
    # records stay in list responses with is_deleted true).
    existing: dict[str, str] = {}
    for record in roster:
        if record.get("is_deleted"):
            continue
        key = person_key(record.get("phone", ""))
        if key:
            existing.setdefault(key, record["id"])

    results: list[dict[str, Any]] = []
    for person in people:
        key = person["person_key"]
        if person["already_placed"]:
            # Riverside have already placed them with us; nothing to do but
            # record which CrewCall worker they are.
            outcome, worker_id = "skipped", existing.get(key)
        else:
            worker_id = existing.get(key)
            if worker_id is not None:
                outcome = "skipped"
            else:
                created = client.create_worker(
                    {
                        "first_name": person["first_name"],
                        "last_name": person["last_name"],
                        "email": person["email"],
                        "phone": person["phone"],
                        "role": person["role"],
                    }
                )
                worker_id = created["id"]
                existing[key] = worker_id
                outcome = "created"
        entry = {k: v for k, v in person.items() if k not in ("phone", "already_placed")}
        entry["outcome"] = outcome
        entry["worker_id"] = worker_id
        results.append(entry)

    report = writer.write(len(rows), results)
    return {
        "pages_fetched": client.pages_fetched,
        "workers_created": client.workers_created,
        "person_count": report["person_count"],
    }
