# VENDORED COPY -- do not edit here.
#
# Canonical source: tools/rework/subresource_family.py
# Refresh with:     python3 tools/rework/sync_family_lib.py task-0002
#
# The grading workspace copies only the task directory, so a scenario cannot
# import from tools/. Edit the canonical file and re-run the sync instead of
# patching this copy -- drift between the two is a suite-lint failure.

"""Reusable invariants for the subresource-completeness mechanic family.

A parent collection whose records each hang a paginated sub-collection can be
swept wrongly in five ways that all look like a clean run:

  S1 every parent visited        including the ones whose sub-collection is
                                 EMPTY -- a connector that builds its output by
                                 flattening the child stream never asks about a
                                 childless parent and never notices it is gone
  S2 no parent outside scope     the cheap way to be "complete" is to sweep the
                                 whole tenant; that is a different (and, for a
                                 scoped export, wrong) job
  S3 tail pages fetched          a sub-collection that fits "obviously" inside
                                 one page usually does -- until one parent's
                                 does not, and then exactly the overflow is lost
  S4 no page refetched           re-walking a sub-collection from the first page
                                 collapses silently on an id-keyed store
  S5 child sets exact            the payload check S1/S3 are the wire evidence
                                 for: per parent, the child id set, not a count

S1 and S3 are the pair that matters. S5 alone cannot tell "the connector asked
and the parent really has no children" from "the connector never asked", and S3
alone passes on a tenant where no parent happens to overflow a page -- so the
checks are kept separate and each is asserted against evidence of its own kind.

EMPTY EVIDENCE IS NOT COMPLIANCE. Every function here returns False when there
is nothing to judge: a submission that made no requests must not bank a
prohibition it was never in a position to violate.

Cursor decoding: this family's vendors hand out an opaque base64 cursor whose
payload carries the offset it resumes at. `decode_offset` reads it so a
verifier can talk about "the second page of cand_0071's notes" instead of about
an opaque string; a cursor it cannot decode comes back as None and is reported
rather than silently treated as offset 0.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Iterable, Mapping, Sequence


def decode_offset(cursor: str | None) -> int | None:
    """An opaque `base64(json({"offset": N}))` cursor -> N.

    None for an absent cursor is deliberate at the call site's discretion:
    callers that treat "no cursor param" as the first page pass 0 themselves,
    because an *undecodable* cursor and an *absent* one are different facts.
    """
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return int(payload["offset"])
    except Exception:  # noqa: BLE001 - any malformed cursor is simply undecodable
        return None


def requested_offsets(
    entries: Sequence[Mapping[str, Any]], *, cursor_param: str = "cursor"
) -> list[int | None]:
    """The page offsets requested, in log order.

    A request carrying no cursor is the first page, i.e. offset 0. A request
    carrying a cursor that will not decode yields None, so a caller can see it
    rather than mistake it for the first page.
    """
    out: list[int | None] = []
    for entry in entries:
        query = entry.get("query") or {}
        if cursor_param not in query:
            out.append(0)
            continue
        out.append(decode_offset(str(query[cursor_param])))
    return out


def subresource_requests(
    request_log: Sequence[Mapping[str, Any]],
    *,
    collection_path: str,
    subresource: str,
) -> dict[str, list[Mapping[str, Any]]]:
    """GETs of `<collection_path>/<parent_id>/<subresource>`, grouped by parent.

    Matched structurally on the path, so a vendor base path or an id shape this
    module knows nothing about is fine.
    """
    prefix = collection_path.rstrip("/") + "/"
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for entry in request_log:
        if (entry.get("method") or "GET").upper() != "GET":
            continue
        path = str(entry.get("path") or "")
        if not path.startswith(prefix) or not path.endswith("/" + subresource):
            continue
        parent = path[len(prefix) : -len("/" + subresource)]
        if not parent or "/" in parent:
            continue
        grouped.setdefault(parent, []).append(entry)
    return grouped


def detail_requests(
    request_log: Sequence[Mapping[str, Any]],
    *,
    collection_path: str,
) -> dict[str, list[Mapping[str, Any]]]:
    """GETs of `<collection_path>/<record_id>`, grouped by record id.

    The by-id read of a record in the parent collection — NOT a sub-collection
    read (`.../{id}/notes`), which carries a further path segment and is
    excluded here, and not a read of the collection itself.

    This is the evidence layer for a vendor whose LIST endpoint serves a
    projection: whether the connector ever went and got the rest of a record is
    a fact about the wire, and the payload alone cannot distinguish "hydrated
    and the value really is empty" from "never asked".
    """
    prefix = collection_path.rstrip("/") + "/"
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for entry in request_log:
        if (entry.get("method") or "GET").upper() != "GET":
            continue
        path = str(entry.get("path") or "")
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):].strip("/")
        if not rest or "/" in rest:
            continue
        grouped.setdefault(rest, []).append(entry)
    return grouped


def collection_requests(
    request_log: Sequence[Mapping[str, Any]], *, collection_path: str
) -> list[Mapping[str, Any]]:
    """GETs of the parent collection itself (never a by-id or sub-collection read)."""
    want = collection_path.rstrip("/")
    return [
        e
        for e in request_log
        if (e.get("method") or "GET").upper() == "GET"
        and str(e.get("path") or "").rstrip("/") == want
    ]


# ---------------------------------------------------------------------------
# S1..S5
# ---------------------------------------------------------------------------

def s1_all_parents_visited(
    visited: Mapping[str, Sequence[Any]], expected: Iterable[str]
) -> tuple[bool, str]:
    """Every in-scope parent's sub-collection was actually requested."""
    want = sorted(set(expected))
    if not want:
        return False, "no in-scope parent expected — nothing to judge"
    if not visited:
        return False, (
            f"no sub-collection request recorded at all; {len(want)} parent(s) "
            "were in scope"
        )
    missing = sorted(set(want) - set(visited))
    return (
        not missing,
        f"never asked for the sub-collection of {len(missing)} parent(s): "
        f"{missing[:8]}{' ...' if len(missing) > 8 else ''}",
    )


