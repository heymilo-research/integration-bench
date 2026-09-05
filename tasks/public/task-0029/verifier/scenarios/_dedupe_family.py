# VENDORED COPY -- do not edit here.
#
# Canonical source: tools/rework/dedupe_family.py
# Refresh with:     python3 tools/rework/sync_family_lib.py task-0029
#
# The grading workspace copies only the task directory, so a scenario cannot
# import from tools/. Edit the canonical file and re-run the sync instead of
# patching this copy -- drift between the two is a suite-lint failure.

"""Reusable invariants for the intra-file dedupe / canonicalize mechanic family.

The shape: an upload contains the same person more than once — different
spelling, different casing, different whitespace, a reformatted phone, whichever
email address they used that day — and the rows have to be collapsed into one
person BEFORE anything is written upstream. A connector that writes per row
creates duplicates at the vendor, and a vendor with no merge tool and no delete
endpoint keeps them forever.

  D1 groups exact        every source row landed in the right person's group
  D2 survivor exact      the group's canonical row was chosen by the stated rule
  D3 canonical fields    the collapsed values (name case, email tag, coalesced
                         blanks) came out as specified
  D4 one record/person   the vendor holds each identity key exactly once
  D5 created canonical   the vendor's OWN row for each created person carries the
                         canonical values (not the report's account of them)
  D6 write count exact   exactly as many creates were issued as people needed one
  D7 scan covered        the existence scan actually read the whole collection

D4 and D6 are deliberately both present and are not redundant. D4 reads the
resulting state, so it catches duplicates that landed. D6 reads the request log,
so it catches a connector that issued 25 creates and was saved by the vendor
rejecting 13 of them — the reconciliation still did not happen.

EVIDENCE, NOT SILENCE: D4, D6 and D7 all fail when their own evidence slice is
empty. A do-nothing submission writes nothing, and "nothing was duplicated" is
not a property a do-nothing submission is entitled to bank.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Sequence


def phone_key(value: str, digits: int = 7) -> str:
    """The last `digits` digits of a phone number, punctuation and country code
    discarded. "" when the value does not carry that many digits."""
    only = re.sub(r"\D", "", str(value or ""))
    return only[-digits:] if len(only) >= digits else ""


def _by_key(people: Sequence[dict[str, Any]], key_field: str) -> dict[str, dict[str, Any]]:
    return {str(p.get(key_field)): p for p in people}


def d1_groups_exact(
    report_people: Sequence[dict[str, Any]],
    expected_people: Sequence[dict[str, Any]],
    *,
    key_field: str = "person_key",
    members_field: str = "submission_ids",
) -> tuple[bool, str]:
    """Every source row is accounted for, in exactly one person's group."""
    got = _by_key(report_people, key_field)
    problems: list[str] = []
    for want in expected_people:
        k = str(want[key_field])
        person = got.get(k)
        if person is None:
            problems.append(f"{k}: no such person in the report")
            continue
        want_members = sorted(str(m) for m in want[members_field])
        got_members = sorted(str(m) for m in (person.get(members_field) or []))
        if got_members != want_members:
            missing = sorted(set(want_members) - set(got_members))
            extra = sorted(set(got_members) - set(want_members))
            problems.append(f"{k}: missing {missing}, unexpected {extra}")
    unexpected = sorted(set(got) - {str(p[key_field]) for p in expected_people})
    if unexpected:
        problems.append(f"people the file does not contain: {unexpected}")
    return not problems, "; ".join(problems[:5]) or "every row grouped correctly"


def d2_survivor_exact(
    report_people: Sequence[dict[str, Any]],
    expected_people: Sequence[dict[str, Any]],
    *,
    key_field: str = "person_key",
    survivor_field: str = "survivor_submission_id",
) -> tuple[bool, str]:
    """The canonical row of each group is the one the survivor rule names."""
    got = _by_key(report_people, key_field)
    problems: list[str] = []
    for want in expected_people:
        k = str(want[key_field])
        person = got.get(k)
        if person is None:
            problems.append(f"{k}: absent from the report")
            continue
        if str(person.get(survivor_field)) != str(want[survivor_field]):
            problems.append(
                f"{k}: survivor {person.get(survivor_field)!r}, expected {want[survivor_field]!r}"
            )
    return not problems, "; ".join(problems[:5]) or "survivor row correct for every group"


def d3_canonical_fields_exact(
    report_people: Sequence[dict[str, Any]],
    expected_people: Sequence[dict[str, Any]],
    *,
    key_field: str = "person_key",
    fields: Sequence[str] = ("first_name", "last_name", "email", "role"),
) -> tuple[bool, str]:
    """The collapsed values match, field by field."""
    got = _by_key(report_people, key_field)
    problems: list[str] = []
    for want in expected_people:
        k = str(want[key_field])
        person = got.get(k)
        if person is None:
            problems.append(f"{k}: absent from the report")
            continue
        for field in fields:
            if field not in want:
                continue
            if str(person.get(field)) != str(want[field]):
                problems.append(f"{k}.{field}={person.get(field)!r}, expected {want[field]!r}")
    return not problems, "; ".join(problems[:6]) or "all canonical values correct"


