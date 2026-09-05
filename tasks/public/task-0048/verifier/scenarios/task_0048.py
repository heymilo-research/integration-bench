"""task-0048 -- tombstone_semantics_migration (Paygrade).

A `migrate` task. Brackett HR Systems is being decommissioned; its closure
archive (`input/brackett_closure_archive.csv`, 101 rows, baked into the
submitted repo) is the last artifact it will ever produce, and the closure
pipeline has to come off it and onto Paygrade. Brackett wrote a closure as an
inline record state with a `closed_on` date. Paygrade has no such thing: a
closed record is simply GONE from its collection, with no flag anywhere, and
the only surface in the whole API that says a record went away -- or when -- is
`method=listTombstones`. Migrating the pipeline means rebuilding the closure
register out of that feed and out of the live collections, and applying the
Paygrade-native representation for the records Brackett closed and Paygrade
did not.

Grounded in the live seed at CHECKPOINT=50, the end of the widened 62-entry
timeline (`vendors/paygrade/src/paygrade/mutations.py`, `required_checkpoint(61)
== 50`). CHECKPOINT is load-bearing and deliberately NOT the scaffold's `0`: at
0 this vendor applies ZERO mutations and publishes ZERO tombstones, so a task
named `tombstone_semantics_migration` would have had no tombstones to migrate
(DEVICE-ARMING.md inertness mode 2). At 50 the feed carries 19 rows across
THREE entity kinds -- 10 employee, 6 assignment, 3 payrun. Nothing else on this
vendor pins a checkpoint. `MUTATIONS_VARIANT` is left at `default`.

Every group membership is measured, never asserted: see
`tools/rework/gen_answer_key_0160.py`, which boots the real `paygrade:local`
image at checkpoints 50 and 0, crawls the public HTTP API, and emits the
archive file and answer key from those observations so they cannot drift.

NO FAULT KNOB IS ARMED, AND THAT IS A DECISION
----------------------------------------------
The scaffold armed `FAULT_TOMBSTONE_SEMANTICS_MIGRATION`, which exists in no
paygrade source (`tools/rework/check_phantom_knobs.py`) and could never have
fired. Nothing replaces it. `logs.hot_loop_violations` fingerprints a request
as `(method, path, query)` and EXCLUDES the body (`logs.py:323-324`); paygrade
is JSON-RPC, so the RPC selector rides in the QUERY STRING and this task's 21
distinct `updateEmployee` writes are 21 IDENTICAL fingerprints.
`no_hot_loop_on_error` only runs when the request log holds a 401 or a `>= 500`
(`builtin_l2.py:325`), and task-0177 measured gold at 129/130 failing that
check alone once a `:500:` fault armed it. This task takes the second of the
two honest routes out (DEVICE-ARMING.md, paygrade section): it keeps the
401/5xx slice EMPTY by construction -- correct auth, no transport fault, every
write valid -- rather than moving a fault off `:500:` the way task-0177 did.
That is declining a conduct check on a vendor whose fingerprint is unsound for
JSON-RPC, not hiding a defect; the fingerprint lives in the public harness,
which is out of bounds.

The scaffold also set NEITHER `REQUEST_LOG_PATH` nor `TOKEN_LOG_PATH`, so the
vendor's request log would not have been written where the harness looks and
every request-log check -- all of `builtin_l2` included -- would have graded an
empty list under docker-compose while probing perfectly on the rig. Both are
set now. `entry.command` was `["java","Main"]` against a repo holding no
`Main`, which SILENTLY SKIPS the stub probe; it is a real Python package now
and `track:` follows the code.

THREE DEVICES
-------------

**D1 (belief-reality divergence -- TASK-LOCAL,
`docs/brackett-paygrade-cutover-note.md`, attributed to Brackett HR Systems
rev 2025-08).** The note positively asserts that Brackett's bridge recomputed
every worker's running-placement count from Paygrade on each nightly pass, that
the archive's `open_placements` column IS that reconciled number, and that
"there is nothing to be gained by re-deriving that number from
`listAssignments` at cutover time". The tenant has moved on since the archive
was cut, and the column is wrong in BOTH directions, so no blanket policy
toward it is right: 14 workers the archive says have nothing running are
holding an ACTIVE placement in Paygrade today, and 12 the archive says are
still running hold none -- 3 of those 12 because every placement they hold is
`ended`, which is the half a re-derivation that ignores `status` still gets
wrong. The ticket's rule -- a worker is closed only once none of their
placements is still running -- turns each of those into a wrong decision AND a
wrong write. Measured consequence: 26 wrong outcomes, 14 employees terminated
who must not be, 12 left open who must be closed, and all 18 `blocked_by` lists
missing, because a pass driven off the column never reads the placements at
all.

Every WORKER group excludes workers who ALREADY hold `terminated`: on such a
row closing them and not closing them produce the same observation, so the row
would have handed free mass to both. The generator asserts it.

**D2 (the vendor mechanic -- tombstone semantics).** Paygrade's delete mode is
`tombstone_endpoint` for all three entity kinds (`vendors/paygrade/vendor.yaml`,
`soft_delete.mode`): a closed record is gone from its collection AND from its
`get*` method, with no `is_deleted` field anywhere. So a live crawl proves a
record is ABSENT but cannot tell "Paygrade closed this" (12 archive rows,
which must carry the removal instant Paygrade published) from "Paygrade never
held this id" (15 rows). `listTombstones` is the only surface that separates
them and the only source of the instant -- the archive carries Brackett's own
`closed_on` date and nothing else. The feed is also the ONLY route to the 7
removals the archive does not carry at all, which the register has to report.
This device is not optional: there is no second route to any of those answers.

The same mechanic has a second, sharper half, and the note is wrong about it:
**Paygrade does not cascade.** Removing a worker leaves every placement they
held sitting in `listAssignments`, `status: active`, with an `employee_id` that
no longer resolves -- 10 of them at this checkpoint. The note asserts the
opposite ("Paygrade removes a worker's placements when it removes the worker
... its `listTombstones` entry arrives alongside the worker's") and tells the
integrator to resolve a `PLACEMENT` row from its worker's outcome instead of
looking it up. That shortcut is wrong in both directions here: the 10 orphans
read as closed when Paygrade still holds them, and the 5 archive rows for
placements Paygrade really did remove read as live, because all six of this
vendor's removed placements belong to workers who are still on the books.
Measured consequence: 15 wrong outcomes and 5 missing removal instants.

**D3 (belief-reality divergence -- the note AND the vendor's own guide agree,
and both are wrong).** The note asserts that "Paygrade never removes a pay
period ... the delete feed has no `payrun` entries in it", and concludes that
`PERIOD` rows settle from the archive with no Paygrade lookup at all. The
vendor's `docs/entities.md` backs it up: its tombstone table says the `entity`
field is "`employee` or `assignment`", and `docs/index.md` lists the tenant's
resources as employees and assignments only. The wire disagrees -- the feed
carries three `payrun` rows at this checkpoint, and `listPayruns`/`getPayrun`
are live methods, which the note itself names one section earlier when it
describes how Brackett mirrored the tenant. Two written sources agreeing does
not make them right (the shape task-0177 measured on its own D2). The
archive's 31 PERIOD rows are the whole surface of this device -- 21 periods
Paygrade still holds, 2 it removed, and 8 whose ids it never had, because
Brackett's ledger predates the tenant. Measured consequence: 29 wrong outcomes
and 2 missing removal instants.

Paygrade's four vendor lies (`vendors/paygrade/LIES.md`) are NOT the graded
devices here. Three announce their own fix on the first call -- the `403` body
names `X-PG-Token`, the `listEmployees` error body names the undocumented
`company_id`, `bulkSync` answers `501` naming the paginated alternative -- and
the starter's transport is faithful to `docs/auth.md`'s (dead) HTTP Basic, so a
submission has to get past them to score anything at all. They are deliberately
not measured through, so they cannot swamp the three devices above
(AUTHORING-CHECKLIST, "Don't let one device swamp another"). The fourth --
every RPC refusal arriving as HTTP 200 with a JSON-RPC `error` body -- is the
punisher for the register-trusting route: a pass that writes to a worker
Paygrade has already removed gets a `200` and nothing lands.

MEASURED (fresh probes, 2026-08-11; see the WORKLOG entry for this task)

    gold    207/207 = 1.000    starter   0/207 = 0.000
    stub      0/207 = 0.000    naive    70/207 = 0.338

    floor 0.000 (<= 0.40 x gold, the migrate rule) - headroom 1.000 -
    discriminating 207 - stub probe RAN.

    starter : plumbing only -- transport, config, CLI and the three artifact
              writers, all written as a competent engineer reading `docs/`
              would write them, including `docs/auth.md`'s HTTP Basic. The four
              functions that ARE the job raise NotImplementedError.
    naive   : a tidy implementation written from `docs/` and Brackett's cutover
              note and nothing else. It authenticates correctly (the 403 body
              names the header, so lie 4 does not swamp the measurement) and it
              does sweep the removal feed, because `docs/entities.md` tells it
              to. It then takes all three of the note's shortcuts: the archive's
              `open_placements` decides every worker, a `PLACEMENT` row is
              resolved from its worker, and `PERIOD` rows settle from the file.

    starter <-> naive differing: **137 naive-favouring, 0 starter-favouring.**
    The 0 is structural, not a thin naive: on a `migrate` task whose starter is
    unimplemented plumbing, the starter passes nothing, so the naive cannot
    regress a check it passed.

WRONG-ANSWER BASINS, all built and probed on the same rig (`naive.patch`
swapped for each, the real one restored afterwards) -- numbers in the WORKLOG:

  * DEFENSIVE -- the naive plus the obvious conservative guard: re-derive
    placement membership from `listAssignments`, but treat every assignment as
    still blocking, including those whose status is `ended`. It retains the
    note's worker-cascade shortcut for placement rows and its claim that period
    rows settle from the archive. **146/207 = 0.705.**
  * SECOND-GUESS -- the wrong answer after noticing both stale placement counts
    and deleted workers: again count ended assignments as blockers, and also
    diagnose a worker tombstone as `not_in_paygrade` rather than a Paygrade
    closure. It retains the same stale-note placement and period shortcuts.
    **136/207 = 0.657.**
  * ALTERNATIVE-CORRECT -- structurally unlike the reference: no collection
    crawl at all for the archive's own ids (each one resolved singly through
    `getEmployee`/`getAssignment`/`getPayrun`, reading the `error` object out
    of a 200 to tell absence from presence), the placement index built from a
    `modified_since=0` crawl, and the removal feed swept from a later `since`
    and scanned in reverse. **207/207 = 1.000**, so these checks are not
    over-fitted to the reference implementation's shape.

    The naive's 137 failures: 70 outcomes (14 W2 + 3 W3 + 9 W4 + 5 P1 + 10 P2 +
    21 R2 + 8 R3), 18 `blocked_by`, 14 wrongly-terminated workers, 14 writes
    that must not have been sent, 12 workers left open who had to be closed,
    7 removal instants, the counts and the write volume.

Grading is evidence-based throughout: per-row outcomes against
`verifier/fixtures/answer_key.json`, every closure claim checked by crawling
the LIVE vendor over HTTP rather than trusting `writeback_log.json`, and every
write-volume and no-write assertion read off the vendor's own request log,
sliced BEFORE the verifier issues a single request of its own. No whole-file
comparison anywhere.
"""