def s2_no_unscoped_parents_visited(
    visited: Mapping[str, Sequence[Any]], expected: Iterable[str]
) -> tuple[bool, str]:
    """No sub-collection read for a parent outside the declared scope."""
    if not visited:
        return False, "no sub-collection request recorded — no evidence to judge"
    extra = sorted(set(visited) - set(expected))
    return (
        not extra,
        f"swept the sub-collection of {len(extra)} out-of-scope parent(s): "
        f"{extra[:8]}{' ...' if len(extra) > 8 else ''}",
    )


def s3_tail_pages_fetched(
    visited: Mapping[str, Sequence[Mapping[str, Any]]],
    expected_offsets: Mapping[str, Sequence[int]],
    *,
    cursor_param: str = "cursor",
) -> tuple[bool, str]:
    """Every page of every multi-page sub-collection was requested.

    `expected_offsets` is the measured truth: parent -> the offsets a complete
    walk must cover. Parents whose sub-collection fits in one page carry `[0]`
    and are therefore also asserted, which is what keeps this from passing
    vacuously on a tenant that happens to have no overflow.
    """
    multipage = {p: offs for p, offs in expected_offsets.items() if len(offs) > 1}
    if not multipage:
        return False, (
            "no parent has a multi-page sub-collection in this fixture — the "
            "check has nothing to prove and must not pass"
        )
    if not visited:
        return False, "no sub-collection request recorded — no evidence to judge"
    short: list[str] = []
    for parent, offsets in sorted(multipage.items()):
        got = {o for o in requested_offsets(visited.get(parent, []), cursor_param=cursor_param)}
        missing = sorted(set(offsets) - got)
        if missing:
            short.append(f"{parent} missing offset(s) {missing} (asked {sorted(o for o in got if o is not None)})")
    return (
        not short,
        f"{len(short)} parent(s) stopped short of their last sub-collection page: "
        + "; ".join(short[:5]),
    )


