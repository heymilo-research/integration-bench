# VENDORED COPY -- do not edit here.
#
# Canonical source: tools/rework/pagination_family.py
# Refresh with:     python3 tools/rework/sync_family_lib.py task-0002
#
# The grading workspace copies only the task directory, so a scenario cannot
# import from tools/. Edit the canonical file and re-run the sync instead of
# patching this copy -- drift between the two is a suite-lint failure.

"""Reusable invariants for the pagination-integrity mechanic family.

A paginated backfill can be wrong in three ways that all look like success: it
can skip a page, re-read one, or stop early. None of those raise, and on a vendor
whose store is keyed by id a re-read collapses silently — so the only reliable
evidence is the set of page numbers the connector actually requested, compared
against the range the collection actually has.

  P1 origin respected      no request below the collection's first valid page
  P2 range covered         every page in 1..N (or 0..N-1) was requested
  P3 no redundant re-reads  no page requested more times than necessary
  P4 no duplicates          the merged output holds each source_id once

P1 is the one that catches a docs-following loop on a vendor that CLAMPS an
out-of-range page instead of erroring: the clamped request returns real records,
so the connector sees a plausible page and never learns it asked for the wrong
one. The cost is at the other end — the loop runs out before the last page.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence


def requested_pages(
    requests: Sequence[dict[str, Any]],
    *,
    page_param: str = "page",
) -> list[int]:
    """The page numbers requested, in order, as sent on the wire.

    Values that are absent or non-numeric are skipped rather than coerced: a
    connector that omits the param entirely is asking for the vendor's default,
    which is a different (and separately checkable) thing from asking for page 0.
    """
    out: list[int] = []
    for entry in requests:
        query = entry.get("query")
        if not isinstance(query, dict) or page_param not in query:
            continue
        try:
            out.append(int(query[page_param]))
        except (TypeError, ValueError):
            continue
    return out


def p1_origin_respected(
    pages: Sequence[int],
    *,
    first_valid: int = 1,
) -> tuple[bool, str]:
    """No request below the collection's first valid page number.

    On a vendor that clamps rather than errors, such a request succeeds and
    returns the first page's records, so nothing downstream ever notices.
    """
    if not pages:
        return False, "no paged request recorded — no evidence to judge"
    bad = sorted({p for p in pages if p < first_valid})
    return (
        not bad,
        f"requested page(s) {bad} below the first valid page ({first_valid}); "
        "this vendor clamps rather than errors, so the response looks legitimate",
    )


def p2_range_covered(
    pages: Sequence[int],
    *,
    expected: Iterable[int],
) -> tuple[bool, str]:
    want = set(expected)
    if not pages:
        return False, "no paged request recorded — no evidence to judge"
    missing = sorted(want - set(pages))
    return (
        not missing,
        f"never requested page(s) {missing}; requested {sorted(set(pages))}",
    )


def p3_no_redundant_rereads(
    pages: Sequence[int],
    *,
    allowance: int = 1,
) -> tuple[bool, str]:
    """No page fetched more than `allowance` times.

    A retry after a transient failure is legitimate, so the default permits one
    fetch per page and callers raise the allowance for tasks that inject faults.
    """
    # A connector that requested no pages re-read none of them. That is an
    # absence of evidence, not compliance (brief §3), and it is how this check
    # used to pass on the harness stub in every pagination task.
    if not pages:
        return False, "no paged request recorded — no evidence of re-reads either way"
    counts: dict[int, int] = {}
    for page in pages:
        counts[page] = counts.get(page, 0) + 1
    over = {p: n for p, n in sorted(counts.items()) if n > allowance}
    return (
        not over,
        f"page(s) fetched more than {allowance}x: {over}",
    )


def p4_no_duplicates(records: Sequence[dict[str, Any]]) -> tuple[bool, str]:
    # An empty output has no duplicates. Brief §3: prove the connector produced
    # something before crediting it with not having duplicated anything.
    if not records:
        return False, "no records in the merged output — nothing to judge for duplication"
    ids = [r.get("source_id") or r.get("id") for r in records]
    dupes = sorted({i for i in ids if ids.count(i) > 1})[:8]
    return not dupes, f"duplicate source_id(s) in the merged output: {dupes}"


def expected_page_range(total: int, per_page: int, *, first_valid: int = 1) -> list[int]:
    """The page numbers a complete crawl must visit."""
    if total <= 0 or per_page <= 0:
        return []
    count = -(-total // per_page)
    return list(range(first_valid, first_valid + count))
