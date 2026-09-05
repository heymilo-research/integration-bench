"""Scenario 1 — initial full crawl of employees + assignments (checkpoint 0).

Flow:
  1. Recreate the vendor at checkpoint 0 (clean output dir — this is the very
     first scenario in the grade run) and run ``paygrade_sync sync``.
  2. Assert ``output/employees.json`` / ``output/assignments.json`` match the
     checkpoint-0 answer key exactly.

This exercises two of Paygrade's four typed doc lies at once:
  - undocumented_required_param: ``listEmployees`` silently requires
    ``company_id`` — omit it and every page comes back as a 200-status
    JSON-RPC error body. A connector that never discovers this either crashes
    on the very first page or (worse) treats the error body as an empty
    result and produces zero employees.
  - aspirational_endpoint: docs advertise ``bulkSync`` as a one-shot full-sync
    method; it is a permanent 501 stub. A connector must never call it.

It also proves offset pagination is driven correctly: Paygrade's envelope has
NO ``total`` field, so the only termination signal is the boolean ``more``
key — a connector using an ``echo_params``-adjacent heuristic (e.g. stopping
when a page comes back shorter than ``count``) happens to work here too since
Paygrade pads every page to `count` except the last, but the gold connector
(and this check) key off ``more`` as the vendor's docs specify.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    clear_output,
    diff_detail,
    load_fixture,
    read_output,
    row_diff,
)


async def run(ctx) -> None:
    clear_output(ctx)
    ctx.vendor("paygrade").recreate(checkpoint=0)

    code, _out, err = ctx.app.run(["sync"])

    employees = read_output(ctx, "employees.json")
    assignments = read_output(ctx, "assignments.json")
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. clear_output() above
    # guarantees these files can only come from THIS run.
    ctx.check(
        "initial_sync_exit_ok",
        code == 0 and employees is not None and assignments is not None,
        f"exit={code} outputs_readable={employees is not None and assignments is not None} "
        f"stderr={err[:400]}",
        # client.py/sync.py are fully unimplemented in the starter (raise
        # NotImplementedError) -- a do-nothing submission crashes here.
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "initial_sync_outputs_readable",
        employees is not None and assignments is not None,
        f"employees={'ok' if employees is not None else 'MISSING'} "
        f"assignments={'ok' if assignments is not None else 'MISSING'}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    if employees is None or assignments is None:
        return

    # The full tenant crawled: 120 employees / 160 assignments, none tombstoned yet.
    # Restores the deleted `initial_sync_{employees,assignments}_match_fixture`
    # compares, per row and per field. +2 mandatory: the whole store landing
    # exactly is what a completed first sync means, and the empty probe fails it.
    for entity, rows in (("employees", employees), ("assignments", assignments)):
        want = load_fixture(ctx, f"{entity}_checkpoint_0.json")
        diffs = row_diff(rows, want)
        ctx.check(
            f"initial_sync_{entity}_rows_exact",
            not diffs,
            diff_detail(f"{entity}@cp0", rows, want, diffs),
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

    ctx.check(
        "initial_sync_row_counts",
        len(employees) == 120 and len(assignments) == 160,
        f"employees={len(employees)} assignments={len(assignments)} (expected 120/160)",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "initial_sync_no_premature_tombstones",
        not any(r.get("is_deleted") for r in employees + assignments),
        "a row is marked is_deleted before any tombstone sweep ran",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    request_log = ctx.vendor("paygrade").request_log()
    list_employee_calls = [e for e in request_log if (e.get("query") or {}).get("method") == "listEmployees"]
    list_assignment_calls = [
        e for e in request_log if (e.get("query") or {}).get("method") == "listAssignments"
    ]

    # undocumented_required_param (LIES #1): every listEmployees call must
    # carry a non-empty company_id, or every page 200s-with-error and the
    # store would be empty.
    missing_company_id = [
        e for e in list_employee_calls if not (e.get("query") or {}).get("company_id")
    ]
    ctx.check(
        "initial_sync_listEmployees_always_sends_company_id",
        len(list_employee_calls) > 0 and not missing_company_id,
        f"{len(missing_company_id)}/{len(list_employee_calls)} listEmployees call(s) missing company_id",
        # The trap (undocumented_required_param, LIES #1): a connector that
        # never discovers this either crashes on the first page or treats
        # the 200-status error body as an empty result.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Paged to exhaustion: 120/50 => 3 pages, 160/50 => 4 pages.
    ctx.check(
        "initial_sync_employees_paged_to_exhaustion",
        len(list_employee_calls) >= 3,
        f"only {len(list_employee_calls)} listEmployees call(s); expected >= 3 pages",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "initial_sync_assignments_paged_to_exhaustion",
        len(list_assignment_calls) >= 4,
        f"only {len(list_assignment_calls)} listAssignments call(s); expected >= 4 pages",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # aspirational_endpoint (LIES #3): bulkSync is a documented-but-501 stub;
    # a correct connector never calls it.
    bulk_sync_calls = [e for e in request_log if (e.get("query") or {}).get("method") == "bulkSync"]
    ctx.check(
        "initial_sync_never_calls_bulkSync",
        not bulk_sync_calls,
        f"{len(bulk_sync_calls)} bulkSync call(s) made against a documented-but-501 stub endpoint",
        # The trap (aspirational_endpoint, LIES #3): docs advertise bulkSync
        # as a one-shot full-sync method; it is a permanent 501 stub.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)
