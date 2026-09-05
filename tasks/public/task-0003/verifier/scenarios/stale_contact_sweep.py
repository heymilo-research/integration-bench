"""task-0003 — last_contact_recency_over_unordered_feed (placemint, zero-lie, `build`).

One sweep of Meridian's open Placemint pipeline, then the same sweep again.

The world is the vendor's own, booted at `CHECKPOINT=56` / `DATASET_SIZE=250`:
260 placements, 2007 notes, 164 of the placements in scope (live and in an open
stage), 96 out of it. `docs/meridian-contact-sweep-runbook.md` — Delivery Ops'
own note, disclaimed in its second line as "our own note, not Placemint's
documentation. Last touched in October" — is wrong about the two things that
decide the whole answer.

DEVICE A — "The feed comes back oldest first ... the LAST row a placement
appears on is that placement's current note. Build a dict keyed on
`placement_id` and just let each row overwrite the one before it." Placemint
promises no ordering anywhere in `docs/` and the wire does not provide one: the
notes arrive in collection order and their `created_at` values are scattered
over six months. Measured by `gen_answer_key_0083.py` against the live vendor:
the last row in feed order is the most recent note for **32 of the 162**
in-scope placements that have one. Last contact is a maximum, and a maximum has
to be computed.

DEVICE B — "Pass the review horizon to `modified_since` ... anything older than
the horizon cannot change the answer. That took our Monday run from twenty-odd
pages to two." It is exactly backwards. The placements this sweep exists to find
are the ones whose every note predates the horizon, so the narrowed feed hides
them: measured, `modified_since=2026-03-20T00:00:00Z` returns 142 of 2007 notes
and leaves **85 of the 164** in-scope placements looking like placements nobody
has ever contacted. The stalled/fresh split survives the narrowing; the date the
chase-up note is required to quote does not.

DEVICE C (competence) — scope. Placemint's deletes are flag-mode
(`docs/index.md`), so 27 retired placements are still served, and
`POST /api/placements/{id}/notes` files a note against one without complaint:
nothing bounces if the sweep chases the desk's dead pipeline. 96 placements are
out of scope in total.

DEVICE D (competence) — the second run. After run 1 every chased placement's
most recent note is the sweep's own chase-up, so a sweep that got the recency
rule right files nothing on the second pass, and one that re-chases under the
same `Idempotency-Key` still has to report the placement as contacted.

MEASURED (rig probe, this scenario, 342 checks):

| variant | what it is | score |
|---|---|---|
| gold (`solution.patch`) | maximum over the whole feed | **342/342 = 1.000** |
| starter | the unimplemented skeleton | 1/342 = 0.003 |
| harness stub | empty submission | 1/342 = 0.003 |
| `naive.patch` | runbook-faithful: narrowed feed, last row wins | **163/342 = 0.477** |
| `variants/second-guess.patch` | dropped the narrowing, kept last-row-wins | 81/342 = 0.237 |
| `variants/defensive.patch` | fixed the ordering, kept the narrowing | 172/342 = 0.503 |
| `variants/alt-correct.patch` | correct by a different route | 342/342 = 1.000 |

The two devices are separable and both bite: fixing only the ordering costs 170
checks, fixing only the narrowing costs 261, and the naive that trusts the
runbook on both fails 179 — 94 `contact_*` rows and 85 `chase_note_*` bodies.
Starter and naive differ on 162 checks, all naive-favour (0 starter-favour: on a
`build` task the starter implements nothing, so there is nothing for it to be
right about that the naive is wrong about).

EVIDENCE. Every check reads the answer key or the vendor's own state crawled by
this verifier over its published port — never the connector's account of it. The
note read is narrowed with `modified_since=<notes_watermark>`, an instant the
key records (and asserts, against the crawled world) as holding zero seeded
notes, so it returns exactly what this run filed. Every "nothing was written
here" check first proves the run accept-wrote something. `builtin_l2` fires
once, after the last run, with this verifier's own request indices excluded.
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
    path = Path(ctx.output_dir) / "sweep_report.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _rows_by_id(report) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(report, dict):
        return out
    for entry in report.get("placements") or []:
        if isinstance(entry, dict):
            out.setdefault(str(entry.get("placement_id")), entry)
    return out


def _raw_rows_are_unique_objects(report, expected_count: int) -> bool:
    """The artifact must contain exactly one object for every expected row."""
    if not isinstance(report, dict) or not isinstance(report.get("placements"), list):
        return False
    raw = report["placements"]
    ids = [str(row.get("placement_id")) for row in raw if isinstance(row, dict)]
    return len(raw) == expected_count and len(ids) == expected_count and len(set(ids)) == len(ids)


def _accept_wrote(request_log) -> bool:
    """Did the submission get a write ACCEPTED? Attempts do not count."""
    return any(
        e.get("method") == "POST"
        and str(e.get("path", "")).startswith("/api/placements/")
        and str(e.get("path", "")).endswith("/notes")
        and int(e.get("status") or 0) in (200, 201)
        for e in request_log
    )


def _contact_ok(row, want):
    """The report's account of this placement's last contact and its verdict."""
    if row is None:
        return False, f"{want['placement_id']}: absent from the report"
    problems = []
    got_at = row.get("last_contact_at")
    if want["last_contact_at"] is None:
        if got_at is not None:
            problems.append(f"last_contact_at={got_at!r}, want null")
        if row.get("last_note_id") is not None:
            problems.append(f"last_note_id={row.get('last_note_id')!r}, want null")
    else:
        if str(got_at) != want["last_contact_at"]:
            problems.append(f"last_contact_at={got_at!r}, want {want['last_contact_at']!r}")
        if str(row.get("last_note_id")) not in want["last_note_ids"]:
            problems.append(
                f"last_note_id={row.get('last_note_id')!r}, want one of "
                f"{want['last_note_ids']}")
    if bool(row.get("stalled")) != want["stalled"]:
        problems.append(f"stalled={row.get('stalled')!r}, want {want['stalled']!r}")
    if str(row.get("client_id")) != str(want["client_id"]):
        problems.append(f"client_id={row.get('client_id')!r}")
    if str(row.get("stage")) != str(want["stage"]):
        problems.append(f"stage={row.get('stage')!r}")
    return not problems, (
        f"{want['placement_id']} ({want['note_count']} note(s) on file): "
        + (", ".join(problems[:3]) or "carries the contact the vendor holds"))


def _chase_ok(filed, row, want, author, *, wrote):
    """Exactly one chase-up note, from us, saying the right thing."""
    pid = want["placement_id"]
    if not wrote:
        return False, f"{pid}: the connector never got a write accepted"
    mine = [n for n in filed.get(pid, []) if str(n.get("author")) == author]
    if len(mine) != 1:
        return False, (
            f"{pid}: {len(mine)} chase-up note(s) filed by {author}, expected exactly 1")
    note = mine[0]
    problems = []
    body = str(note.get("body"))
    if body != want["chase_body"]:
        problems.append(f"body={body!r}, want {want['chase_body']!r}")
    claimed = str((row or {}).get("note_id") or "")
    if claimed != str(note.get("id")):
        problems.append(
            f"the report names note_id={claimed!r}, the vendor holds {note.get('id')!r}")
    return not problems, f"{pid}: " + (", ".join(problems) or "chased, correctly")


def _left_alone(filed, pid, author, label, *, wrote):
    if not wrote:
        return False, f"{pid}: the connector never got a write accepted"
    mine = [n for n in filed.get(pid, []) if str(n.get("author")) == author]
    return not mine, (
        f"{pid} ({label}): {len(mine)} chase-up note(s) filed against it, expected none"
        if mine else f"{pid} ({label}): left alone")


def _rerun_artifact_ok(report, rerun_by_id, rows, filed, author):
    """Validate every field of the complete post-chase artifact."""
    problems: list[str] = []
    if not _raw_rows_are_unique_objects(report, len(rows)):
        problems.append("placements is not an exact unique object list")
    if not isinstance(report, dict):
        return False, "second report is absent or malformed"
    expected_ids = {want["placement_id"] for want in rows}
    if set(rerun_by_id) != expected_ids:
        problems.append("placement-id coverage differs from the open pipeline")
    if report.get("scope_count") != len(rows):
        problems.append(f"scope_count={report.get('scope_count')!r}, want {len(rows)}")
    if report.get("stalled_count") != 0 or report.get("fresh_count") != len(rows):
        problems.append(
            f"stalled/fresh={report.get('stalled_count')!r}/{report.get('fresh_count')!r}, "
            f"want 0/{len(rows)}")

    for want in rows:
        pid = want["placement_id"]
        row = rerun_by_id.get(pid)
        if not isinstance(row, dict):
            problems.append(f"{pid}: missing row")
            continue
        if str(row.get("client_id")) != str(want["client_id"]):
            problems.append(f"{pid}: client_id drifted")
        if str(row.get("stage")) != str(want["stage"]):
            problems.append(f"{pid}: stage drifted")
        if row.get("stalled") is not False or row.get("note_id") is not None:
            problems.append(f"{pid}: not reported fresh and unwritten")
        if want["stalled"]:
            mine = [n for n in filed.get(pid, []) if str(n.get("author")) == author]
            if len(mine) != 1:
                problems.append(f"{pid}: cannot identify the first-run chase note")
            else:
                note = mine[0]
                if (str(row.get("last_note_id")) != str(note.get("id"))
                        or str(row.get("last_contact_at")) != str(note.get("created_at"))):
                    problems.append(f"{pid}: rerun does not name the chase note as latest")
        else:
            ok, _detail = _contact_ok(row, want)
            if not ok:
                problems.append(f"{pid}: original fresh contact drifted")
        if len(problems) >= 8:
            break
    return not problems, "; ".join(problems) if problems else "all rerun rows match vendor state"


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    rows = key["rows"]
    stalled = [r for r in rows if r["stalled"]]
    fresh = [r for r in rows if not r["stalled"]]
    author = key["chase_author"]
    out_of_scope = key["out_of_scope"]

    code, _out, err = ctx.app.run()
    report = _read_report(ctx)
    ctx.check_l1(
        "contact_sweep_run_completed",
        code == 0 and isinstance(report, dict),
        f"exit={code} report={type(report).__name__} stderr={err[:400]}",
    )

    body = report if isinstance(report, dict) else {}
    by_id = _rows_by_id(body)

    ctx.check_l1(
        "sweep_report_covers_the_open_pipeline_exactly",
        body.get("scope_count") == key["counts"]["scope"]
        and body.get("stalled_count") == key["counts"]["stalled"]
        and body.get("fresh_count") == key["counts"]["fresh"]
        and _raw_rows_are_unique_objects(body, key["counts"]["scope"])
        and set(by_id) == {r["placement_id"] for r in rows},
        "report says scope={} stalled={} fresh={} over {} distinct placement(s); the "
        "tenant holds {} in scope, {} of them stalled".format(
            body.get("scope_count"), body.get("stalled_count"), body.get("fresh_count"),
            len(by_id), key["counts"]["scope"], key["counts"]["stalled"]),
    )

    missing_scope = sorted(p["placement_id"] for p in out_of_scope if p["placement_id"] in by_id)
    ctx.check_l1(
        "sweep_report_omits_every_closed_and_retired_placement",
        len(by_id) > 0 and not missing_scope,
        f"{len(missing_scope)} out-of-scope placement(s) in the report, e.g. "
        f"{missing_scope[:5]}" if by_id else "the report lists no placements at all",
    )

    # One check per in-scope placement, emitted unconditionally: a run that
    # produced no artifact fails each of these rather than shrinking the
    # denominator.
    for want in rows:
        ctx.check_l1(
            f"contact_{want['placement_id']}_is_the_latest_note_on_file",
            *_contact_ok(by_id.get(want["placement_id"]), want),
        )

    log_after_run = ctx.vendor(VENDOR).request_log()
    n_after_run = len(log_after_run)
    wrote = _accept_wrote(log_after_run)

    placements, filed = _read_world(ctx, key)
    n_after_probe_1 = len(ctx.vendor(VENDOR).request_log())

    for want in stalled:
        ctx.check_l1(
            f"chase_note_on_{want['placement_id']}_is_the_vendors",
            *_chase_ok(filed, by_id.get(want["placement_id"]), want, author, wrote=wrote),
        )
    for want in fresh:
        ctx.check_l1(
            f"fresh_{want['placement_id']}_was_left_uncontacted",
            *_left_alone(filed, want["placement_id"], author, "contacted inside the horizon",
                         wrote=wrote),
        )

    outside = sorted(
        pid for pid in (p["placement_id"] for p in out_of_scope)
        if any(str(n.get("author")) == author for n in filed.get(pid, []))
    )
    ctx.check_l1(
        "nothing_was_filed_outside_the_open_pipeline",
        wrote and not outside,
        f"{len(outside)} closed or retired placement(s) were chased, e.g. {outside[:5]}"
        if wrote else "the connector never got a write accepted",
    )

    total_filed = sum(
        1 for group in filed.values() for n in group if str(n.get("author")) == author)
    ctx.check_l1(
        "one_chase_note_per_stalled_placement_and_no_more",
        wrote and total_filed == len(stalled),
        f"the tenant holds {total_filed} chase-up note(s) from {author}, expected "
        f"{len(stalled)}" if wrote else "the connector never got a write accepted",
    )

    # -- the same sweep again --------------------------------------------------
    code, _out, err = ctx.app.run()
    rerun = _read_report(ctx)
    ctx.check_l1(
        "rerun_completed",
        code == 0 and isinstance(rerun, dict),
        f"exit={code} report={type(rerun).__name__} stderr={err[:400]}",
    )
    rerun_by_id = _rows_by_id(rerun)

    n_after_rerun = len(ctx.vendor(VENDOR).request_log())
    _placements_again, filed_again = _read_world(ctx, key)
    n_after_probe_2 = len(ctx.vendor(VENDOR).request_log())

    total_again = sum(
        1 for group in filed_again.values() for n in group if str(n.get("author")) == author)
    ctx.check_l1(
        "rerun_filed_no_further_chase_notes",
        wrote and total_again == total_filed == len(stalled),
        f"the tenant holds {total_again} chase-up note(s) after the second run "
        f"(had {total_filed}, expected {len(stalled)})"
        if wrote else "the connector never got a write accepted",
    )

    # After run 1 the chase-up note IS the placement's most recent contact, so a
    # sweep that reads recency correctly reports every chased placement as fresh
    # on the second pass and files nothing for it.
    still_stalled = sorted(
        r["placement_id"] for r in stalled
        if bool((rerun_by_id.get(r["placement_id"]) or {}).get("stalled"))
        or (rerun_by_id.get(r["placement_id"]) or {}).get("note_id") is not None
    )
    ctx.check_l1(
        "rerun_reports_every_chased_placement_as_contacted",
        wrote and bool(rerun_by_id) and not still_stalled,
        f"{len(still_stalled)} placement(s) chased on the first run are still reported "
        f"stalled or re-chased on the second, e.g. {still_stalled[:5]}"
        if wrote and rerun_by_id else "the second run produced no usable report",
    )
    rerun_touched = sorted(
        r["placement_id"] for r in fresh
        if any(str(n.get("author")) == author for n in filed_again.get(r["placement_id"], []))
    )
    ctx.check_l1(
        "rerun_left_the_contacted_cohort_alone",
        wrote and not rerun_touched,
        f"{len(rerun_touched)} placement(s) contacted inside the horizon were chased by "
        f"the second run, e.g. {rerun_touched[:5]}"
        if wrote else "the connector never got a write accepted",
    )
    ctx.check_l1(
        "rerun_full_report_matches_the_post_chase_vendor_state",
        *_rerun_artifact_ok(rerun, rerun_by_id, rows, filed_again, author),
    )

    await builtin_l2(
        ctx,
        exclude_request_indices=[
            *range(n_after_run, n_after_probe_1),
            *range(n_after_rerun, n_after_probe_2),
        ],
        app_runs=2,
    )
