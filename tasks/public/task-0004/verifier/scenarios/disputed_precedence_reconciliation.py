"""disputed_precedence_reconciliation -- rungs 2 and 5.

Rung 2 (L1, named ids): for the 15 unambiguous disputed candidates (see
_scenario_util.DISPUTED), the merged roster carries Placemint's stage, not
StaffLine's stale one. Scored PER APPLICATION ID, not as one aggregate: the
starter's "last write wins" tie-break happens to land on the right side for a
subset of the 15 (whichever rows Placemint touched more recently), and an
aggregate check hid that partial credit behind a single bit while the
connector's real precedence rule was still absent. One check per id makes the
per-row measurement visible and stops a single hard row from being worth the
same as fourteen.

Rung 5 (L3, traffic-conditional, per id): StaffLine's own request log shows
the connector issued a GET-by-id lookup against EACH contested candidate_id
-- proof it re-resolved every contested reference itself rather than trusting
the name-join alone. A run that produced no roster at all records these as
failed evidence rather than skipping them, per the "no output -> never
reaches builtin_l2" convention.

Conduct (builtin_l2) is scored once per verdict, in `correction_writeback` --
see merge_baseline.py's header for why.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _scenario_util import (  # noqa: E402
    DISPUTED,
    DISPUTED_CANDIDATE_IDS,
    STAFFLINE,
    candidate_get_by_id_calls,
    clear_outputs,
    read_roster,
    recreate_world,
)


async def run(ctx) -> None:
    recreate_world(ctx)
    clear_outputs(ctx)

    code, _out, err = ctx.app.run(["merge"])
    roster = read_roster(ctx, exit_ok=code == 0)
    # AND-ed with roster readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check(
        "merge_exit_ok",
        code == 0 and roster is not None,
        f"exit={code} stderr={err[:500]} roster_readable={roster is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    if roster is None:
        ctx.check(
            "roster_readable",
            False,
            "missing or unreadable output/roster.json",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        # Record the full per-id check set as failed evidence so the check
        # count stays identical whether or not the connector produced output.
        for d in DISPUTED:
            ctx.check(
                f"disputed_precedence:{d['application_id']}",
                False,
                "no roster.json to check",
                pass_value=2,
                fail_value=0,
                mandatory=True,
            )
        for candidate_id in DISPUTED_CANDIDATE_IDS:
            ctx.check(
                f"ref_integrity_probed:{candidate_id}",
                False,
                "no roster.json to check",
                pass_value=1,
                fail_value=0,
                mandatory=False,
            )
        return
    ctx.check(
        "roster_readable",
        True,
        f"rows={len(roster)}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    by_id = {row.get("source_id"): row for row in roster if isinstance(row, dict)}
    # THE TRAP (primary_mechanic: precedence_reconciliation_lag): once an
    # active Placemint placement exists for a candidate, Placemint's stage
    # must win unconditionally -- never a "most recently touched side wins"
    # tie-break. Scored per application id (see docstring) so a partially-
    # correct precedence rule (e.g. a lingering recency tie-break that
    # happens to agree on some rows) is measured, not hidden behind one bit.
    for d in DISPUTED:
        row = by_id.get(d["application_id"])
        if row is None:
            detail = "row missing from roster"
            ok = False
        else:
            ok = (
                row.get("source_of_truth") == "placemint"
                and row.get("stage") == d["target_stage"]
                and row.get("placemint_placement_id") == d["placemint_placement_id"]
            )
            detail = (
                f"source_of_truth={row.get('source_of_truth')} stage={row.get('stage')} "
                f"placement={row.get('placemint_placement_id')} "
                f"(want placemint/{d['target_stage']}/{d['placemint_placement_id']}, "
                f"staffline said {d['staffline_stage']})"
            )
        ctx.check(
            f"disputed_precedence:{d['application_id']}",
            ok,
            detail,
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

    # --------------------------------------------------------------- L3
    # Supporting conduct evidence for the same defect: did the connector
    # actually re-resolve each contested ref against StaffLine directly,
    # rather than trusting the Placemint name-join alone. Not itself the
    # correctness outcome (disputed_precedence:* above is), so it scores but
    # does not gate.
    sl_log = ctx.vendor(STAFFLINE).request_log()
    probed = {
        e.get("path", "").rsplit("/", 1)[-1] for e in candidate_get_by_id_calls(sl_log)
    }
    for candidate_id in DISPUTED_CANDIDATE_IDS:
        ctx.check(
            f"ref_integrity_probed:{candidate_id}",
            candidate_id in probed,
            f"GET /svc/candidates/{candidate_id} issued={candidate_id in probed} "
            f"(total GET-by-id lookups seen: {len(probed)})",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
