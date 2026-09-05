"""task-0030 — bidirectional_orphan_detection (placemint, zero-lie vendor, `build`).

One census of Meridian's desk board against the Placemint placement book, then
the same census again.

The world is the vendor's own, booted at `CHECKPOINT=56` / `DATASET_SIZE=120`:
130 placements, 18 of them soft-deleted, and `input/desk_board.csv`, 133 rows of
what the desk thinks it is working, covering 65 of the 112 live placements. Both
directions are graded — every board row has to be resolved, and every placement
Placemint still holds has to be accounted for by some row.
`docs/meridian-desk-board-runbook.md` — Delivery Ops' own note, disclaimed in its
second line as "our own note, not Placemint's documentation. Last revised in
September" — is wrong about four things, and the first two move records in BOTH
directions at once.

DEVICE A — "`placemint_ref` is the Placemint id, exactly as Placemint issues it
... Ravi backfilled all of [the old spreadsheet rows] to the full id, so no row
on the board carries the short form any more." Measured by
`gen_answer_key_0095.py` against the live vendor: 61 of the 133 rows carry the
console number instead (`73` for `plc_00073`). A connector that looks the ref up
verbatim gets 51 of those rows' verdicts wrong AND — because the same 51
engagements are then nobody's — flags 44 placements as unclaimed and files a
census note on each, including 4 the desk closed out. One divergence, both
directions, 95 checks.

DEVICE B — "the ref points at the engagement the row is about ... the
`candidate_name` and `client_id` columns are there so a human can read the board
... If the ref resolves, the row is that placement." 30 rows carry a ref for the
right ACCOUNT and the wrong engagement — what the first version of the board
sync, which matched on the client, left behind. Delivery Ops' matching rule
(PROBLEM.md) is `candidate_name` AND `client_id`, so those rows are `unmatched`,
and the 30 placements they point at are still nobody's, so they belong on the
census with a note. 20 of the 30 carry the full id, so a verbatim reader pays
for this device too (60 checks); a connector that reconstructs the console form
without checking who the row is about pays for all 30 (90 checks), which is the
exact opposite error to device A's. Neither side of the pair can sit still:
`variants/second-guess.patch` (reconstructs, never verifies) passes 30 checks
`naive.patch` fails, and fails 91 that `naive.patch` passes.

DEVICE C (competence) — "Placemint takes a placement out of the book when the
desk closes it out ... a row whose ref Placemint no longer answers for is one
that has been closed upstream." Placemint's deletes are flag-mode
(`docs/index.md`, `docs/entities.md`, three separate places), so all 18 retired
placements are still served with `is_deleted: true`. 14 of them are on the
board, where the verdict is `retired` and not `matched`; the other 4 are not,
and a connector that reads the book without the flag calls them unclaimed and
files a note on a dead placement — which Placemint accepts, by design
(`docs/writeback.md` § Missing parents), so nothing bounces.

DEVICE D (competence) — the board holds 14 second rows for an engagement another
row already covers (two desks on one account). The runbook says the board tool
will not allow it and that the file can be loaded into a dict keyed on
`placemint_ref`; doing that silently drops 14 rows out of the census
(`variants/board-keyed.patch`, 142/322 = 0.441).

MEASURED (rig probe, this scenario, 322 checks):

| variant | what it is | score |
|---|---|---|
| gold (`solution.patch`) | reconstruct, verify, then read the flag | **322/322 = 1.000** |
| starter | the unimplemented skeleton | 1/322 = 0.003 |
| harness stub | empty submission | 1/322 = 0.003 |
| `naive.patch` | runbook-faithful: verbatim ref, no verification, no flag | **149/322 = 0.463** |
| `variants/second-guess.patch` | reconstructs the console form, still trusts it | 210/322 = 0.652 |
| `variants/defensive.patch` | naive + `is_deleted == false` on the way in | 157/322 = 0.488 |
| `variants/board-keyed.patch` | naive + the runbook's ref-keyed board dict | 142/322 = 0.441 |
| `variants/alt-correct.patch` | never reads the ref; resolves on the engagement | 322/322 = 1.000 |

Starter and naive differ on 148 checks, all naive-favour (0 starter-favour: on a
`build` task the starter implements nothing, so there is nothing for it to be
right about that the naive is wrong about). The naive's 173 failures are 81 board
rows, 48 placements it flagged that the board already accounts for, 40 unclaimed
checks over the 20 placements a full-form conflict row hid from it, and 4
aggregates.

EVIDENCE. Every check reads the answer key or the vendor's own state crawled by
this verifier over its published port — never the connector's account of it. The
note read is narrowed with `modified_since=<notes_watermark>`, an instant the key
records (and asserts, against the crawled world) as holding zero seeded notes, so
it returns exactly what this run filed. Every "nothing was filed here" check
first proves the run got a write ACCEPTED — a refused attempt is not evidence of
restraint. `builtin_l2` fires once, after the last run, with this verifier's own
request indices excluded.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "placemint"
PAGE = 100


def _token(ctx) -> str:
    base = ctx.vendor(VENDOR).base_url
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": ctx.secrets.get("PM_CLIENT_ID", ""),
        "client_secret": ctx.secrets.get("PM_CLIENT_SECRET", ""),
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/oauth/token", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


def _crawl(ctx, path: str, tok: str, params: dict | None = None) -> list[dict]:
    base = ctx.vendor(VENDOR).base_url
    out: list[dict] = []
    offset = 0
    while True:
        query = dict(params or {})
        query.update({"offset": offset, "limit": PAGE})
        req = urllib.request.Request(
            f"{base}{path}?{urllib.parse.urlencode(query)}",
            headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            envelope = json.load(resp)
        out.extend(envelope.get("data") or [])
        offset += int(envelope.get("limit") or PAGE)
        if offset >= int(envelope.get("total") or 0):
            return out


def _read_world(ctx, key):
    """Placements by id, and the notes THIS RUN filed grouped by placement."""
    tok = _token(ctx)
    placements = {str(p["id"]): p for p in _crawl(ctx, "/api/placements", tok)}
    filed: dict[str, list[dict]] = {}
    for note in _crawl(ctx, "/api/notes", tok, {"modified_since": key["notes_watermark"]}):
        filed.setdefault(str(note.get("placement_id")), []).append(note)
    return placements, filed


def _read_report(ctx):
    path = Path(ctx.output_dir) / "census_report.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _rows_by_ref(report) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not isinstance(report, dict):
        return out
    for entry in report.get("rows") or []:
        if isinstance(entry, dict):
            out.setdefault(str(entry.get("board_ref")), []).append(entry)
    return out


def _unclaimed_by_id(report) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(report, dict):
        return out
    for entry in report.get("unclaimed") or []:
        if isinstance(entry, dict):
            out.setdefault(str(entry.get("placement_id")), entry)
    return out


def _accept_wrote(request_log) -> bool:
    """Did the submission get a write ACCEPTED? Attempts do not count."""
    return any(
        e.get("method") == "POST"
        and str(e.get("path", "")).startswith("/api/placements/")
        and str(e.get("path", "")).endswith("/notes")
        and int(e.get("status") or 0) in (200, 201)
        for e in request_log
    )


def _artifact_matches(report, key) -> bool:
    if not isinstance(report, dict) or set(report) != {
        "board_row_count", "matched_count", "retired_count", "unmatched_count",
        "unclaimed_count", "rows", "unclaimed",
    }:
        return False
    expected_rows = [
        {field: row[field] for field in ("board_ref", "verdict", "placement_id", "source_line")}
        for row in key["rows"]
    ]
    got_unclaimed = report.get("unclaimed")
    if not isinstance(got_unclaimed, list):
        return False
    expected_unclaimed = {
        row["placement_id"]: {
            "placement_id": row["placement_id"], "client_id": row["client_id"],
            "stage": row["stage"],
        }
        for row in key["unclaimed"]
    }
    if any(not isinstance(row, dict) or set(row) != {
        "placement_id", "client_id", "stage", "note_id"
    } for row in got_unclaimed):
        return False
    projected = [{k: v for k, v in row.items() if k != "note_id"} for row in got_unclaimed]
    return (
        report.get("rows") == expected_rows
        and len(projected) == len(expected_unclaimed)
        and all(isinstance(row.get("note_id"), str) and row.get("note_id") for row in got_unclaimed)
        and {row["placement_id"]: row for row in projected} == expected_unclaimed
        and report["board_row_count"] == key["board_row_count"]
        and report["matched_count"] == key["verdict_counts"]["matched"]
        and report["retired_count"] == key["verdict_counts"]["retired"]
        and report["unmatched_count"] == key["verdict_counts"]["unmatched"]
        and report["unclaimed_count"] == key["unclaimed_count"]
    )


def _note_posts_match(log, key) -> bool:
    posts = [e for e in log if e.get("method") == "POST"
             and str(e.get("path", "")).startswith("/api/placements/")
             and str(e.get("path", "")).endswith("/notes")]
    got = []
    for entry in posts:
        parts = str(entry.get("path")).strip("/").split("/")
        got.append({
            "placement_id": parts[2] if len(parts) == 4 else None,
            "body": entry.get("body"),
            "idempotency_key": entry.get("idempotency_key")
            or (entry.get("headers") or {}).get("idempotency-key")
            or (entry.get("headers") or {}).get("Idempotency-Key"),
            "status": entry.get("status"),
        })
    want = [{
        "placement_id": row["placement_id"],
        "body": {"body": row["note_body"], "author": key["note_author"]},
        "idempotency_key": f"census:{row['placement_id']}",
        "status": 201,
    } for row in key["unclaimed"]]
    return sorted(got, key=lambda row: str(row["placement_id"])) == sorted(
        want, key=lambda row: row["placement_id"])


def _row_ok(got, want):
    """The census's verdict for one board row, and the engagement it named."""
    ref = want["board_ref"]
    if len(got) != 1:
        return False, (f"{ref} ({want['group']}): {len(got)} entr(ies) in the census, "
                       "expected exactly 1")
    entry = got[0]
    problems = []
    if str(entry.get("verdict")) != want["verdict"]:
        problems.append(f"verdict={entry.get('verdict')!r}, want {want['verdict']!r}")
    claimed = entry.get("placement_id")
    if want["placement_id"] is None:
        if claimed is not None:
            problems.append(f"placement_id={claimed!r}, want null")
    elif str(claimed) != want["placement_id"]:
        problems.append(f"placement_id={claimed!r}, want {want['placement_id']!r}")
    return not problems, f"{ref} ({want['group']}, ref {want['placemint_ref']!r}): " + (
        ", ".join(problems) or "carries the verdict the board earns")