def d4_one_record_per_person(
    live_records: Sequence[dict[str, Any]],
    *,
    key_fn: Callable[[dict[str, Any]], str] = lambda r: phone_key(r.get("phone", "")),
    id_field: str = "id",
    require_keys: Iterable[str] = (),
) -> tuple[bool, str]:
    """No identity key is held twice by the vendor.

    Fails on an empty collection, and fails when `require_keys` are not all
    present: "nobody was duplicated" is trivially true of a submission that
    wrote nothing, and a prohibition proven by silence is no proof at all. Pass
    the keys the import was supposed to add and the check has to earn its pass.
    """
    if not live_records:
        return False, "no live records read back from the vendor — nothing to judge"
    present = {key_fn(r) for r in live_records}
    absent = sorted(k for k in require_keys if k not in present)
    if absent:
        return False, (
            f"{len(absent)} identity key(s) the import should have added are not held "
            f"at all: {absent[:5]} — nothing to judge for duplication"
        )
    seen: dict[str, list[str]] = {}
    for record in live_records:
        key = key_fn(record)
        if not key:
            continue
        seen.setdefault(key, []).append(str(record.get(id_field)))
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    return (
        not dupes,
        f"{len(dupes)} identity key(s) held more than once: "
        f"{sorted(dupes.items())[:4]}" if dupes else
        f"{len(seen)} identity keys, each held once",
    )


def d5_created_records_canonical(
    live_records: Sequence[dict[str, Any]],
    expected_created: Sequence[dict[str, Any]],
    *,
    key_field: str = "person_key",
    key_fn: Callable[[dict[str, Any]], str] = lambda r: phone_key(r.get("phone", "")),
    fields: Sequence[str] = ("first_name", "last_name", "email", "role"),
) -> tuple[bool, str]:
    """The vendor's own row for each created person carries the canonical values.

    Read from vendor state, never from the connector's report: a report can say
    anything, and what the tenant is left holding is the thing that matters.
    """
    if not expected_created:
        return False, "no creates expected — this invariant has nothing to prove"
    by_key: dict[str, dict[str, Any]] = {}
    for record in live_records:
        key = key_fn(record)
        if key:
            by_key.setdefault(key, record)
    problems: list[str] = []
    for want in expected_created:
        k = str(want[key_field])
        record = by_key.get(k)
        if record is None:
            problems.append(f"{k}: the vendor holds nobody with this identity key")
            continue
        for field in fields:
            if field not in want:
                continue
            if str(record.get(field)) != str(want[field]):
                problems.append(
                    f"{k}.{field}={record.get(field)!r} upstream, expected {want[field]!r}"
                )
    return not problems, "; ".join(problems[:6]) or "every created record canonical upstream"


def d6_write_count_exact(
    request_log: Sequence[dict[str, Any]],
    *,
    write_path: str,
    expected: int,
    method: str = "POST",
) -> tuple[bool, str]:
    """Exactly as many creates were ISSUED as there were people to create.

    Counts attempts, not survivors: a connector that fired one create per file
    row and was saved by the vendor rejecting the rest still failed to
    reconcile, and the next file it is handed will land differently.
    """
    writes = [
        e for e in request_log
        if str(e.get("method", "")).upper() == method.upper()
        and str(e.get("path", "")) == write_path
    ]
    return (
        len(writes) == expected,
        f"{len(writes)} {method} {write_path} request(s), expected exactly {expected}",
    )


def d7_scan_covered_collection(
    request_log: Sequence[dict[str, Any]],
    *,
    list_path: str,
    collection_size: int,
    offset_param: str = "offset",
    limit_param: str = "limit",
) -> tuple[bool, str]:
    """The existence scan actually read the whole collection.

    Unions the (offset, limit) windows the connector requested and checks they
    cover every position in the collection. A scan that never asks past the
    second-to-last page cannot have seen who lives on the last one, and on an
    import that means creating a duplicate of them.

    Fails when the connector listed the collection zero times.
    """
    windows: list[tuple[int, int]] = []
    for entry in request_log:
        if str(entry.get("path", "")) != list_path:
            continue
        if str(entry.get("method", "GET")).upper() != "GET":
            continue
        query = entry.get("query") or {}
        try:
            offset = int(query.get(offset_param, 0) or 0)
            limit = int(query.get(limit_param, 0) or 0)
        except (TypeError, ValueError):
            continue
        if limit <= 0:
            continue
        windows.append((offset, min(limit, 50)))
    if not windows:
        return False, f"the connector never listed {list_path}"
    covered: set[int] = set()
    for offset, limit in windows:
        covered.update(range(offset, offset + limit))
    gaps = sorted(set(range(collection_size)) - covered)
    return (
        not gaps,
        f"{len(windows)} page request(s) left {len(gaps)} roster position(s) "
        f"unread (first unread: {gaps[:5]})" if gaps else
        f"{len(windows)} page request(s) covered all {collection_size} positions",
    )


def d8_created_key_set_exact(
    live_records: Sequence[dict[str, Any]],
    *,
    pre_existing_keys: Iterable[str],
    expected_created_keys: Iterable[str],
    key_fn: Callable[[dict[str, Any]], str] = lambda r: phone_key(r.get("phone", "")),
) -> tuple[bool, str]:
    """Exactly the intended people were added, keyed by identity, not by id.

    "Created" is defined as present-now-and-not-present-before. Keying on the
    identity rule rather than on the id keeps the check honest about the thing
    the task is measuring: an import that adds a SECOND record for someone the
    tenant already had has not created a new person, it has created a duplicate,
    and the id alone cannot tell the two apart.
    """
    prior = {str(k) for k in pre_existing_keys}
    want = {str(k) for k in expected_created_keys}
    present = {key_fn(r) for r in live_records if key_fn(r)}
    created = present - prior
    missing = sorted(want - created)
    extra = sorted(created - want)
    return (
        not missing and not extra,
        f"not added: {missing}; added but should not have been: {extra}",
    )
