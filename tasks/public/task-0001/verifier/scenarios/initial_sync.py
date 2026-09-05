"""Rung 1: a full back-fill of all four entity kinds, including sending
include_stage=1 on every applications.list call. Reachable by any connector that
gets signing and pagination right at all.

Grading note (2026-08-07): this scenario used to grade a single
`initial_backfill_complete` check comparing all four output files against
fixtures byte-for-byte. That votes once for everything — it cannot say whether a
connector lost 30 rows to a pagination bug or dropped `stage` because it forgot
`include_stage=1`, and it hands the same zero to both. Replaced with per-entity
row counts plus the request-log evidence for the one behaviour this rung actually
exists to check.
"""

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output

ENTITY_PLURALS = {
    "candidate": "candidates",
    "job": "jobs",
    "application": "applications",
    "note": "notes",
}

# Row counts of the untouched seed, from verifier/fixtures/*_checkpoint_0.json.
SEEDED_ROWS = {
    "candidates": 150,
    "jobs": 25,
    "applications": 180,
    "notes": 80,
}


def _applications_list_calls(request_log):
    return [
        e
        for e in request_log
        if e.get("method") == "GET" and "/applications" in str(e.get("path", ""))
    ]


def _carries_include_stage(entry) -> bool:
    """Whether the request supplied the vendor's required parameter.

    StaffLine treats this as a presence flag: ``1``, ``true`` and other
    non-empty values all enable the stage projection.  The output check below
    independently proves that stage data actually came back, so the wire check
    must not impose a verifier-only spelling for the same accepted behavior.
    """
    query = entry.get("query") or {}
    return isinstance(query, dict) and "include_stage" in query


async def run(ctx) -> None:
    exit_code, stdout, stderr = ctx.app.run()

    outputs: dict[str, object] = {}
    for plural in ENTITY_PLURALS.values():
        output_path = ctx.output_dir / f"{plural}.json"
        outputs[plural] = read_json_output(
            output_path, timeout_s=15.0 if exit_code == 0 else 0.5
        )
    outputs_readable = sum(1 for v in outputs.values() if v is not None)

    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    #
    # Preserve-style: the given starter already runs and writes readable output,
    # so passing earns nothing and only a regression costs.
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and outputs_readable > 0,
        f"exit={exit_code} stderr={stderr[:500]} outputs_readable={outputs_readable}/4",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # Per entity, so a partial crawl names the entity it truncated instead of
    # collapsing into one opaque "does not match fixture".
    for plural, want in SEEDED_ROWS.items():
        rows = outputs.get(plural)
        got = len(rows) if isinstance(rows, list) else None
        ctx.check(
            f"backfill_row_count:{plural}",
            got == want,
            f"{plural}: got={got if got is not None else 'missing/unreadable'} want={want}",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    # The behaviour this rung is named for. `stage` only comes back when the
    # connector sends include_stage=1, so a connector that omits it produces
    # 180 applications with no stage — the right row count and the wrong data,
    # which a row-count check alone cannot see.
    applications = outputs.get("applications") or []
    staged = [
        r
        for r in applications
        if isinstance(r, dict) and (r.get("data") or {}).get("stage") is not None
    ]
    ctx.check(
        "applications_carry_stage",
        len(applications) > 0 and len(staged) == len(applications),
        f"{len(staged)}/{len(applications)} applications carry a stage "
        "(absent unless include_stage=1 is sent on every applications.list call)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Evidence for the same behaviour at the wire, not just in the output: every
    # applications.list call must carry the parameter, not merely the first.
    log = ctx.vendor("staffline").request_log()
    calls = _applications_list_calls(log)
    # `query` is a DICT in the request log (see incremental_watermark.py), not a
    # raw query string — a substring match against str() would appear to work
    # and silently never fire.
    with_flag = [e for e in calls if _carries_include_stage(e)]
    ctx.check(
        "include_stage_on_every_applications_call",
        len(calls) > 0 and len(with_flag) == len(calls),
        f"{len(with_flag)}/{len(calls)} applications.list calls supplied include_stage",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)