def _unclaimed_listed_ok(placements, entry, want):
    """The census lists this placement, with the account Placemint holds."""
    pid = want["placement_id"]
    if entry is None:
        return False, f"{pid}: absent from the census's unclaimed list"
    problems = []
    if str(entry.get("client_id")) != want["client_id"]:
        problems.append(f"client_id={entry.get('client_id')!r}, want {want['client_id']!r}")
    if str(entry.get("stage")) != want["stage"]:
        problems.append(f"stage={entry.get('stage')!r}, want {want['stage']!r}")
    live = placements.get(pid)
    if live is None or live.get("is_deleted"):
        problems.append("the vendor no longer serves this placement live")
    return not problems, f"{pid}: " + (
        ", ".join(problems) or "listed with the account Placemint holds")


def _unclaimed_note_ok(filed, entry, want, author, *, wrote):
    """Exactly one census note, from us, saying the right thing."""
    pid = want["placement_id"]
    if not wrote:
        return False, f"{pid}: the connector never got a write accepted"
    mine = [n for n in filed.get(pid, []) if str(n.get("author")) == author]
    if len(mine) != 1:
        return False, (
            f"{pid}: {len(mine)} census note(s) filed by {author}, expected exactly 1")
    note = mine[0]
    problems = []
    body = str(note.get("body"))
    if body != want["note_body"]:
        problems.append(f"body={body!r}, want {want['note_body']!r}")
    claimed = str((entry or {}).get("note_id") or "")
    if claimed != str(note.get("id")):
        problems.append(
            f"the census names note_id={claimed!r}, the vendor holds {note.get('id')!r}")
    return not problems, f"{pid}: " + (", ".join(problems) or "flagged, correctly")


