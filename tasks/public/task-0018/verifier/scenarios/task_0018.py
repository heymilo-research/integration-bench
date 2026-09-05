"""task-0018 -- mart_rollup_api_cutover (RecruitOS, migrate).

One nightly run of Sandhurst Recruitment's requisition rollup, on the first
night after RecruitOS's Reporting Mart -- the nightly extract product the file
came out of since 2022 -- was switched off.

The connector in `repo/` is the WORKING mart-era pass, not a stub: it globs
`MART_DROP_DIR` for tonight's `rollup-*.csv`, renames two columns and hands the
result to Finance's loader. The mart derived every column itself, so the
mart-era pass never had to. With the mart gone the drop directory is empty and
`build_rollup` raises `NoDropTonight`, so the pass produces nothing until it is
ported onto the live REST API. That is the task.

SCOPE CHANGE, recorded here so it is not mistaken for drift. The `minimal-real`
scaffold declared `primary_mechanic: signature_algorithm_dual_verify_migration`,
`entry.command: [node, index.js]` with `track: typescript` over a repo holding
one Python `main.py`, `CHECKPOINT: '0'` (RecruitOS applies zero mutations
there), a phantom `FAULT_SIGNATURE_ALGORITHM_DUAL_VERIFY_MIGRATION` knob no
RecruitOS source file reads, and the generated 3-check whole-file-vs-fixture
template.

  * The declared mechanic is NOT EXPRESSIBLE on this vendor. RecruitOS signs
    with exactly one algorithm and one secret (`webhooks._sign`, hmac-sha256
    over `timestamp + "." + raw_body`; `vendor.yaml` `webhooks.signing`), there
    is no second algorithm, no key id, no `X-ROS-Signature-Version`, and no
    knob anywhere in `src/recruitos/` that could introduce one. A dual-verify
    migration could only be authored by CHANGING the vendor -- and RecruitOS is
    the zero-lie calibration control, so a vendor change here is the most
    expensive edit in the suite. AUTHORING-BRIEF S7: "if your task cannot be
    expressed on its assigned vendor's current capability, stop and report
    that rather than reshaping the vendor to fit."
  * `primary_mechanic` was therefore RENAMED to a migration this vendor can
    actually pose. `category: migrate` is UNCHANGED, per AUTHORING-CHECKLIST
    "do not change a task's category", and the shape matches it: `repo/` ships
    a working connector against a retired source that has to be carried onto a
    live one.
  * The task is also read-only against RecruitOS, deliberately. Every write
    handler on this vendor (`main.py:341,349,357`) returns
    `403 {"error":"read_only_tenant"}` unconditionally while `docs/writeback.md`
    documents a 201/200/422 contract -- an OPEN vendor defect. Grading anything
    on that 403 would be grading an undocumented divergence on the control
    vendor, so nothing here writes.

DEVICES
-------
`docs/` is RecruitOS's own documentation, byte-identical to the vendor bundle
(`check_honest_vendor_docs.py --enforce` must keep reporting exactly ONE
contaminated task, and it is task-0051, not this one), plus ONE task-local
document: `docs/sandhurst-mart-handover.md`, Data Services' own handover note,
disclaimed in its second line as "our own internal note, not RecruitOS
documentation ... last revised in November". The vendor is honest. The note is
where this tenant's beliefs live, and three of them are false. Every number
below is measured by `tools/rework/gen_answer_key_0161.py` against the world
this task boots (CHECKPOINT=60), not asserted.

  D1. **THE DEFENSIVE BRANCH THAT FIRES 127 TIMES.** The note: *"`frozen` never
      fired. It is a defensive branch and always was. In RecruitOS an
      application sitting against a requisition that is no longer open has
      already been resolved ... The mart derived the disposition from the
      application's own stage and nothing else, and we never once had to look
      at the requisition ledger."* MEASURED at this checkpoint: 230 of the 300
      applications sit against a requisition that is not `open`, and 127 of
      them are the tenant's largest disposition class. Only 15 of 46
      requisitions are open. Nothing in RecruitOS's documentation settles this
      either way -- it is a claim about the tenant's DATA, and the only thing
      that answers it is one GET of the requisition ledger.

  D2. **THE RETIRED RECORDS ARRIVE LOOKING ORDINARY, AND ARE INSIDE `total`.**
      The note: *"`dropped` never fired either ... RecruitOS's list endpoints
      only ever hand you live records ... and the envelope's `total` is the
      count of live records. If you find yourself writing a reconciliation pass
      to work out what has been retired, stop."* This one is contradicted by
      the vendor's OWN docs -- `docs/index.md` and `docs/entities.md` both say
      soft-deleted records stay in list responses carrying `is_deleted: true`
      -- so it is the note-over-vendor-docs burn. MEASURED: the envelopes
      report 259/46/300, the live counts are 253/43/300, and 24 applications
      hang off a retired candidate or a retired requisition.

  D3. **THERE IS NO CASCADE.** The note: *"RecruitOS cascades its timestamps.
      Touching a requisition bumps `updated_at` on every application attached
      to it, and the same is true of a candidate ... There is nothing to
      reconcile here and no `max()` to compute."* MEASURED: 86 of the 300
      applications have a `last_change_at` strictly greater than their own
      `updated_at`, because their requisition or their candidate moved and
      they did not. A pass that takes the note at its word backdates 86 lines
      and the on-call dashboard sees a quiet night.

  C1 (competence, not a divergence). The join is total and offset-paginated:
      259 candidates and 300 applications are six pages each at the documented
      maximum `limit` of 50, 46 requisitions are one. `total` is the terminal
      condition and it counts the retired rows, so a walk that stops early
      loses the tail of the candidate ledger and cannot resolve part of the
      join at all.

MEASURED, and why the mass moves
--------------------------------
A pass written faithfully from `docs/` AND the handover note (`naive.patch`)
gets 151 of 300 dispositions and 86 of 300 `last_change_at` values wrong --
50% and 29% of the ledger. On the 60 sampled witnesses that is 34 and 21.
`gold / starter / stub / naive` and the wrong-answer basins are recorded in
this task's WORKLOG entry.

The per-record witnesses are `sorted(application_ids)[::5]` -- every fifth
application in ascending id order, 60 of 300, chosen by a deterministic rule
rather than by disposition, so the check mass follows the tenant's own mix.
The sample's measured composition is written into the answer key
(`witness_composition`) so it can be checked for stratification toward the
trapped rows: frozen 31, lost 13, placed 7, working 6, dropped 3.

Evidence: every check reads the connector's declared artifacts against
`verifier/fixtures/answer_key.json` or the vendor's request log. There is no
whole-file fixture comparison anywhere, and this verifier issues no request to
RecruitOS at any point, so every log entry it reads is the connector's own.
The connector is run in two fresh processes against the same tenant. The
second lifetime must reproduce the complete artifacts; conduct checks account
for both runs.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "recruitos"
CSV_COLUMNS = (
    "application_id",
    "candidate_id",
    "requisition_id",
    "stage",
    "disposition",
    "last_change_at",
)
DISPOSITIONS = ("dropped", "placed", "lost", "frozen", "working")


def _load_json(ctx, name: str):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _load_csv(ctx, name: str) -> list[dict[str, str]] | None:
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return list(csv.DictReader(io.StringIO(text)))
    except csv.Error:
        return None


def _cell(row: dict[str, Any] | None, column: str) -> str:
    if not isinstance(row, dict):
        return ""
    value = row.get(column)
    return "" if value is None else str(value).strip()


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    want_rows: dict[str, dict[str, str]] = key["rows"]
    witnesses: list[str] = key["witnesses"]

    code, _out, err = ctx.app.run(["sync"])
    first_result = _load_json(ctx, "result.json")
    first_report = _load_csv(ctx, "rollup.csv")

    ctx.check_l1(
        "rollup_pass_completed",
        code == 0 and isinstance(first_result, dict) and first_report is not None,
        f"exit={code} result={type(first_result).__name__} "
        f"rollup.csv={'parsed' if first_report is not None else 'missing'} stderr={err[:400]}",
    )

    # A scheduler restart is a new process with only the declared artifacts as
    # durable state.  Re-running the same cutover view must converge rather
    # than depend on module globals or a lucky first traversal.
    rerun_code, _rerun_out, rerun_err = ctx.app.run(["sync"])
    result = _load_json(ctx, "result.json")
    report = _load_csv(ctx, "rollup.csv")
    ctx.check_l1(
        "rollup_rerun_converged_after_restart",
        rerun_code == 0
        and result == first_result
        and report == first_report,
        f"rerun_exit={rerun_code} result_stable={result == first_result} "
        f"csv_stable={report == first_report} stderr={rerun_err[:400]}",
    )

    # -- what the pass actually read -----------------------------------------
    # Requirement-shaped and independent of the artifacts: a pass that never
    # asked for a ledger cannot have joined against it, and inaction cannot
    # satisfy any of the three.
    log = ctx.vendor(VENDOR).request_log()
    listed = {
        entry.get("path")
        for entry in log
        if (entry.get("method") or "").upper() == "GET" and int(entry.get("status") or 0) == 200
    }
    for collection, path in sorted(key["list_paths"].items()):
        ctx.check_l1(
            f"rollup_read_the_{collection}_ledger",
            path in listed,
            f"the pass drew no 200 from {path}; tonight's file is a join over "
            f"{sorted(key['list_paths'].values())} and it read "
            f"{sorted(p for p in listed if str(p).startswith('/api/'))}",
        )

    # Conduct, over the connector's own traffic and nothing else -- this
    # verifier has issued no request to RecruitOS.
    await builtin_l2(ctx, app_runs=2)

    # Everything below is emitted UNCONDITIONALLY. A pass that wrote nothing
    # must fail each check rather than shrink the denominator.
    doc = result if isinstance(result, dict) else {}
    rows = doc.get("rows") if isinstance(doc.get("rows"), list) else []
    counts = doc.get("counts") if isinstance(doc.get("counts"), dict) else {}
    retired = doc.get("retired") if isinstance(doc.get("retired"), dict) else {}

    got: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("application_id") is not None:
            got[str(row["application_id"])] = row

    # -- the ledger the file has to cover ------------------------------------
    want_ids = set(key["row_ids"])
    have_ids = set(got)
    expected_rows = {
        aid: {"application_id": aid, **want_rows[aid]} for aid in key["row_ids"]
    }
    exact_result_rows = (
        len(rows) == len(want_ids)
        and got == expected_rows
        and all(set(row) == set(CSV_COLUMNS) for row in rows if isinstance(row, dict))
    )
    ctx.check_l1(
        "rollup_covers_every_application",
        have_ids == want_ids and exact_result_rows,
        f"the file carries {len(have_ids)} application(s); the tenant holds "
        f"{len(want_ids)}. missing (sample): {sorted(want_ids - have_ids)[:5]}; "
        f"not in the tenant (sample): {sorted(have_ids - want_ids)[:5]}",
    )
    ctx.check_l1(
        "rollup_declares_its_row_count",
        set(doc) == {"source", "counts", "retired", "rows"}
        and doc.get("source") == "recruitos-api"
        and set(counts) == {"rows", *DISPOSITIONS}
        and all(type(counts.get(name)) is int for name in ("rows", *DISPOSITIONS))
        and set(retired) == {"candidates", "requisitions"}
        and all(type(retired.get(name)) is int for name in retired)
        and counts.get("rows") == key["row_count"],
        f"result.json declares counts.rows={counts.get('rows')!r}; the rollup covers the "
        f"whole book, which is {key['row_count']} application(s)",
    )

    # -- the marginals the on-call dashboard scrapes -------------------------
    for name in DISPOSITIONS:
        want_n = key["disposition_totals"][name]
        got_n = sum(1 for row in got.values() if _cell(row, "disposition") == name)
        ctx.check_l1(
            f"rollup_total_{name}",
            got_n == want_n and counts.get(name) == want_n,
            f"the file carries {got_n} {name!r} line(s) and declares "
            f"counts.{name}={counts.get(name)!r}; tonight's book holds {want_n}",
        )

    for what, want_n in sorted(key["retired"].items()):
        ctx.check_l1(
            f"rollup_retired_{what}",
            retired.get(what) == want_n,
            f"result.json says retired.{what}={retired.get(what)!r}; the tenant has retired "
            f"{want_n} (the {what} envelope reports total="
            f"{key['envelope_totals'].get('candidates' if what == 'candidates' else 'jobs')})",
        )

    # -- D1/D2: one disposition verdict per witness --------------------------
    for aid in witnesses:
        want = want_rows[aid]
        row = got.get(aid)
        ctx.check_l1(
            f"rollup_disposition_{aid}",
            row is not None and _cell(row, "disposition") == want["disposition"],
            f"{aid} (stage {want['stage']}, requisition {want['requisition_id']}, candidate "
            f"{want['candidate_id']}) belongs on tonight's file as {want['disposition']!r}; "
            f"the pass wrote {_cell(row, 'disposition')!r}"
            if row is not None
            else f"{aid} belongs on tonight's file as {want['disposition']!r} and is absent",
        )

    # -- D3: one last_change_at verdict per witness --------------------------
    for aid in witnesses:
        want = want_rows[aid]
        row = got.get(aid)
        ctx.check_l1(
            f"rollup_last_change_{aid}",
            row is not None and _cell(row, "last_change_at") == want["last_change_at"],
            f"{aid} last changed at {want['last_change_at']} across the application, "
            f"requisition {want['requisition_id']} and candidate {want['candidate_id']}; "
            f"the pass wrote {_cell(row, 'last_change_at')!r}"
            if row is not None
            else f"{aid} is absent from the file, so it carries no last_change_at",
        )

    # -- the two artifacts have to describe the same pass --------------------
    csv_rows = report or []
    csv_tuples = sorted(
        tuple(_cell(row, column) for column in CSV_COLUMNS) for row in csv_rows
    )
    json_tuples = sorted(
        tuple(_cell(row, column) for column in CSV_COLUMNS) for row in got.values()
    )
    ctx.check_l1(
        "rollup_csv_and_result_agree",
        bool(json_tuples)
        and len(csv_rows) == key["row_count"]
        and all(set(row) == set(CSV_COLUMNS) for row in csv_rows)
        and csv_tuples == json_tuples,
        f"rollup.csv carries {len(csv_rows)} line(s), result.json carries {len(json_tuples)}; "
        f"the two disagree on {len(set(csv_tuples) ^ set(json_tuples))} line(s)",
    )

    # -- conduct on the published budget --------------------------------------
    # Gated on the pass having read all three ledgers: "was not throttled" is
    # free for a pass that issued nothing.
    read_everything = set(key["list_paths"].values()) <= listed
    throttled = [entry for entry in log if entry.get("rate_limited")]
    ctx.check_l1(
        "rollup_fits_the_published_request_budget",
        read_everything and not throttled,
        (
            f"{len(throttled)} request(s) were throttled while building tonight's file"
            if throttled
            else (
                f"{sum(1 for e in log if (e.get('method') or '').upper() == 'GET')} GET(s), "
                "none throttled"
                if read_everything
                else "the pass did not read all three ledgers, so a small request count "
                     "proves nothing"
            )
        ),
    )
