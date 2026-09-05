"""task-0042 — tz_storage_normalization_audit (Rosterly, migrate).

One run of Nordhavn Care Group's rota-mirror migration, against a Rosterly
booted at CHECKPOINT=55.

This is a `migrate`. The connector in `repo/` is the WORKING nightly job that
has kept Nordhavn's Postgres mirror fed since it went in — it mints an OAuth
grant, walks `/api/workers`, `/api/shifts` and `/api/interviews`, keeps the
records that name a worker on `input/crew_roster.csv`, and rewrites one mirror
row per record in the mirror's LEGACY storage shape: the local wall clock as
Rosterly printed it plus the name of the clock it is on. That job is not
broken. The storage shape is what is being retired, because you cannot order
two text stamps written on two different clocks. The migration is to the shape
the ticket specifies — canonical UTC instant, the offset in force at that
instant, the zone, and whether Rosterly still holds the record as live.

THE DIVERGENCE (D1). Rosterly is a zero-lie vendor (`LIES.md`: `docs.lies:
[]`), and `docs/index.md` §Timestamps opens "READ THIS FIRST", states that a
shift or interview stamp is local wall-clock plus an IANA zone name that
**varies per record**, and gives three worked conversions. What is wrong is
Nordhavn's own note, `docs/nordhavn-mirror-migration-spec.md`, attributed and
dated in its third line ("Data Platform, Copenhagen. This is our own note, not
Rosterly's documentation. Written in February, when the register was pulled"):

> **The offset is a property of the venue, not of the row.** There is one
> number per clock and it is in the table above; there is no need to work one
> out per record, and no need for a timezone database in the job at all.

The register was pulled in January and every stamp on this tenant lives in
2026-05-24..2026-08-13. Five of the eight clocks Rosterly uses observe summer
time and had moved by then — America/New_York, America/Los_Angeles,
Europe/Berlin, Australia/Sydney and Pacific/Chatham are each an hour off in the
register. `tools/rework/gen_answer_key_0164.py` measures the register against
the live wire per row and refuses to write a key if the divergence thins out:
**35 of the 47 named-timezone rows in scope carry an offset the register gets
wrong**, and the other 12 — Asia/Tokyo, Asia/Kolkata, America/Sao_Paulo — are
the controls where the register happens to be right.

The controls are load-bearing. They are what stops "the register was pulled in
winter, so add an hour to everything" from scoring like the real fix: three of
the eight clocks do not observe DST at all, so a blanket correction corrupts
every row on them. The generator refuses to ship a key with fewer than two
control zones.

THE SECOND DIVERGENCE (D2), from the same note, section 3:

> Crew rows [...] are stamped by the group's own scheduling office, which is
> here, so they are on the **home clock** [`Europe/Copenhagen`, `+01:00`].

Rosterly's documentation says the opposite in as many words — worker and note
stamps are bare-naive ISO 8601 and "**These are UTC.**" — and the wire agrees.
All 40 crew rows in scope come out an hour early for anyone who believes the
note. D1 and D2 are the same shape pointed at the two different wire formats:
whichever one an engineer reasons their way out of, the other is still there.

THE THIRD DEVICE (D3, not a divergence, and documented). Section 4 of the note
asserts the mirror is current — "the wall clock in `stored_local` is the same
string Rosterly holds [...] this is an **in-place** migration". It is not. The
inventory was taken when the mirror last refreshed and the tenant has moved
since: measured at these two checkpoints, **23 of the 83 inventory rows carry a
stamp Rosterly has since replaced, 4 records exist that the inventory never
had, and 6 records Rosterly still lists are soft-deleted** — which
`docs/index.md` §Entities documents plainly ("deleted records are **not
removed** from list responses; they carry `is_deleted: true`. Always check this
flag") and which the legacy mirror has no column for at all. A migration run
in place off the inventory is stale, short and unable to retire anything.

MEASURED WORLD (live vendor, CHECKPOINT=55, inventory at CHECKPOINT=5):

    in scope            87 rows   40 workers · 29 shifts · 18 interviews
    register wrong      35 of 47 named-tz rows
    control zones       America/Sao_Paulo, Asia/Kolkata, Asia/Tokyo
    stale / adopted / retired    23 / 4 / 6

`naive.patch` is the note-faithful migration: a competent engineer who read
their own team's spec and believed it. It runs clean, walks the collections for
membership, transforms every row and writes both artifacts — it is simply wrong
about which instant 75 of the 87 rows denote, and about what the mirror already
held.

MEASURED (fresh rig probes, 2026-08-11, 110 unique checks):

    gold                     110/110  1.000
    starter (legacy job)      14/110  0.127   `migrate` -> floor 0.127 <= 0.40
    stub                       0/110  0.000   (ran; _stub_skipped absent)
    naive (spec-faithful)     25/110  0.227
    defensive                 26/110  0.236   naive plus the one guard a cautious
                                              engineer adds -- read the clock name
                                              and the wall clock off the response
                                              in hand instead of out of the
                                              inventory. It buys ONE check: the
                                              numbers still come from the register.
    second guess              38/110  0.345   "the register was pulled in winter,
                                              so put the hour back on every clock".
                                              The three zones that do not observe
                                              summer time are what this costs.
    second guess + lifecycle  81/110  0.736   the same blanket hour, but with crew
                                              stamps read as UTC and the retire /
                                              adopt lifecycle done properly. The
                                              highest basin measured, and an
                                              incoherent one: the paragraph that
                                              says crew stamps are UTC ends by
                                              naming `zoneinfo.ZoneInfo` as the
                                              parser for the other format.
    alt-correct              110/110  1.000   pages at limit=25, walks the
                                              collections in the reverse order,
                                              derives the offset from `%z` and
                                              keys adoption off record ids alone.

    discriminating 96 · omitted 0 · vacuous 0.0% · free 12.7% (all
    starter-PRESERVES: the
    legacy job really does walk all three collections and really does record the
    three offsets the register gets right) · starter/naive differing: 0
    starter-favour, 11 naive-favour. The starter writes the mirror's LEGACY
    columns, so it cannot pass a single row-level check the naive fails --
    0 starter-favour is structural to this migration's shape, not a thin naive.
    Ungrounded 0/12. Active implementations record 116 raw checks because the
    six evidence-gated conduct names are also emitted by builtin_l2; duplicate
    names fold with logical AND into the stable 110-name scorer universe.

Evidence: every row is compared field by field against an answer key measured
from a live vendor at the pinned checkpoint; the collection walk is read off
the VENDOR'S REQUEST LOG. This scenario issues no HTTP request of its own at
all, so no probe traffic can be graded as the submission's conduct, and
`builtin_l2` fires once, immediately after the single connector run. Every
check is emitted unconditionally, and the one check that credits absence first
proves the report is not empty.
"""