def _covered_ok(unclaimed_by_id, filed, want, author, *, wrote):
    """A placement the board accounts for carries nothing this run put there."""
    pid = want["placement_id"]
    if not wrote:
        return False, f"{pid}: the connector never got a write accepted"
    problems = []
    if pid in unclaimed_by_id:
        problems.append("the census calls it unclaimed")
    mine = [n for n in filed.get(pid, []) if str(n.get("author")) == author]
    if mine:
        problems.append(f"{len(mine)} census note(s) filed against it")
    label = "retired" if want["is_deleted"] else "on the board"
    return not problems, f"{pid} ({label}): " + (
        ", ".join(problems) or "left off the census, as the board accounts for it")


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    rows = key["rows"]
    unclaimed_want = key["unclaimed"]
    covered_want = key["covered"]
    author = key["note_author"]

    code, _out, err = ctx.app.run()
    report = _read_report(ctx)
    ctx.check_l1(
        "board_census_run_completed",
        code == 0 and isinstance(report, dict),
        f"exit={code} report={type(report).__name__} stderr={err[:400]}",
    )

    body = report if isinstance(report, dict) else {}
    by_ref = _rows_by_ref(body)
    unclaimed_by_id = _unclaimed_by_id(body)

    ctx.check_l1(
        "census_accounts_for_both_directions_exactly",
        body.get("board_row_count") == key["board_row_count"]
        and body.get("matched_count") == key["verdict_counts"]["matched"]
        and body.get("retired_count") == key["verdict_counts"]["retired"]
        and body.get("unmatched_count") == key["verdict_counts"]["unmatched"]
        and body.get("unclaimed_count") == key["unclaimed_count"]
        and set(by_ref) == {r["board_ref"] for r in rows},
        "census says board_row_count={} matched={} retired={} unmatched={} unclaimed={} "
        "over {} distinct board ref(s); the board holds {} row(s), {} of them matched, "
        "and the tenant holds {} unclaimed placement(s)".format(
            body.get("board_row_count"), body.get("matched_count"),
            body.get("retired_count"), body.get("unmatched_count"),
            body.get("unclaimed_count"), len(by_ref), key["board_row_count"],
            key["verdict_counts"]["matched"], key["unclaimed_count"]),
    )
    ctx.check_l1(
        "first_census_artifact_matches_the_complete_board_and_book",
        _artifact_matches(report, key),
        "the complete artifact must preserve board order/source lines and list every "
        "unclaimed placement with exactly the published schema",
    )

    # One check per board row, emitted unconditionally: a run that produced no
    # artifact fails each of these rather than shrinking the denominator.
    for want in rows:
        ctx.check_l1(
            f"board_row_{want['board_ref']}_{want['verdict']}",
            *_row_ok(by_ref.get(want["board_ref"]) or [], want),
        )

    log_after_run = ctx.vendor(VENDOR).request_log()
    n_after_run = len(log_after_run)
    wrote = _accept_wrote(log_after_run)
    ctx.check_l1(
        "first_census_note_requests_match_unclaimed_placements_exactly",
        _note_posts_match(log_after_run, key),
        "one exact note body and stable census:<placement_id> key is required per unclaimed placement",
    )

    placements, filed = _read_world(ctx, key)
    n_after_probe_1 = len(ctx.vendor(VENDOR).request_log())

    for want in unclaimed_want:
        pid = want["placement_id"]
        entry = unclaimed_by_id.get(pid)
        ctx.check_l1(
            f"unclaimed_{pid}_is_on_the_census",
            *_unclaimed_listed_ok(placements, entry, want),
        )
        ctx.check_l1(
            f"unclaimed_{pid}_carries_the_census_note",
            *_unclaimed_note_ok(filed, entry, want, author, wrote=wrote),
        )

    for want in covered_want:
        ctx.check_l1(
            f"covered_{want['placement_id']}_was_left_off_the_census",
            *_covered_ok(unclaimed_by_id, filed, want, author, wrote=wrote),
        )

    total_filed = sum(
        1 for group in filed.values() for n in group if str(n.get("author")) == author)
    ctx.check_l1(
        "one_census_note_per_unclaimed_placement_and_no_more",
        wrote and total_filed == len(unclaimed_want),
        f"the tenant holds {total_filed} census note(s) from {author}, expected "
        f"{len(unclaimed_want)}" if wrote else "the connector never got a write accepted",
    )

    # -- the same census again -------------------------------------------------
    code, _out, err = ctx.app.run()
    rerun = _read_report(ctx)
    ctx.check_l1(
        "rerun_completed",
        code == 0 and isinstance(rerun, dict),
        f"exit={code} report={type(rerun).__name__} stderr={err[:400]}",
    )
    rerun_refs = sorted(_rows_by_ref(rerun))
    rerun_unclaimed = sorted(_unclaimed_by_id(rerun))
    ctx.check_l1(
        "rerun_reaches_the_same_census",
        rerun_refs == sorted(r["board_ref"] for r in rows)
        and rerun_unclaimed == sorted(w["placement_id"] for w in unclaimed_want),
        f"the second run resolved {len(rerun_refs)} board row(s) and called "
        f"{len(rerun_unclaimed)} placement(s) unclaimed; the board holds "
        f"{key['board_row_count']} row(s) and {key['unclaimed_count']} are unclaimed",
    )
    ctx.check_l1(
        "rerun_artifact_matches_the_complete_board_and_book",
        _artifact_matches(rerun, key),
        "the second complete artifact must retain every first-run contract value",
    )

    n_after_rerun = len(ctx.vendor(VENDOR).request_log())
    placements_again, filed_again = _read_world(ctx, key)
    n_after_probe_2 = len(ctx.vendor(VENDOR).request_log())

    total_again = sum(
        1 for group in filed_again.values() for n in group if str(n.get("author")) == author)
    ctx.check_l1(
        "rerun_filed_no_further_census_notes",
        wrote and total_again == total_filed == len(unclaimed_want),
        f"the tenant holds {total_again} census note(s) after the second run "
        f"(had {total_filed}, expected {len(unclaimed_want)})"
        if wrote else "the connector never got a write accepted",
    )
    drifted = sorted(pid for pid in placements if placements_again.get(pid) != placements[pid])
    ctx.check_l1(
        "rerun_left_every_placement_exactly_as_it_was",
        wrote and not drifted,
        f"{len(drifted)} placement(s) changed on the second run, e.g. {drifted[:5]}"
        if wrote else "the connector never got a write accepted",
    )

    await builtin_l2(
        ctx,
        exclude_request_indices=[
            *range(n_after_run, n_after_probe_1),
            *range(n_after_rerun, n_after_probe_2),
        ],
        app_runs=2,
    )