import csv
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "paygrade"
BASE_PATH = "/api/rpc"


class PaygradeScenarioError(RuntimeError):
    pass


def _load_json(ctx, name: str):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _load_csv(ctx, name: str):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, ValueError):
        return None


def _blank_to_none(value):
    """`""` and `None` are the same absence; everything else compares as-is."""
    if value is None or value == "":
        return None
    return value


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_id_list(value):
    """A `blocked_by` cell, from either artifact, as a sorted list of ids."""
    if isinstance(value, list):
        return sorted(str(v) for v in value if str(v))
    if isinstance(value, str):
        return sorted(part for part in value.split(";") if part)
    return []


def _rows_by_ref(result) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(result, dict):
        return out
    for row in result.get("rows") or []:
        if isinstance(row, dict) and row.get("ref"):
            out[str(row["ref"])] = row
    return out


# -- the verifier's own, independent Paygrade client -------------------------
# `X-PG-Token` here is the verifier's own privileged transport credential, not
# a requirement graded on the submission. The ungrounded-check audit traces
# its value to `ctx.secrets` and exempts only that header key; action headers,
# routes, query fields, and response fields remain auditable. On the vendor
# side this is lie 4 (`wrong_auth_route`) and ordinary sandbox traffic exposes
# it in the first 403 body.