import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "rosterly"
ROW_COLUMNS = ["entity", "record_id", "zone", "local_wall_clock",
               "updated_utc", "utc_offset", "state"]


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower())


def _read_json(ctx, name: str):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_report(ctx) -> list[dict] | None:
    path = Path(ctx.output_dir) / "import_report.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != ROW_COLUMNS:
                return None
            return list(reader)
    except (OSError, ValueError):
        return None


def _covers_whole_collection(log: list[dict], list_path: str, total: int) -> bool:
    """Did the connector's own traffic read every record the tenant holds?

    Requirement-shaped on purpose: a submission that did nothing cannot satisfy
    it, and neither can one that read only the rows its input file already
    named.
    """
    covered: set[int] = set()
    for entry in log:
        if str(entry.get("path") or "") != list_path:
            continue
        if int(entry.get("status") or 0) != 200:
            continue
        query = entry.get("query") or {}
        try:
            offset = int(query.get("offset", 0))
            limit = int(query.get("limit", 50))
        except (TypeError, ValueError):
            continue
        covered.update(range(offset, min(offset + limit, total)))
    return len(covered) >= total


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    expected_rows = key["rows"]
    list_paths = key["list_paths"]
    totals = key["collection_totals"]

    ctx.vendor(VENDOR).recreate(checkpoint=key["checkpoint"])

    code, _out, err = ctx.app.run()
    result = _read_json(ctx, "result.json")
    report = _read_report(ctx)
    log = ctx.vendor(VENDOR).request_log()

    # builtin_l2's conduct checks are traffic-conditional. Emit the six names
    # this task arms on every probe so a submission with no requests fails them
    # instead of deleting them from the denominator. When builtin_l2 also emits
    # a name, the scorer folds the duplicate instances with logical AND.
    data_requests = [
        entry for entry in log
        if str(entry.get("path") or "").startswith("/api/")
    ]
    credential_query_keys = {
        "access_token", "token", "client_id", "client_secret", "refresh_token"
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

    token_mints = [
        entry for entry in log
        if entry.get("method") == "POST"
        and entry.get("path") == "/oauth/token"
        and int(entry.get("status") or 0) == 200
    ]
    ctx.check_l1(
        "reauth_per_request:/oauth/token",
        bool(data_requests) and len(token_mints) == 1,
        f"data_requests={len(data_requests)} successful_token_mints={len(token_mints)}",
    )

    for entity, list_path in list_paths.items():
        plural = list_path.rsplit("/", 1)[-1]
        total = int(totals.get(plural) or 0)
        pages = [
            entry for entry in log
            if entry.get("method") == "GET"
            and entry.get("path") == list_path
            and int(entry.get("status") or 0) == 200
        ]
        page_keys = Counter(
            (
                str((entry.get("query") or {}).get("offset", "0")),
                str((entry.get("query") or {}).get("limit", "50")),
            )
            for entry in pages
        )
        ctx.check_l1(
            f"no_unnecessary_full_resync:{entity}",
            bool(pages)
            and _covers_whole_collection(log, list_path, total)
            and all(count == 1 for count in page_keys.values()),
            f"{list_path}: successful_pages={len(pages)} "
            f"page_keys={dict(page_keys)} total={total}",
        )

    # Conduct, once per recreate epoch, over traffic that is entirely the
    # connector's: this scenario never calls the vendor itself.
    await builtin_l2(ctx, app_runs=1)

    body = result if isinstance(result, dict) else {}
    rows = report if isinstance(report, list) else []
    ctx.check_l1(
        "mirror_port_result_schema_exact",
        set(body) == {
            "migrated_row_count", "active_row_count", "retired_row_count",
            "adopted_row_count", "adopted", "zone_offsets",
        },
        f"result.json keys={sorted(body)}",
    )

    # A bare exit code proves nothing -- an empty submission exits 0 too.
    ctx.check_l1(
        "mirror_port_run_completed",
        code == 0 and isinstance(result, dict) and isinstance(report, list)
        and bool(report),
        f"exit={code} result={type(result).__name__} "
        f"import_report={type(report).__name__}({len(report or [])}) "
        f"stderr={err[:400]}",
    )

    ctx.check_l1(
        "mirror_port_headline_counts_exact",
        body.get("migrated_row_count") == key["migrated_row_count"]
        and body.get("active_row_count") == key["active_row_count"]
        and body.get("retired_row_count") == key["retired_row_count"]
        and body.get("adopted_row_count") == key["adopted_row_count"]
        and len(rows) == key["migrated_row_count"],
        f"reported migrated={body.get('migrated_row_count')} "
        f"active={body.get('active_row_count')} "
        f"retired={body.get('retired_row_count')} "
        f"adopted={body.get('adopted_row_count')}; the report file holds "
        f"{len(rows)} row(s); expected {key['migrated_row_count']}/"
        f"{key['active_row_count']}/{key['retired_row_count']}/"
        f"{key['adopted_row_count']}",
    )

    by_record: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        by_record.setdefault(
            (str(row.get("entity") or ""), str(row.get("record_id") or "")), []).append(row)

    # -- one migrated row per in-scope record, column by column ---------------
    for want in expected_rows:
        found = by_record.get((want["entity"], want["record_id"]), [])
        if not found:
            ok, detail = False, (
                f"{want['record_id']}: no migrated row, but Rosterly holds it "
                f"for a worker on the crew roster"
                + ("" if want["in_inventory"] else
                   " (and the mirror inventory never had it)"))
        elif len(found) > 1:
            ok, detail = False, (
                f"{want['record_id']}: {len(found)} migrated rows for one record")
        else:
            row = found[0]
            problems = []
            for column in ("zone", "local_wall_clock", "updated_utc",
                           "utc_offset", "state"):
                if str(row.get(column)) != want[column]:
                    problems.append(
                        f"{column}={row.get(column)!r} (want {want[column]!r})")
            ok = not problems
            detail = (f"{want['record_id']} on {want['zone']}: "
                      + ("; ".join(problems) or "migrated exactly"))
        ctx.check_l1(
            f"mirror_row_{want['entity']}_{want['record_id']}_exact", ok, detail)

    # -- the offset the run applied to each clock it saw ----------------------
    reported_offsets = body.get("zone_offsets")
    reported_offsets = reported_offsets if isinstance(reported_offsets, dict) else {}
    for zone, offset in key["zone_offsets"].items():
        got = reported_offsets.get(zone)
        ctx.check_l1(
            f"venue_clock_offset_{_slug(zone)}_in_force",
            str(got) == offset,
            f"{zone}: the run recorded {got!r}; the offset in force over this "
            f"tenant's stamps is {offset!r}",
        )

    # -- lifecycle: what Rosterly has retired, and what it has gained ---------
    retired_got = sorted(str(row.get("record_id"))
                         for row in rows if str(row.get("state")) == "retired")
    ctx.check_l1(
        "mirror_port_retired_set_exact",
        retired_got == key["retired"],
        f"the migration marked {len(retired_got)} row(s) retired "
        f"{retired_got[:4]}; Rosterly soft-deletes {len(key['retired'])} "
        f"in-scope record(s) {key['retired'][:4]}",
    )

    adopted_got = sorted(str(x) for x in (body.get("adopted") or []))
    ctx.check_l1(
        "mirror_port_adopted_set_exact",
        adopted_got == key["adopted"],
        f"the migration adopted {adopted_got[:4]} ({len(adopted_got)} row(s)); "
        f"Rosterly holds {len(key['adopted'])} in-scope record(s) the mirror "
        f"inventory never had: {key['adopted'][:4]}",
    )

    # -- did the run read the tenant, or only its own inventory? --------------
    in_scope_ids = {r["record_id"] for r in expected_rows}
    for entity, list_path in list_paths.items():
        plural = list_path.rsplit("/", 1)[-1]
        total = int(totals.get(plural) or 0)
        walked = _covers_whole_collection(log, list_path, total)
        by_id = {
            str(e.get("path") or "").rsplit("/", 1)[-1]
            for e in log
            if str(e.get("path") or "").startswith(list_path + "/")
            and int(e.get("status") or 0) == 200
        }
        wanted = {r["record_id"] for r in expected_rows if r["entity"] == entity}
        ctx.check_l1(
            f"{entity}_collection_read_from_rosterly_in_full",
            walked or (wanted and wanted <= by_id),
            f"{list_path}: the run {'walked' if walked else 'did not walk'} all "
            f"{total} record(s) and fetched {len(by_id & in_scope_ids)} of the "
            f"{len(wanted)} in-scope one(s) by id",
        )

    # -- nothing outside the crew's rota, gated on the report holding rows ----
    stray = sorted({str(row.get("record_id")) for row in rows} - in_scope_ids)
    ctx.check_l1(
        "mirror_report_holds_no_out_of_scope_row",
        bool(rows) and not stray,
        f"{len(stray)} row(s) belong to no worker on the crew roster: {stray[:4]}"
        if stray else ("the report is empty — nothing to judge" if not rows else
                       f"all {len(rows)} row(s) are Nordhavn's own"),
    )