def s4_no_subresource_page_refetched(
    visited: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    allowance: int = 1,
    cursor_param: str = "cursor",
) -> tuple[bool, str]:
    """No (parent, page) fetched more than `allowance` times."""
    if not visited:
        return False, "no sub-collection request recorded — no evidence to judge"
    over: list[str] = []
    for parent, entries in sorted(visited.items()):
        counts: dict[Any, int] = {}
        for offset in requested_offsets(entries, cursor_param=cursor_param):
            counts[offset] = counts.get(offset, 0) + 1
        hot = {o: n for o, n in counts.items() if n > allowance}
        if hot:
            over.append(f"{parent}: {hot}")
    return (
        not over,
        f"{len(over)} parent(s) had a sub-collection page fetched more than "
        f"{allowance}x: " + "; ".join(over[:5]),
    )


def s6_all_records_hydrated(
    hydrated: Mapping[str, Sequence[Any]], expected: Iterable[str]
) -> tuple[bool, str]:
    """Every in-scope record was read through its own endpoint.

    For a collection whose LIST response is a projection, the by-id read is the
    only place the rest of the record exists. Asserted on the wire and not only
    on the payload, because a row carrying an empty value and a row that was
    never hydrated are indistinguishable in the output file.
    """
    want = sorted(set(expected))
    if not want:
        return False, "no in-scope record expected — nothing to judge"
    if not hydrated:
        return False, (
            f"no by-id read recorded at all; {len(want)} record(s) were in scope "
            "and the list view does not carry the whole record"
        )
    missing = sorted(set(want) - set(hydrated))
    return (
        not missing,
        f"never read {len(missing)} in-scope record(s) through their own "
        f"endpoint: {missing[:8]}{' ...' if len(missing) > 8 else ''}",
    )


def s7_no_hydration_outside_scope(
    hydrated: Mapping[str, Sequence[Any]], expected: Iterable[str]
) -> tuple[bool, str]:
    """No by-id read of a record outside the declared scope.

    Hydrating the whole tenant is the brute-force way to be complete; it is a
    different job, it costs the tenant's rate budget, and on a scoped export it
    is wrong.
    """
    if not hydrated:
        return False, "no by-id read recorded — no evidence to judge"
    extra = sorted(set(hydrated) - set(expected))
    return (
        not extra,
        f"read {len(extra)} out-of-scope record(s) by id: "
        f"{extra[:8]}{' ...' if len(extra) > 8 else ''}",
    )


def s8_no_record_rehydrated(
    hydrated: Mapping[str, Sequence[Any]], *, allowance: int = 1
) -> tuple[bool, str]:
    """No record read through its own endpoint more than `allowance` times."""
    if not hydrated:
        return False, "no by-id read recorded — no evidence to judge"
    over = {rid: len(v) for rid, v in sorted(hydrated.items()) if len(v) > allowance}
    return (
        not over,
        f"{len(over)} record(s) read by id more than {allowance}x: "
        + ", ".join(f"{k}x{v}" for k, v in list(over.items())[:8]),
    )


def s5_child_ids_exact(
    actual: Mapping[str, Iterable[str]], expected: Mapping[str, Iterable[str]]
) -> tuple[bool, str]:
    """Per parent, the exported child id set equals the measured truth."""
    if not expected:
        return False, "no expected child sets — nothing to judge"
    if not actual:
        return False, "the export holds no parent at all — no evidence to judge"
    problems: list[str] = []
    for parent, want_ids in sorted(expected.items()):
        want = set(want_ids)
        if parent not in actual:
            problems.append(f"{parent}: absent from the export (expected {len(want)} child rows)")
            continue
        got = set(actual[parent])
        if got != want:
            problems.append(
                f"{parent}: missing {sorted(want - got)} extra {sorted(got - want)}"
            )
    return (
        not problems,
        f"{len(problems)} parent(s) with a wrong child set: " + "; ".join(problems[:5]),
    )