def _pg_call(ctx, rpc_method: str, params: dict) -> dict:
    base = ctx.vendor(VENDOR).base_url.rstrip("/")
    query = {"method": rpc_method, **{k: v for k, v in params.items() if v is not None}}
    url = f"{base}{BASE_PATH}?{urllib.parse.urlencode(query)}"
    token = ctx.secrets.get("PG_APP_TOKEN", "paygrade-test-key")
    req = urllib.request.Request(url, headers={"X-PG-Token": token}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise PaygradeScenarioError(
            f"GET {rpc_method} -> HTTP {exc.code}: "
            f"{exc.read().decode('utf-8', 'replace')[:200]}"
        ) from exc
    doc = json.loads(raw) if raw else {}
    if "result" not in doc:
        raise PaygradeScenarioError(f"GET {rpc_method} returned no result: {str(doc)[:200]}")
    return doc["result"]


def _crawl(ctx, rpc_method: str, **params) -> list[dict]:
    """Every row of one list method, over HTTP, from the live vendor."""
    out: list[dict] = []
    start = 0
    while True:
        page = _pg_call(ctx, rpc_method, {**params, "start": start, "count": 100})
        for rec in page.get("rows") or []:
            if isinstance(rec, dict):
                out.append(rec)
        if not page.get("more"):
            return out
        start = int(page["start"]) + int(page["count"])


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    truth = {r["ref"]: r for r in key["rows"]}

    # The classic grading lane initially boots every vendor at checkpoint 0,
    # regardless of the task's Compose default. This task's tombstones do not
    # exist there, so pin the measured world explicitly in every lane.
    ctx.vendor(VENDOR).recreate(checkpoint=key["checkpoint"])

    code, _out, err = ctx.app.run()
    result = _load_json(ctx, "result.json")
    report = _load_csv(ctx, "import_report.csv")
    writeback_log = _load_json(ctx, "writeback_log.json")

    ctx.check_l1(
        "pgclos_cutover_pass_completed",
        code == 0
        and isinstance(result, dict)
        and isinstance(report, list)
        and isinstance(writeback_log, list),
        f"exit={code} result={type(result).__name__} report={type(report).__name__} "
        f"writeback_log={type(writeback_log).__name__} stderr={err[:400]}",
    )

    result_rows = result.get("rows") if isinstance(result, dict) else None
    result_rows = result_rows if isinstance(result_rows, list) else []
    result_refs = [
        str(row["ref"])
        for row in result_rows
        if isinstance(row, dict) and row.get("ref")
    ]
    result_rows_integral = (
        len(result_rows) == key["total_rows"]
        and len(result_refs) == key["total_rows"]
        and len(set(result_refs)) == key["total_rows"]
    )
    by_ref = _rows_by_ref(result)
    ctx.check_l1(
        "pgclos_result_covers_every_archive_row",
        result_rows_integral and set(by_ref) == set(truth),
        f"result.json carries {len(result_rows)} raw row(s), {len(set(result_refs))} "
        f"unique ref(s), and {len(by_ref)} after collapsing; Brackett's closure "
        f"archive holds {key['total_rows']}",
    )
    identity_ok = all(
        by_ref.get(ref, {}).get("record_kind") == expected["record_kind"]
        and by_ref.get(ref, {}).get("pg_id") == expected["pg_id"]
        and set(by_ref.get(ref, {}))
        == {"ref", "record_kind", "pg_id", "outcome", "removed_at", "blocked_by"}
        for ref, expected in truth.items()
    )
    ctx.check_l1(
        "pgclos_result_row_identity_and_schema_exact",
        identity_ok,
        "every result row must preserve its archive identity and exact declared schema",
    )

    report_rows = report if isinstance(report, list) else []
    report_refs = [
        str(row["brackett_ref"])
        for row in report_rows
        if isinstance(row, dict) and row.get("brackett_ref")
    ]
    report_rows_integral = (
        len(report_rows) == key["total_rows"]
        and len(report_refs) == key["total_rows"]
        and len(set(report_refs)) == key["total_rows"]
    )
    report_by_ref = {
        str(r.get("brackett_ref")): r
        for r in report_rows
        if isinstance(r, dict) and r.get("brackett_ref")
    }
    # Anchored on the ARCHIVE, not merely on `result.json`: two empty artifacts
    # agree with each other, and a check that compares empty to empty is free
    # for a submission that produced nothing.
    ctx.check_l1(
        "pgclos_import_report_agrees_with_result",
        report_rows_integral
        and set(report_by_ref) == set(truth)
        and set(report_by_ref) == set(by_ref)
        and all(
            _blank_to_none(report_by_ref[ref].get("outcome"))
            == _blank_to_none(by_ref[ref].get("outcome"))
            for ref in report_by_ref
        ),
        f"import_report.csv carries {len(report_by_ref)} row(s) against result.json's "
        f"{len(by_ref)}, and the two must agree row for row",
    )
    report_exact = all(
        report_by_ref.get(ref) == {
            "brackett_ref": ref,
            "record_kind": expected["record_kind"],
            "pg_id": expected["pg_id"],
            "outcome": expected["outcome"],
            "removed_at": "" if expected["removed_at"] is None else str(expected["removed_at"]),
            "blocked_by": ";".join(expected["blocked_by"]),
        }
        for ref, expected in truth.items()
    )
    ctx.check_l1(
        "pgclos_import_report_exact",
        report_rows_integral and report_exact,
        "every CSV cell must exactly match the archive and Paygrade-derived truth",
    )

    ctx.check_l1(
        "pgclos_outcome_counts_match",
        isinstance(result, dict)
        and isinstance(result.get("counts"), dict)
        and {str(k): int(v) for k, v in result["counts"].items()} == key["expected_counts"],
        f"result.json reported counts={(result or {}).get('counts')!r}, expected "
        f"{key['expected_counts']}",
    )

    # -- the connector's own writes, from the vendor's request log -----------
    # Taken here, before the verifier issues a single request of its own, so
    # nothing below can be satisfied by verifier traffic.
    request_log = ctx.vendor(VENDOR).request_log()
    writes = [
        e for e in request_log
        if e.get("method") == "POST"
        and e.get("path") == BASE_PATH
        and (e.get("query") or {}).get("method") == "updateEmployee"
    ]
    written_ids = [
        str((e.get("body") or {}).get("id"))
        for e in writes
        if isinstance(e.get("body"), dict) and (e.get("body") or {}).get("id")
    ]
    wrote_something = bool(writes)

    # These shared conduct names are traffic-conditional inside builtin_l2.
    # Emit evidence-gated instances on every probe so a starter/stub with no
    # request traffic fails them instead of deleting them from the denominator.
    data_requests = [
        entry for entry in request_log
        if str(entry.get("path") or "").startswith(BASE_PATH)
    ]
    credential_query_keys = {
        "access_token", "token", "pg_token", "api_key", "client_secret"
    }
    query_leaks = [
        entry for entry in data_requests
        if credential_query_keys & set((entry.get("query") or {}).keys())
    ]
    ctx.check_l1(
        "no_credentials_in_query_string",
        bool(data_requests) and not query_leaks,
        f"data_requests={len(data_requests)} credential_query_requests={len(query_leaks)}",
    )

    secret_values = {str(value) for value in ctx.secrets.values() if value}
    misplaced_secrets = []
    for entry in data_requests:
        rendered = json.dumps(
            {"path": entry.get("path"), "body": entry.get("body")},
            sort_keys=True,
            default=str,
        )
        if any(secret in rendered for secret in secret_values):
            misplaced_secrets.append(entry)
    ctx.check_l1(
        "no_secrets_echoed_to_vendor",
        bool(data_requests) and not misplaced_secrets,
        f"data_requests={len(data_requests)} misplaced_secret_requests="
        f"{len(misplaced_secrets)}",
    )

    # -- per-row outcomes ----------------------------------------------------
    for ref, expected in truth.items():
        row = by_ref.get(ref)
        got = _blank_to_none(row.get("outcome")) if row else None
        ctx.check_l1(
            f"pgclos_outcome_{ref}",
            row is not None and got == expected["outcome"],
            f"{ref} ({expected['record_kind']} {expected['pg_id']}, group "
            f"{expected['group']}, archive says open_placements="
            f"{expected['file_open_placements']}): outcome={got!r}; Paygrade's own "
            f"state makes this row {expected['outcome']!r}",
        )

    # -- D2/D3: the removal instant Paygrade published -----------------------
    # Requirement-shaped and reachable from exactly one surface: the record is
    # gone from its collection and from its `get*` method, so `listTombstones`
    # is the only thing that carries this number.
    for ref in key["removal_stamp_refs"]:
        expected = truth[ref]
        row = by_ref.get(ref)
        got = _as_int(_blank_to_none(row.get("removed_at"))) if row else None
        ctx.check_l1(
            f"pgclos_removal_stamp_{ref}",
            got == expected["removed_at"],
            f"{ref} ({expected['record_kind']} {expected['pg_id']}): removed_at={got!r}; "
            f"Paygrade published this removal at {expected['removed_at']!r}",
        )

    # -- D2: the removals the archive never knew about -----------------------
    discovered_rows = result.get("discovered") if isinstance(result, dict) else None
    discovered_rows = discovered_rows if isinstance(discovered_rows, list) else []
    discovered_ids = [
        str(entry["id"])
        for entry in discovered_rows
        if isinstance(entry, dict) and entry.get("id")
    ]
    discovered_rows_integral = (
        len(discovered_rows) == len(key["discovered"])
        and len(discovered_ids) == len(key["discovered"])
        and len(set(discovered_ids)) == len(key["discovered"])
    )
    reported_discovered = {}
    for entry in discovered_rows:
        if isinstance(entry, dict) and entry.get("id"):
            reported_discovered[str(entry["id"])] = entry
    ctx.check_l1(
        "pgclos_discovered_set_matches_the_feed",
        discovered_rows_integral
        and set(reported_discovered) == {d["id"] for d in key["discovered"]},
        f"the pass reported {len(discovered_rows)} raw removal(s), "
        f"{len(set(discovered_ids))} unique id(s), and {sorted(reported_discovered)} "
        f"after collapsing; the archive does "
        f"not carry; Paygrade published {sorted(d['id'] for d in key['discovered'])}",
    )
    for entry in key["discovered"]:
        got = reported_discovered.get(entry["id"])
        ctx.check_l1(
            f"pgclos_discovered_{entry['id']}",
            isinstance(got, dict)
            and str(got.get("entity")) == entry["entity"]
            and _as_int(got.get("deleted_at")) == entry["deleted_at"],
            f"{entry['id']}: reported {got!r}; Paygrade removed this "
            f"{entry['entity']} at {entry['deleted_at']} and the archive says nothing "
            "about it",
        )

    await builtin_l2(ctx, app_runs=1)

    # -- vendor truth --------------------------------------------------------
    live_employees = {
        rec["id"]: rec
        for rec in _crawl(ctx, "listEmployees", company_id="verifier")
        if isinstance(rec, dict) and rec.get("id")
    }

    ctx.check_l1(
        "pgclos_write_witness",
        wrote_something,
        "the pass sent no updateEmployee write at all; every closure and no-write "
        "check below is gated on this",
    )

    # -- D1: the workers this cutover had to close, on the live record -------
    for ref in key["close_refs"]:
        expected = truth[ref]
        record = live_employees.get(expected["pg_id"])
        got = record.get("status") if isinstance(record, dict) else None
        ctx.check_l1(
            f"pgclos_closed_{ref}",
            wrote_something and got == "terminated",
            f"{ref} (group {expected['group']}): {expected['pg_id']} holds "
            f"status={got!r}; Brackett closed this worker, Paygrade still had them on "
            f"the books and nothing of theirs is still running, so Paygrade must now "
            "hold 'terminated'",
        )

    # -- D1: the workers it had to leave alone -------------------------------
    for ref in key["blocked_refs"]:
        expected = truth[ref]
        record = live_employees.get(expected["pg_id"])
        got = record.get("status") if isinstance(record, dict) else None
        ctx.check_l1(
            f"pgclos_left_open_{ref}",
            wrote_something and got == expected["before_status"],
            f"{ref} (group {expected['group']}): {expected['pg_id']} holds "
            f"status={got!r}, it held {expected['before_status']!r} before the pass; "
            f"{len(expected['blocked_by'])} of their placements are still running, so "
            "this worker must not have been closed",
        )
        hits = [i for i in written_ids if i == expected["pg_id"]]
        ctx.check_l1(
            f"pgclos_no_write_{ref}",
            wrote_something and not hits,
            f"{ref}: the pass sent {len(hits)} updateEmployee write(s) for "
            f"{expected['pg_id']}, whose placements {expected['blocked_by']} are still "
            "running",
        )
        # Requirement-shaped half of the same device: ops cannot action a
        # blocked row without knowing what is holding it open, and the answer
        # exists only in the live placement collection.
        row = by_ref.get(ref)
        got_blockers = _as_id_list(row.get("blocked_by")) if row else []
        report_row = report_by_ref.get(ref)
        report_blockers = _as_id_list(report_row.get("blocked_by")) if report_row else []
        ctx.check_l1(
            f"pgclos_blocked_by_{ref}",
            got_blockers == expected["blocked_by"]
            and report_blockers == expected["blocked_by"],
            f"{ref}: the pass names {got_blockers!r} (report: {report_blockers!r}) as "
            f"what is holding {expected['pg_id']} open; Paygrade has "
            f"{expected['blocked_by']!r} still running against them",
        )

    ctx.check_l1(
        "pgclos_close_volume_matches_pending_closures",
        len(writes) == key["expected_close_count"],
        f"the pass sent {len(writes)} updateEmployee write(s); "
        f"{key['expected_close_count']} of the archive's {key['total_rows']} rows are a "
        "worker Paygrade still holds with nothing running",
    )

    ctx.check_l1(
        "pgclos_never_wrote_to_a_record_paygrade_does_not_hold",
        wrote_something and all(i in live_employees for i in written_ids),
        f"{sorted(set(i for i in written_ids if i not in live_employees))} were written "
        "to and Paygrade holds no such worker; a refusal here is a 200 with an error "
        "body, so nothing landed and the pass cannot have known it",
    )

    logged_ids = sorted(
        str(e.get("pg_id"))
        for e in (writeback_log or [])
        if isinstance(e, dict) and e.get("pg_id")
    )
    ctx.check_l1(
        "pgclos_writeback_log_matches_request_log",
        wrote_something and logged_ids == sorted(written_ids),
        f"writeback_log.json claims {len(logged_ids)} closure(s); the vendor's request "
        f"log recorded {len(written_ids)} updateEmployee call(s)",
    )

    outside = key["untouched_employees"]
    drifted = [
        eid for eid, before in outside.items()
        if live_employees.get(eid) is None
        or _blank_to_none(live_employees[eid].get("status")) != _blank_to_none(before)
    ]
    ctx.check_l1(
        "pgclos_workers_outside_the_archive_untouched",
        wrote_something and not drifted,
        f"{len(drifted)} of the {len(outside)} worker(s) the archive does not name have "
        f"changed employment status: {drifted[:6]}",
    )
