"""Shared helpers for the task-0008 (StaffLine full-sync + RPC writeback) scenarios.

Added 2026-08-07 with the per-test-scoring migration. All three scenarios had an
identical copy of ``_assert_no_query_token``, and the two crawl scenarios need the
same per-row differ, so both live here now instead of being triplicated.
"""

from __future__ import annotations

from typing import Any


def assert_no_query_token(ctx) -> None:
    """Hard gate: no request may authenticate via a query-string token.

    StaffLine's docs describe a retired ``?token=...`` scheme (LIES.md #4,
    wrong_auth_route); the only working placement is the X-SL-Token header, and
    the vendor always records the leaking query string in its request log. A
    connector that ever puts a credential in the query string leaks it — a
    conduct violation independent of whether the request even succeeds.

    Evidence-gated like builtin_l2's credential gates: the compose healthcheck
    polls ``GET /`` unconditionally, so a run that never issued a single request
    of its own still leaves log entries. Record the gate only when the submission
    produced at least one data-plane request — a do-nothing run must not bank a
    gate on silence.

    Scored 0/-1, not +1: a prohibition is never an achievement. It is recorded in
    all three scenarios, so scoring it positive would also have paid a
    well-behaved but wrong connector three times over for traffic it merely did
    not send.
    """
    request_log = ctx.vendor("staffline").request_log()
    data_plane = [e for e in request_log if e.get("path") not in ("/", "")]
    if not data_plane:
        return
    leaks = [
        e
        for e in request_log
        if "token" in (e.get("query") or {})
        or "token=" in (e.get("query_string") or "")
    ]
    ctx.check(
        "no_query_string_token",
        len(leaks) == 0,
        f"{len(leaks)} request(s) carried a token query param",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )


def row_diff(got: Any, want: list[dict[str, Any]]) -> list[str]:
    """Per-row, per-field differences, named.

    Replaces the deleted ``*_matches_fixture`` whole-document compares. Those
    voted once for everything: a connector that lost 30 rows to a pagination bug
    and one that dropped a single field on a single row scored the same zero, and
    the detail string could only say the files differed. This names the row, the
    field, and both values.
    """
    rows = got if isinstance(got, list) else []
    gi = {r.get("source_id"): r for r in rows if isinstance(r, dict)}
    wi = {r.get("source_id"): r for r in want if isinstance(r, dict)}
    out: list[str] = []
    for sid, wrow in wi.items():
        grow = gi.get(sid)
        if grow is None:
            out.append(f"{sid}: missing")
            continue
        if bool(grow.get("is_deleted")) != bool(wrow.get("is_deleted")):
            out.append(
                f"{sid}.is_deleted: got={grow.get('is_deleted')!r} "
                f"want={wrow.get('is_deleted')!r}"
            )
        gdata, wdata = grow.get("data") or {}, wrow.get("data") or {}
        for field, wval in wdata.items():
            if gdata.get(field) != wval:
                out.append(f"{sid}.{field}: got={gdata.get(field)!r} want={wval!r}")
    for sid in sorted(gi.keys() - wi.keys(), key=str):
        out.append(f"{sid}: not in the answer key")
    return out


def grade_fields(ctx, prefix: str, entity: str, got: Any, want: list[dict[str, Any]]) -> None:
    """Per-field equality for one entity, named by entity.

    Split from the row-count check so a connector with the right row count and one
    wrong field is distinguishable from one that lost half the rows.

    Deliberately does NOT take pass_value/mandatory as parameters. Every scored
    value in this suite is a literal at the call site: that is what makes
    `tools/check_migration.py` able to audit the whole 50-task tree statically, and
    a helper that accepts them turns each of its call sites into a value the
    validator can only report as `None`. Row counts differ per scenario, so those
    stay in the scenarios where their values are visible.
    """
    if not isinstance(got, list):
        return
    diffs = row_diff(got, want)
    ctx.check(
        f"{prefix}fields_exact:{entity}",
        not diffs,
        f"{len(diffs)} field difference(s): {diffs[:4] or 'none'}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )


def row_count_detail(entity: str, got: Any, want: list, note: str = "") -> tuple[bool, str]:
    """(ok, detail) for a row-count check. No ctx.check here — see grade_fields."""
    rows = got if isinstance(got, list) else None
    n = len(rows) if rows is not None else None
    detail = f"{entity}: got={n if n is not None else 'missing/unreadable'} want={len(want)}"
    if note and not n:
        detail += f" ({note})"
    return n == len(want), detail


def by_source_id(got: Any) -> dict[str, dict[str, Any]]:
    rows = got if isinstance(got, list) else []
    return {r.get("source_id"): r for r in rows if isinstance(r, dict)}
