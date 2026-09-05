# VENDORED COPY -- do not edit here.
#
# Canonical source: tools/rework/import_family.py
# Refresh with:     python3 tools/rework/sync_family_lib.py task-0029
#
# The grading workspace copies only the task directory, so a scenario cannot
# import from tools/. Edit the canonical file and re-run the sync instead of
# patching this copy -- drift between the two is a suite-lint failure.

"""Reusable invariants for the create-only import mechanic family.

A create-only import has two halves, and the second is the one that does damage.
Creating the right records is straightforward; NOT touching the ones that already
exist is where an "upsert, it's simpler" implementation quietly overwrites
upstream state with whatever a spreadsheet happened to say.

  I1 created set exact      exactly the intended records were created
  I2 pre-existing untouched every matched record still holds its upstream values
  I3 no write attempted     no write request was even issued against a match
  I4 final cardinality      the collection ends at the expected size
  I5 values normalised      locale-specific inputs were converted correctly
  I6 idempotent             a second run over the same input creates nothing

I2 and I3 are deliberately both present and are not redundant. I2 reads the
resulting state, so it catches a write that landed. I3 reads the request log, so
it catches a write that was *attempted* and happened to be a no-op or to fail —
which is still the connector deciding it was entitled to mutate a record it was
told to leave alone.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence


def i1_created_set_exact(
    final_records: Sequence[dict[str, Any]],
    *,
    pre_existing_emails: Iterable[str],
    expected_created_emails: Iterable[str],
    email_field: str = "email",
) -> tuple[bool, str]:
    prior = {str(e).strip().lower() for e in pre_existing_emails}
    want = {str(e).strip().lower() for e in expected_created_emails}
    present = {str(r.get(email_field, "")).strip().lower() for r in final_records}
    created = present - prior
    missing = sorted(want - created)
    extra = sorted(created - want)
    return (
        not missing and not extra,
        f"not created: {missing}; created but should not have been: {extra}",
    )


def i2_preexisting_untouched(
    final_records: Sequence[dict[str, Any]],
    untouched: Sequence[dict[str, Any]],
    *,
    import_observed: bool,
    fields: Sequence[str] = ("stage", "updated_ts"),
    id_field: str = "id",
) -> tuple[bool, str]:
    """Pre-existing records came through the import unchanged.

    `import_observed` is not optional and has no default: this check reads the
    vendor's collection, and a connector that did nothing at all leaves that
    collection exactly as it found it, so it banks "unchanged" for free. The
    claim being graded is "you imported without collateral damage", and the
    first half of it has to be witnessed before the second half means anything.
    Callers pass the evidence that an import actually landed.
    """
    if not import_observed:
        return False, (
            "no import is visible in the collection — leaving the pre-existing "
            "records alone is not an achievement when nothing was written at all"
        )
    by_id = {str(r.get(id_field)): r for r in final_records}
    problems: list[str] = []
    for spec in untouched:
        rec = by_id.get(str(spec["id"]))
        if rec is None:
            problems.append(f"{spec['id']} ({spec.get('email')}) vanished from the collection")
            continue
        for field in fields:
            if field in spec and spec[field] is not None:
                got = rec.get(field)
                if str(got) != str(spec[field]):
                    problems.append(
                        f"{spec['id']} {field}: {got!r} now, was {spec[field]!r} upstream"
                    )
    return not problems, "; ".join(problems[:5]) or "all matched records unchanged"


def i3_no_write_attempted(
    request_log: Sequence[dict[str, Any]],
    *,
    protected_ids: Iterable[str],
    write_methods: Sequence[str] = ("PATCH", "PUT", "POST", "DELETE"),
) -> tuple[bool, str]:
    """No write request was issued against a record that already existed.

    Catches the attempt, not just the effect: a PATCH that 422'd or wrote the same
    value is still the connector claiming a mandate it was not given.
    """
    protected = {str(i) for i in protected_ids}
    writes = [e for e in request_log if (e.get("method") or "GET").upper() in write_methods]
    if not writes:
        # A connector that never issued a write trivially never aimed one at a
        # protected record. Restraint is only observable in something that acted.
        return False, (
            "the connector issued no write requests at all — there is no write "
            "behaviour here to judge as respectful of pre-existing records"
        )
    offenders: list[str] = []
    for entry in request_log:
        method = (entry.get("method") or "GET").upper()
        if method not in write_methods:
            continue
        path = str(entry.get("path") or "")
        for pid in protected:
            if pid and pid in path:
                offenders.append(f"{method} {path}")
                break
    return (
        not offenders,
        f"{len(offenders)} write request(s) against pre-existing record(s): {offenders[:4]}",
    )


def i4_final_cardinality(final_records: Sequence[dict[str, Any]], expected: int) -> tuple[bool, str]:
    return (
        len(final_records) == expected,
        f"collection holds {len(final_records)} record(s), expected {expected}",
    )


def i5_values_normalised(
    report_rows: Sequence[dict[str, Any]],
    expected_rows: Sequence[dict[str, Any]],
    *,
    key_field: str = "ref",
    numeric_fields: Sequence[str] = ("applied_on_epoch_s", "expected_rate"),
) -> tuple[bool, str]:
    """Locale-specific inputs converted to the wire's units and real numbers.

    Compares numerically rather than by string so formatting is not graded, and
    reports the first few mismatches with both values.
    """
    got_by_key = {str(r.get(key_field)): r for r in report_rows}
    problems: list[str] = []
    for expected in expected_rows:
        k = str(expected[key_field])
        row = got_by_key.get(k)
        if row is None:
            problems.append(f"{k}: absent from the report")
            continue
        for field in numeric_fields:
            want = expected[field]
            raw = row.get(field)
            try:
                got = float(raw)
            except (TypeError, ValueError):
                problems.append(f"{k}.{field}={raw!r} is not numeric (expected {want})")
                continue
            if abs(got - float(want)) > 0.001:
                problems.append(f"{k}.{field}={raw!r}, expected {want}")
    return not problems, "; ".join(problems[:5]) or "all values normalised"


def i6_idempotent(
    before: Sequence[dict[str, Any]],
    after: Sequence[dict[str, Any]],
    *,
    import_observed: bool,
    id_field: str = "id",
) -> tuple[bool, str]:
    """A second run over the same input adds and removes nothing.

    `import_observed` is required for the same reason as in
    `i2_preexisting_untouched`: comparing the collection to itself across two
    runs that both created nothing is comparing empty to empty, and it is the
    do-nothing connector that passes it most reliably of all.
    """
    if not import_observed:
        return False, (
            "no import is visible in the collection — a second run that repeats "
            "the first run's nothing is not evidence of idempotence"
        )
    a = {str(r.get(id_field)) for r in before}
    b = {str(r.get(id_field)) for r in after}
    added = sorted(b - a)[:6]
    removed = sorted(a - b)[:6]
    return (
        not added and not removed,
        f"a second run over the same input added {added} and removed {removed}",
    )
