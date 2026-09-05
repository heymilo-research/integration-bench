"""task-0029 — intra_file_dedupe_canonicalize.

One import of the agency's signup export, then a re-run over the same file.

The export holds 27 submissions from 12 people. Five of those people are already
on CrewCall's roster, seven are not, and one of the seven is a re-signup of
somebody the tenant soft-deleted at CHECKPOINT 1. Nobody appears once and
cleanly: every person arrives two to four times with the name in a different
case, the whitespace mangled, the phone reformatted five different ways, and a
different email each time.

`docs/` holds CrewCall's own documentation, byte-identical to the vendor's, plus
ONE task-local document — `docs/riverside-signup-runbook.md`, Workforce Ops'
internal note, disclaimed in its third line as "our own note, not CrewCall's
documentation". The vendor is honest (`LIES.md`: `docs.lies: []`); the runbook is
where this tenant's beliefs live, and two of them are false.

SIX independent things have to go right. Each was made to fail on its own
against the live vendor and the consequence measured (rig probe, this scenario,
102 checks; gold = 102/102, empty submission and harness stub = 0).

1. THE ROWS MUST COLLAPSE BEFORE ANYTHING IS WRITTEN. `POST /v1/workers` has no
   idempotency key and no server-side de-duplication, and CrewCall has no update
   and no delete endpoint, so a duplicate is permanent. MEASURED —
   `variants/report-correct-write-per-row.patch`, an import whose REPORT is
   byte-perfect (12 people, right groups, right survivors, right canonical
   values, right outcomes) but which issues one create per submission row for
   each new person: 17 creates instead of 7, 136 live workers instead of 126,
   and every one of the seven new people held two-to-four times by a tenant with
   no merge tool. **75/102 = 0.735**, losing 27 checks. Against the previous
   revision of this scenario — which asserted upstream cardinality once, as a
   summary — the same connector scored 0.850; the trap was always real and only
   the scoring failed to charge for it, which is why the roster is now asserted
   person by person, twice (before and after the re-run).

2. THE IDENTITY KEY IS THE PHONE, NOT THE EMAIL. Emails differ WITHIN a person's
   group (personal address one week, agency address the next, `+tag` variants),
   and for all five people who already exist the address the agency holds is not
   the address CrewCall holds. MEASURED — an otherwise-correct import keyed on
   the canonicalised email collapses the 27 rows to 22 people instead of 12: one
   bogus group holding the five rows that carry no email at all, and the same
   human split across their personal, agency and `+tag` addresses.

3. THE PHONE HAS TO BE CANONICALISED ON BOTH SIDES. The file writes the same
   number as `+1-555-7085`, `(555) 7085`, `555.7085`, `1-555-7085` and
   `+1 (555) 7085`; the roster stores `+1-555-7085`.

4. **DIVERGENCE A — "we take that column at face value."** Riverside's export
   carries an `already_placed` column, and the runbook says in as many words
   that Workforce Ops trust it: *"if Riverside say somebody is already placed,
   they are already placed; they have the same API we do"*, and *"the crawl is
   now only there for the people the column marks `no`."* The wire says
   otherwise. MEASURED against a live tenant and pinned in the key as
   `already_placed_false_yes`: the column claims `yes` for **5550505 (Priya
   Raghunathan), 5550606 (Sam Okoro) and 5552582 (Tim Wang)**, and CrewCall
   holds no live worker for any of them. An import that believes the column
   never signs those three humans up — and, unlike every other failure in this
   task, that damage is a person who is simply ABSENT, which nothing downstream
   trips over. Root cause: a partner's stale cross-tenant flag. The column is
   also wrong the other way for four people (`already_placed_false_no`), which
   is invisible to a believer: it sends them to the roster crawl, where (5)
   waits. MEASURED in isolation by `variants/defensive.patch`, which converges
   its crawl properly and changes nothing else: 4 creates instead of 7, 123 live
   workers instead of 126, Priya Raghunathan, Sam Okoro and Tim Wang left off
   the roster permanently, and 24 checks lost — **78/102 = 0.765.**

5. **DIVERGENCE B — "the second pass has never once discovered an id the first
   pass had not already seen."** The runbook says the tenant is quiet at 02:10
   and that a single forward pass is what Workforce Ops run, explicitly against
   CrewCall's own `docs/pagination.md`, which is restored byte-identical here
   and which states the churn, the cross-page duplicate, the record that shifts
   behind the cursor, and the dedupe-and-re-crawl-until-clean recipe in full.
   The wire sides with the vendor. This tenant runs a four-shift churn schedule
   (`CC_DRIFT_SCHEDULE`, armed by `run` below through `recreate(env=...)` so the
   shifted ids are not handed to the agent's dev loop), and the four workers it
   clocks in are wkr_0084, wkr_0120, wkr_0076 and wkr_0100 — four of the five
   already-employed people the file names. The fifth, wkr_0009 (Nikola Mbeki),
   sits near the top of the roster and cannot be pushed behind a forward
   crawler; it is the control that proves a broken scan is not simply failing to
   read anything. The key pins what a single pass misses, measured live: at
   limit=10 and limit=25 all four (people 5551424, 5553495, 5557085, 5557800);
   at limit=50, wkr_0084 and wkr_0120 — the schedule ORDER is picked for that,
   see `gen_answer_key_0057.py`. A one-pass scan does not produce a missing
   record — it produces a SECOND copy of somebody the tenant already employed,
   permanently, on a vendor with no merge tool. MEASURED in isolation by
   `variants/verify-everyone.patch`, which ignores the column and decides every
   person against the roster and changes nothing else: 11 creates instead of 7,
   130 live workers instead of 126, Bhupen Santos, Charles Rossi, Emmy Jansen
   and Sophie Byrne each held twice, and 24 checks lost — **78/102 = 0.765.**

6. A SOFT-DELETED MATCH IS NOT A MATCH. Tim Wang (wkr_0013) is `is_deleted: true`
   at CHECKPOINT 1 and still served by the list endpoint. He is in the file, and
   the tenant's rule is that a deleted worker is not on the roster: his row is a
   fresh signup, not a skip and not a resurrection. He is also one of the three
   people the `already_placed` column lies about, so an import that trusts the
   column reaches the right-looking answer ("skipped") for the wrong reason and
   leaves a human off the roster.

MEASURED VARIANTS (all under `variants/`, all run through
`tools/rig/floor_rig2.py --mode naive` with the patch swapped in):

| variant | what it is | score |
|---|---|---|
| gold (`solution.patch`) | converging crawl, column ignored | **102/102 = 1.000** |
| starter | the shipped skeleton | 0/99 = 0.000 |
| harness stub | empty submission | 0/99 = 0.000 |
| `naive.patch` | tidy, faithful to `docs/` INCLUDING the runbook: one forward pass, column taken at face value | **60/102 = 0.588** |
| `variants/defensive.patch` | naive + the one guard a cautious engineer adds without having observed anything: follow `docs/pagination.md` and re-crawl until a clean pass. Fixes divergence B, keeps A | **78/102 = 0.765** |
| `variants/verify-everyone.patch` | naive + the other obvious guard: stop trusting the column, decide every person against the roster. Fixes A, keeps B | **78/102 = 0.765** |
| `variants/second-guess.patch` | notices that the column and the crawl disagree for seven of the twelve, concludes the CRAWL is the unreliable one (it does demonstrably race a re-sort) and lets the column win outright | **55/102 = 0.539** |
| `variants/report-correct-write-per-row.patch` | perfect report, one create per submission row | **75/102 = 0.735** |
| `variants/alt-correct.patch` | legitimate and structurally different: groups with `itertools.groupby` over an ascending sort, picks the survivor with one `max` over an inverted comparison tuple, converges the crawl by comparing two whole passes for equality at limit=50, matches by linear scan, rebuilds the phone into the tenant's house format instead of echoing the file's, and creates in descending identity order | **102/102 = 1.000** |

`defensive` and `verify-everyone` are the two-device proof. Each repairs exactly
one divergence and leaves the other untouched, and each still loses 24 of the
102 checks — so neither device is carrying the task on its own and neither is a
rounding error. The failures do not overlap: `defensive` loses the three people
the column lies about (5550505, 5550606, 5552582) and `verify-everyone` loses
the four the churn hides (5551424, 5553495, 5557085, 5557800), by construction —
the column's false `yes` values name only people the tenant does not employ and
the churn can only hide people it does.

`naive` takes both hits at once and loses 42: 8 creates instead of 7 (four
duplicates of people it already employed, three people never signed up at all),
127 live workers instead of 126, and seven of the twelve humans in the file
wrong at the report layer, at the tenant's-state layer, and again after the
re-run.

`second-guess` scores BELOW naive, which is the point of measuring it: letting
the column overrule the crawl also destroys idempotence, because the second run
reads the same column and signs the same four new people up a second time
(`rerun_reports_every_person_already_present`: created=8 on the re-run).

Evidence: every check reads the connector's declared artifact against the answer
key, the vendor's own request log, or the vendor's state crawled by this
verifier over its published port — never the connector's account of the vendor.
Every "the connector did not do X" check first proves the connector did
something: the per-person roster checks, the `i2_*` checks and `d4`/`d6`/`d7`/
`d9` all fail on an empty evidence slice. `builtin_l2` fires once, after the last
connector run and before this verifier touches the vendor, and the request
indices this verifier injected are excluded explicitly so the connector is not
graded for the verifier's own traffic.
"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _dedupe_family import (  # noqa: E402
    d1_groups_exact,
    d2_survivor_exact,
    d3_canonical_fields_exact,
    d4_one_record_per_person,
    d5_created_records_canonical,
    d6_write_count_exact,
    d7_scan_covered_collection,
    d8_created_key_set_exact,
    phone_key,
)
from _import_family import i2_preexisting_untouched, i4_final_cardinality, i6_idempotent  # noqa: E402

VENDOR = "crewcall"
CRAWL_LIMIT = 50
CANONICAL_FIELDS = ("first_name", "last_name", "email", "role")


def _read_report(ctx):
    path = Path(ctx.output_dir) / "dedupe_report.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _crawl(ctx) -> list[dict]:
    """The roster as the VENDOR holds it, read over the verifier's own HTTP path.

    Dedupes by id and re-crawls from offset 0 until a whole pass turns up nothing
    new, so the verifier is not itself fooled by the churn it is grading the
    connector on. The tenant's churn schedule is finite, so this terminates.
    """
    base = ctx.vendor(VENDOR).base_url
    api_key = ctx.secrets.get("CC_API_KEY", "")
    known: dict[str, dict] = {}
    while True:
        discovered = 0
        offset = 0
        while True:
            req = urllib.request.Request(
                f"{base}/v1/workers?offset={offset}&limit={CRAWL_LIMIT}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            envelope = json.load(urllib.request.urlopen(req, timeout=30))
            rows = envelope.get("data") or []
            for record in rows:
                if record["id"] not in known:
                    known[record["id"]] = record
                    discovered += 1
            limit = int(envelope.get("limit") or CRAWL_LIMIT)
            if len(rows) < limit:
                break
            offset += limit
        if discovered == 0:
            return list(known.values())


def _live(records):
    return [r for r in records if not r.get("is_deleted")]


def _held_for(live_records, person_key):
    """Ids of the LIVE workers the tenant holds under one identity key."""
    return sorted(
        str(r.get("id"))
        for r in live_records
        if phone_key(str(r.get("phone") or "")) == person_key
    )


def _one_live_row(live_records, ids_before, want, *, connector_talked):
    """The tenant holds exactly ONE live worker for this person, and it is the
    right one.

    This is the check both of the task's divergences land on, which is why it is
    asserted person by person rather than rolled into a summary:

      - a scan that believed a single forward pass reports somebody absent and
        signs them up again, so the tenant holds their identity TWICE;
      - an import that believed the export's `already_placed` column never signs
        somebody up at all, so the tenant holds their identity ZERO times.

    Neither is visible from the connector's own report, and the two failures
    point in opposite directions, so the count itself is the evidence.

    EVIDENCE, NOT SILENCE: an empty roster read, or a connector that never
    contacted the vendor, fails rather than passes.
    """
    if not connector_talked:
        return False, "the connector never contacted the vendor"
    if not live_records:
        return False, "no live records read back from the vendor — nothing to judge"
    key = want["person_key"]
    held = _held_for(live_records, key)
    who = f"{key} ({want['first_name']} {want['last_name']})"
    if len(held) != 1:
        expectation = (
            f"the record the tenant already held ({want['existing_worker_id']})"
            if want["outcome"] == "skipped" else "one freshly signed-up record"
        )
        return False, (
            f"{who}: the tenant holds {len(held)} live record(s) {held[:4]}, expected "
            f"exactly 1 — {expectation}"
        )
    got = held[0]
    if want["outcome"] == "skipped":
        return (
            got == str(want["existing_worker_id"]),
            f"{who}: held as {got}, expected the pre-existing {want['existing_worker_id']}",
        )
    before = {str(i) for i in ids_before}
    return (
        got not in before,
        f"{who}: the single live record {got} is one the tenant already had before "
        "the import — a new hire was expected",
    )


def _rows_this_import_added(live_records, ids_before):
    """Live rows the connector put there: present now, absent from the tenant before.

    Classified by id against the measured pre-import id set rather than by "the
    id looks new", so a vendor that assigns ids differently cannot fool it.
    """
    before = {str(i) for i in ids_before}
    return [r for r in live_records if str(r.get("id")) not in before]


def _added_rows_are_all_new_people(live_records, ids_before, created_keys):
    """Every row the import added belongs to somebody the tenant did not employ.

    The failure this catches is the one the roster's churn produces: an existence
    scan that misses somebody reports them absent, and the import signs up a
    second copy of a person the tenant already had. That row is indistinguishable
    from a legitimate new hire by id alone — only its identity key gives it away.

    Fails when the import added nothing at all: "every added row is a new person"
    is not a property a submission that wrote nothing gets to bank.
    """
    added = _rows_this_import_added(live_records, ids_before)
    if not added:
        return False, "the import added no rows at all — nothing to judge"
    want = {str(k) for k in created_keys}
    offenders = sorted(
        f"{r.get('id')} ({phone_key(str(r.get('phone') or ''))})"
        for r in added
        if phone_key(str(r.get("phone") or "")) not in want
    )
    return (
        not offenders,
        f"{len(offenders)} of {len(added)} added row(s) duplicate somebody the tenant "
        f"already employed: {offenders[:5]}" if offenders else
        f"all {len(added)} added row(s) are people the tenant did not employ",
    )


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    expected_people = key["people"]
    created_people = key["created_people"]
    created_keys = key["created_person_keys"]
    list_path = key["list_path"]
    write_path = key["write_path"]
    ids_before = key["roster_ids_before"]

    # This tenant clocks four workers in during a crawl rather than the vendor's
    # published three, and the four are exactly four of the five already-employed
    # people the signup file names (wkr_0009, the fifth, sits near the top of the
    # roster and cannot be pushed behind a forward crawler). Armed here rather
    # than in the task's docker-compose so the shifted ids are not handed to the
    # agent.
    ctx.vendor(VENDOR).recreate(
        checkpoint=key["checkpoints"]["import"],
        env={"CC_DRIFT_SCHEDULE": key["drift_schedule"]},
    )

    # -- the import -----------------------------------------------------------
    code, _out, err = ctx.app.run()
    report = _read_report(ctx)
    ctx.check_l1(
        "signup_import_completed",
        code == 0 and isinstance(report, dict),
        f"exit={code} report={type(report).__name__} stderr={err[:400]}",
    )

    # -- what the connector says it did ---------------------------------------
    # Emitted unconditionally: a submission that produced no artifact has failed
    # each of these, and hiding them behind an `if report` would shrink the
    # denominator for exactly the submissions that deserve the zero.
    body = report if isinstance(report, dict) else {}
    people = body.get("people") or []
    by_key = {str(p.get("person_key")): p for p in people if isinstance(p, dict)}

    ctx.check_l1(
        "dedupe_report_collapses_rows_to_people",
        body.get("row_count") == key["row_count"]
        and body.get("person_count") == key["person_count"]
        and body.get("created_count") == key["expected_created_count"]
        and body.get("skipped_count") == key["expected_skipped_count"],
        f"report says rows={body.get('row_count')} people={body.get('person_count')} "
        f"created={body.get('created_count')} skipped={body.get('skipped_count')}; "
        f"expected {key['row_count']}/{key['person_count']}/"
        f"{key['expected_created_count']}/{key['expected_skipped_count']}",
    )
    ctx.check_l1("d1_submissions_grouped_by_person", *d1_groups_exact(people, expected_people))
    ctx.check_l1("d2_group_survivor_row_chosen", *d2_survivor_exact(people, expected_people))
    ctx.check_l1(
        "d3_person_values_canonicalised",
        *d3_canonical_fields_exact(people, expected_people),
    )

    # Per person, at the report layer: the outcome, the group, and the collapsed
    # values. Three separate checks rather than one, because the three fail for
    # different reasons and a task graded on the fraction of checks passed must
    # charge for each of them.
    for want in expected_people:
        k = want["person_key"]
        got = by_key.get(k)
        who = f"{k} ({want['first_name']} {want['last_name']})"

        ctx.check_l1(
            f"person_{k}_is_{want['outcome']}",
            got is not None and got.get("outcome") == want["outcome"],
            f"{who}: reported {(got or {}).get('outcome')!r}, expected {want['outcome']!r}",
        )

        if got is None:
            group_ok, group_detail = False, f"{who}: absent from the report"
            values_ok, values_detail = False, f"{who}: absent from the report"
        else:
            members = sorted(str(m) for m in (got.get("submission_ids") or []))
            want_members = sorted(str(m) for m in want["submission_ids"])
            survivor_ok = str(got.get("survivor_submission_id")) == str(
                want["survivor_submission_id"]
            )
            group_ok = members == want_members and survivor_ok
            group_detail = (
                f"{who}: rows {members} (want {want_members}), survivor "
                f"{got.get('survivor_submission_id')!r} (want "
                f"{want['survivor_submission_id']!r})"
            )
            wrong = [
                f"{f}={got.get(f)!r} (want {want[f]!r})"
                for f in CANONICAL_FIELDS
                if str(got.get(f)) != str(want[f])
            ]
            values_ok = not wrong
            values_detail = f"{who}: " + (", ".join(wrong) or "canonical values correct")

        ctx.check_l1(f"person_{k}_group_exact", group_ok, group_detail)
        ctx.check_l1(f"person_{k}_canonical_exact", values_ok, values_detail)

    # -- the request log, before this verifier adds any traffic to it ----------
    log_after_import = ctx.vendor(VENDOR).request_log()
    n_after_import = len(log_after_import)
    connector_talked = bool(log_after_import)

    ctx.check_l1(
        "d6_one_create_per_new_person",
        *d6_write_count_exact(
            log_after_import, write_path=write_path, expected=key["expected_created_count"]
        ),
    )
    ctx.check_l1(
        "d7_existence_scan_read_whole_roster",
        *d7_scan_covered_collection(
            log_after_import, list_path=list_path, collection_size=key["roster_rows_before"]
        ),
    )

    # -- the vendor's own state ------------------------------------------------
    roster = _crawl(ctx)
    n_after_probe_1 = len(ctx.vendor(VENDOR).request_log())
    live = _live(roster)
    live_ids = {str(r.get("id")) for r in live}

    ctx.check_l1(
        "roster_holds_one_row_per_signed_up_person",
        *i4_final_cardinality(live, key["expected_live_workers_after"]),
    )
    ctx.check_l1(
        "d8_created_person_set_exact",
        *d8_created_key_set_exact(
            live, pre_existing_keys=key["live_person_keys_before"],
            expected_created_keys=created_keys,
        ),
    )
    ctx.check_l1(
        "d4_no_person_held_twice_upstream",
        *d4_one_record_per_person(live, require_keys=created_keys),
    )
    ctx.check_l1(
        "d5_created_workers_canonical_upstream",
        *d5_created_records_canonical(live, created_people),
    )

    # Per person, at the tenant's-state layer and at the report-vs-state layer.
    live_by_key: dict[str, list[dict]] = {}
    for record in live:
        live_by_key.setdefault(phone_key(str(record.get("phone") or "")), []).append(record)

    for want in expected_people:
        k = want["person_key"]
        who = f"{k} ({want['first_name']} {want['last_name']})"

        ctx.check_l1(
            f"roster_{k}_one_live_row",
            *_one_live_row(live, ids_before, want, connector_talked=connector_talked),
        )

        # The report's `worker_id` has to name a row the vendor really holds for
        # this person. A report is free to claim anything; this is the only check
        # that ties its claim to the tenant's state.
        got = by_key.get(k)
        reported = str((got or {}).get("worker_id") or "")
        if got is None:
            wid_ok, wid_detail = False, f"{who}: absent from the report"
        elif not reported:
            wid_ok, wid_detail = False, f"{who}: the report names no worker_id"
        elif reported not in live_ids:
            wid_ok, wid_detail = False, (
                f"{who}: the report names {reported}, which the tenant does not hold "
                "as a live worker"
            )
        elif want["outcome"] == "skipped" and reported != str(want["existing_worker_id"]):
            wid_ok, wid_detail = False, (
                f"{who}: the report names {reported}, but the tenant already employed "
                f"them as {want['existing_worker_id']}"
            )
        elif want["outcome"] == "created" and reported in {str(i) for i in ids_before}:
            wid_ok, wid_detail = False, (
                f"{who}: the report names {reported}, a worker the tenant held before "
                "the import — this person was a new hire"
            )
        else:
            held = {str(r.get("id")) for r in live_by_key.get(k, [])}
            wid_ok = reported in held
            wid_detail = (
                f"{who}: the report names {reported}; the tenant holds "
                f"{sorted(held) or 'nobody'} under that identity"
            )
        ctx.check_l1(f"person_{k}_worker_id_is_the_vendors", wid_ok, wid_detail)

    # Per created person, at the value layer: the row the TENANT ends up holding
    # carries the canonical values, not the report's account of them.
    for want in created_people:
        k = want["person_key"]
        who = f"{k} ({want['first_name']} {want['last_name']})"
        rows = live_by_key.get(k, [])
        if not connector_talked:
            ok, detail = False, "the connector never contacted the vendor"
        elif len(rows) != 1:
            ok, detail = False, (
                f"{who}: the tenant holds {len(rows)} live record(s) under this identity, "
                "expected exactly the one the import signed up"
            )
        else:
            wrong = [
                f"{f}={rows[0].get(f)!r} (want {want[f]!r})"
                for f in CANONICAL_FIELDS
                if str(rows[0].get(f)) != str(want[f])
            ]
            ok = not wrong
            detail = f"{who}: " + (", ".join(wrong) or "canonical upstream")
        ctx.check_l1(f"created_{k}_upstream_values", ok, detail)

    ctx.check_l1(
        "d9_added_rows_are_all_new_people",
        *_added_rows_are_all_new_people(live, ids_before, created_keys),
    )

    untouched_ok, untouched_detail = i2_preexisting_untouched(
        roster,
        key["untouched"],
        import_observed=connector_talked,
        fields=("role", "status", "rating", "email", "updated_at"),
    )
    ctx.check_l1(
        "i2_matched_workers_untouched",
        connector_talked and untouched_ok,
        untouched_detail if connector_talked else "the connector never contacted the vendor",
    )

    # A soft-deleted match is not a match: the tombstone must still be a
    # tombstone, and the person must nonetheless have been signed up afresh.
    tomb_specs = [p for p in expected_people if p.get("tombstoned_worker_id")]
    by_id = {r["id"]: r for r in roster}
    tomb_problems = []
    for spec in tomb_specs:
        tomb = by_id.get(spec["tombstoned_worker_id"])
        if tomb is None:
            tomb_problems.append(f"{spec['tombstoned_worker_id']} is gone from the roster")
        elif not tomb.get("is_deleted"):
            tomb_problems.append(f"{spec['tombstoned_worker_id']} is no longer soft-deleted")
        fresh = [r for r in live if phone_key(str(r.get("phone") or "")) == spec["person_key"]]
        if len(fresh) != 1:
            tomb_problems.append(
                f"{spec['person_key']} has {len(fresh)} live record(s), expected exactly 1 "
                "(a deleted worker who signs up again is a new hire)"
            )
    ctx.check_l1(
        "tombstoned_person_resigned_not_resurrected",
        bool(tomb_specs) and not tomb_problems,
        "; ".join(tomb_problems[:4]) or f"{len(tomb_specs)} tombstoned re-signup(s) handled",
    )

    # -- the same file again ---------------------------------------------------
    code, _out, err = ctx.app.run()
    rerun_report = _read_report(ctx)
    # The artifact is part of "completed": a process that exits 0 without
    # producing one has not run the import, and a stub submission exits 0.
    ctx.check_l1(
        "rerun_completed",
        code == 0 and isinstance(rerun_report, dict),
        f"exit={code} report={type(rerun_report).__name__} stderr={err[:400]}",
    )
    ctx.check_l1(
        "rerun_reports_every_person_already_present",
        isinstance(rerun_report, dict)
        and rerun_report.get("created_count") == 0
        and rerun_report.get("skipped_count") == key["person_count"],
        f"second run reported created={(rerun_report or {}).get('created_count')} "
        f"skipped={(rerun_report or {}).get('skipped_count')}; expected 0/{key['person_count']}",
    )

    n_after_rerun = len(ctx.vendor(VENDOR).request_log())
    again = _crawl(ctx)
    n_after_probe_2 = len(ctx.vendor(VENDOR).request_log())
    again_live = _live(again)

    # The per-person state assertion again, after the second run. Both divergences
    # survive a re-run — a duplicate is permanent and an absence is permanent —
    # so both are charged at this layer too.
    for want in expected_people:
        k = want["person_key"]
        ctx.check_l1(
            f"rerun_roster_{k}_one_live_row",
            *_one_live_row(again_live, ids_before, want, connector_talked=connector_talked),
        )

    idempotent_ok, idempotent_detail = i6_idempotent(
        roster, again, import_observed=connector_talked
    )
    ctx.check_l1(
        "rerun_signed_nobody_up_again",
        idempotent_ok and len(again_live) == key["expected_live_workers_after"],
        f"{idempotent_detail}; roster now holds {len(again_live)} live worker(s), "
        f"expected {key['expected_live_workers_after']}",
    )
    ctx.check_l1(
        "d4_no_person_held_twice_after_rerun",
        *d4_one_record_per_person(again_live, require_keys=created_keys),
    )
    ctx.check_l1(
        "d9_added_rows_are_all_new_people_after_rerun",
        *_added_rows_are_all_new_people(again_live, ids_before, created_keys),
    )
    rerun_untouched_ok, rerun_untouched_detail = i2_preexisting_untouched(
        again,
        key["untouched"],
        import_observed=connector_talked,
        fields=("role", "status", "rating", "email", "updated_at"),
    )
    ctx.check_l1(
        "i2_matched_workers_untouched_after_rerun",
        connector_talked and rerun_untouched_ok,
        rerun_untouched_detail if connector_talked
        else "the connector never contacted the vendor",
    )

    # -- conduct, once per vendor lifetime, over the connector's traffic only --
    await builtin_l2(
        ctx,
        exclude_request_indices=[
            *range(n_after_import, n_after_probe_1),
            *range(n_after_rerun, n_after_probe_2),
        ],
        app_runs=2,
    )
