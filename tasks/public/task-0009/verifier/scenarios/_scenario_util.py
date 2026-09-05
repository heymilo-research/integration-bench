"""Shared helpers for the task-0009 (Bullpen legacy-auth sunset) scenarios.

Added 2026-08-07 with the per-test-scoring migration, to replace the eleven
``*_matches_fixture`` whole-document compares this task used to run with per-row,
per-field differences that name the offending record.

None of these functions calls ``ctx.check`` with a value passed in by the caller:
every scored value stays a literal at its call site in the scenario, which is what
lets ``tools/check_migration.py`` audit the whole tree statically.
"""

from __future__ import annotations

from typing import Any


def by_source_id(got: Any) -> dict[str, dict[str, Any]]:
    rows = got if isinstance(got, list) else []
    return {r.get("source_id"): r for r in rows if isinstance(r, dict)}


def row_diff(got: Any, want: list[dict[str, Any]]) -> list[str]:
    """Per-row, per-field differences, named.

    A whole-document compare votes once for everything: a connector that lost 50
    rows crossing the auth sunset and one that dropped a single phone number score
    the same zero, and the detail string can only say the files differ. This names
    the row, the field, and both values.
    """
    gi = by_source_id(got)
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


def row_count_ok(got: Any, want: list) -> tuple[bool, str]:
    rows = got if isinstance(got, list) else None
    n = len(rows) if rows is not None else None
    return n == len(want), (
        f"rows={n if n is not None else 'missing/unreadable'} want={len(want)}"
    )


def diff_detail(diffs: list[str]) -> str:
    return f"{len(diffs)} field difference(s): {diffs[:4] or 'none'}"
