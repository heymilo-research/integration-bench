"""task-0036 — stale_account_snapshot_authority (placemint, zero-lie vendor, `harden`).

One nightly book extract, then the same extract again.

PREMISE REPLACED — see tools/rework/specs/task-0036.yaml for the reasoning. The
scaffold declared `refresh_scheduling_under_pressure` (proactive vs reactive
token minting under an injected 5xx burst) and armed
`FAULT_REFRESH_SCHEDULING_UNDER_PRESSURE`, a knob no placemint source file
reads. The mechanic is not gradable on this vendor: a mint-count ceiling is
pace-sensitive (the vendor's own `cfg.py` records the measurement — on task-0047
the mint count was pinned by a single rate-limit stall, and "under contention the
crawl spreads past 60s, the limiter never fires ... and `reauth_across_token_
expiry` fails on a correct connector"), and `FAULT_TOKEN_EXPIRY_MIDRUN` gives the
first token a 5-second life, which a five-page crawl finishes inside — an inert
device at this task's configuration.

WHAT THE SHIPPED CONNECTOR DOES. It crawls the placement book nightly and
resolves every row's account against `input/account_book.json`, the copy the
weekly account sync leaves behind, then marks a row billable when that copy still
holds the account. It works: it exits 0, writes all 409 rows, and gets the
placement's own fields right on every one of them.

The world is the vendor's own, booted at `CHECKPOINT=26` / `DATASET_SIZE=400`:
410 placements (409 live, 1 retired), 301 accounts, of which 154 carry
`status: inactive` and exactly ONE is soft-deleted.
`docs/meridian-account-book-note.md` — Revenue Ops' own note, disclaimed in its
second line as "our own note, not Placemint's documentation ... last touched in
February" — is why, and it is wrong about both halves of the account question.

DEVICE A (which SOURCE) — "we open or close maybe two [accounts] a quarter ... a
week-old copy of a list that changes twice a quarter is never more than a row or
two out of date." Measured by `gen_answer_key_0138.py` against the live vendor:
the snapshot disagrees with Placemint about 115 of the 301 accounts, and those
accounts carry **225 of the 409 rows** — 61 paused-since-reopened, 62
reopened-since-paused, 60 renamed, and 42 under an account the snapshot has
never seen.

DEVICE B (which FIELD) — "Placemint drops an account's record when the account
stops trading — that is what the delete flag on the account is for ... The
account record also carries a `status` column. Ignore it." On the wire ONE
account of 301 is deleted and 154 are `inactive`, an enum `docs/entities.md`
documents in full. A connector that pulls the accounts live and keeps the note's
rule still gets **206 of 409 rows** wrong.

THE MATCHED PAIR. The two devices are independent and they cross. `paused` is a
class of accounts Placemint holds ACTIVE whose week-old snapshot row says
inactive: the SHIPPED connector is right about those 61 rows precisely because it
never reads `status`, and the obvious fix — keep the snapshot, start honouring
`status` — gets them wrong. `naive.patch` is that fix. Measured: 61 rows the
starter passes and the naive fails, 89 the naive passes and the starter fails.
Neither source-plus-field pairing but the right one can hold both ends.

MEASURED (rig probe, this scenario, 420 checks):

| variant | what it is | score |
|---|---|---|
| gold (`solution.patch`) | live account crawl, `status` decides | **420/420 = 1.000** |
| starter | the shipped connector: weekly snapshot, delete flag decides | 163/420 = 0.388 |
| harness stub | empty submission | 1/420 = 0.002 |
| `naive.patch` | the obvious fix: snapshot kept, `status` honoured | **191/420 = 0.455** |
| `variants/second-guess.patch` | accounts pulled live, delete flag kept | 212/420 = 0.505 |
| `variants/defensive.patch` | naive + the unseen accounts looked up by id | 233/420 = 0.555 |
| `variants/alt-correct.patch` | correct, account-major, by a different route | **420/420 = 1.000** |

`harden` is headroom-only: floor 0.388 · headroom 0.612 · discriminating 257 ·
vac 0/417 (the 161 free passes are all STARTER-PRESERVED, which is what a
working-but-unsafe connector means; the stub passes exactly one harness-owned
check). The starter is a working connector — it exits 0, writes all 409 rows and
gets 163 checks — so the `harden` label matches the shape.

EVIDENCE. Every row check reads the vendor's own placement and account state,
crawled by this verifier over its published port at grading time — never the
connector's account of it, and never the shipped snapshot. The answer key
supplies the row list and each row's snapshot class so a failure can be attributed
to a device. `account_book_was_read_from_placemint` is requirement-shaped ("did
do X", not "did not do X") and reads ONLY the slice of the request log the app
produced, taken before this verifier issues a single request of its own — the
verifier's own client crawl would otherwise hand that check to the empty
submission (the task-0182 defect). `builtin_l2` fires once, after the last run,
with this verifier's own request indices excluded.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "placemint"
PAGE = 100
FEE_TOLERANCE = 0.005
REPORT_FIELDS = {
    "placement_count", "billable_count", "on_hold_count",
    "fee_total_billable", "placements",
}
ROW_FIELDS = {
    "placement_id", "client_id", "client_name", "client_industry",
    "candidate_name", "role_title", "stage", "fee_amount", "billable",
}


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


def _crawl(ctx, path: str, tok: str) -> list[dict]:
    base = ctx.vendor(VENDOR).base_url
    out: list[dict] = []
    offset = 0
    while True:
        req = urllib.request.Request(
            f"{base}{path}?{urllib.parse.urlencode({'offset': offset, 'limit': PAGE})}",
            headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            envelope = json.load(resp)
        out.extend(envelope.get("data") or [])
        offset += int(envelope.get("limit") or PAGE)
        if offset >= int(envelope.get("total") or 0):
            return out


def _read_world(ctx):
    """The book and the account list, as Placemint holds them right now."""
    tok = _token(ctx)
    placements = {str(p["id"]): p for p in _crawl(ctx, "/api/placements", tok)}
    accounts = {str(c["id"]): c for c in _crawl(ctx, "/api/clients", tok)}
    return placements, accounts


def _read_extract(ctx):
    path = Path(ctx.output_dir) / "book_extract.json"
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


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _close(a, b) -> bool:
    fa, fb = _num(a), _num(b)
    if fa is None and fb is None:
        return True
    return fa is not None and fb is not None and abs(fa - fb) < FEE_TOLERANCE


def _truth(placement, account) -> dict:
    """What the extract has to say about one placement, from vendor state."""
    return {
        "client_id": str(placement.get("client_id")),
        "client_name": str(account.get("name")),
        "client_industry": str(account.get("industry")),
        "candidate_name": str(placement.get("candidate_name")),
        "role_title": str(placement.get("role_title")),
        "stage": str(placement.get("stage")),
        "fee_amount": placement.get("fee_amount"),
        "billable": (not account.get("is_deleted")) and str(account.get("status")) == "active",
    }


def _row_ok(entry, placements, accounts, want):
    placement_id = want["placement_id"]
    placement = placements.get(placement_id)
    if placement is None:
        return False, f"{placement_id}: the vendor no longer serves this placement"
    account = accounts.get(str(placement.get("client_id")))
    if account is None:
        return False, f"{placement_id}: the vendor serves no account {placement.get('client_id')}"
    truth = _truth(placement, account)
    if entry is None:
        return False, f"{placement_id} ({want['snapshot_class']}): absent from the extract"
    problems = []
    if set(entry) != ROW_FIELDS:
        problems.append(f"fields={sorted(entry)}, want exactly {sorted(ROW_FIELDS)}")
    if entry.get("placement_id") != placement_id:
        problems.append(f"placement_id={entry.get('placement_id')!r}, want {placement_id!r}")
    for field in ("client_id", "client_name", "client_industry", "candidate_name",
                  "role_title", "stage"):
        if entry.get(field) != truth[field]:
            problems.append(f"{field}={entry.get(field)!r}, Placemint holds {truth[field]!r}")
    if entry.get("fee_amount") != truth["fee_amount"]:
        problems.append(f"fee_amount={entry.get('fee_amount')!r}, want {truth['fee_amount']!r}")
    if entry.get("billable") is not truth["billable"]:
        problems.append(
            f"billable={entry.get('billable')!r}, the account Placemint holds is "
            f"{str(account.get('status'))!r}"
            + (" and deleted" if account.get("is_deleted") else ""))
    detail = (f"{placement_id} ({want['snapshot_class']} account "
              f"{placement.get('client_id')}): "
              + (", ".join(problems[:3]) or "matches the account Placemint holds"))
    return not problems, detail


def _account_book_read(app_log, account_total: int, accounts_needed: int):
    """Requirement-shaped: did the run get the account book from the VENDOR?

    Satisfied by a list crawl that reaches the end of the collection or by
    per-id lookups covering all but a handful of the accounts the book needs.
    Reads only the slice of the request log the app produced.
    """
    windows = set()
    covered = set()
    by_id = set()
    for entry in app_log:
        if entry.get("method") != "GET":
            continue
        path = str(entry.get("path", ""))
        if path == "/api/clients":
            query = entry.get("query") or {}
            try:
                offset = int(query.get("offset", 0) or 0)
                limit = int(query.get("limit", PAGE) or PAGE)
            except (TypeError, ValueError):
                offset, limit = -1, -1
            if (int(entry.get("status") or 0) == 200 and offset >= 0
                    and 0 < limit <= PAGE):
                windows.add((offset, limit))
                covered.update(range(offset, min(offset + limit, account_total)))
        elif path.startswith("/api/clients/") and int(entry.get("status") or 0) == 200:
            by_id.add(path.rsplit("/", 1)[-1])
    complete_list = len(covered) == account_total
    ok = complete_list or len(by_id) >= accounts_needed
    return ok, (
        f"the run read successful account-list windows {sorted(windows)}, covering "
        f"{len(covered)}/{account_total} account positions; it also "
        f"made {len(by_id)} successful account lookup(s), while the book it wrote needs "
        f"{accounts_needed} distinct account(s)")


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    rows = key["rows"]

    code, _out, err = ctx.app.run()
    report = _read_extract(ctx)
    ctx.check_l1(
        "book_extract_run_completed",
        code == 0 and isinstance(report, dict),
        f"exit={code} extract={type(report).__name__} stderr={err[:400]}",
    )

    # The app's own traffic, sliced before this verifier issues a single
    # request: a check about what the CONNECTOR read must not be able to read
    # the verifier's crawl.
    app_log = ctx.vendor(VENDOR).request_log()
    n_after_run = len(app_log)
    ctx.check_l1(
        "account_book_was_read_from_placemint",
        *_account_book_read(app_log, key["account_count"], key["distinct_accounts_on_the_book"]),
    )

    body = report if isinstance(report, dict) else {}
    by_id = _rows_by_id(body)
    document_rows = body.get("placements") if isinstance(body.get("placements"), list) else []
    expected_ids = sorted(r["placement_id"] for r in rows)
    document_ids = [r.get("placement_id") for r in document_rows if isinstance(r, dict)]
    exact_shape = (
        set(body) == REPORT_FIELDS
        and type(body.get("placement_count")) is int
        and type(body.get("billable_count")) is int
        and type(body.get("on_hold_count")) is int
        and isinstance(body.get("fee_total_billable"), (int, float))
        and not isinstance(body.get("fee_total_billable"), bool)
        and len(document_rows) == key["placement_count"]
        and len(document_ids) == len(document_rows)
        and document_ids == expected_ids
        and all(set(row) == ROW_FIELDS for row in document_rows if isinstance(row, dict))
    )

    ctx.check_l1(
        "extract_covers_the_live_book_exactly",
        body.get("placement_count") == key["placement_count"]
        and body.get("billable_count") == key["billable_count"]
        and body.get("on_hold_count") == key["on_hold_count"]
        and set(by_id) == set(expected_ids)
        and exact_shape,
        "extract says placement_count={} billable={} on_hold={} over {} distinct "
        "placement(s); exact artifact shape/order={}; Placemint holds {} on the book, {} of them under an account "
        "still trading, and {} retired placement(s) that do not belong in it".format(
            body.get("placement_count"), body.get("billable_count"),
            body.get("on_hold_count"), len(by_id), exact_shape, key["placement_count"],
            key["billable_count"], len(key["retired_placement_ids"])),
    )

    placements, accounts = _read_world(ctx)
    n_after_probe_1 = len(ctx.vendor(VENDOR).request_log())

    # One check per placement on the book, emitted unconditionally: a run that
    # produced no artifact fails each of these rather than shrinking the
    # denominator.
    for want in rows:
        ctx.check_l1(
            f"book_row_{want['placement_id']}_carries_the_account_placemint_holds",
            *_row_ok(by_id.get(want["placement_id"]), placements, accounts, want),
        )

    truth_total = round(sum(
        float(placements[r["placement_id"]].get("fee_amount"))
        for r in rows
        if placements.get(r["placement_id"]) is not None
        and isinstance(placements[r["placement_id"]].get("fee_amount"), (int, float))
        and _truth(placements[r["placement_id"]],
                   accounts.get(str(placements[r["placement_id"]].get("client_id")), {}))["billable"]
    ), 2)
    ctx.check_l1(
        "billable_fee_total_follows_the_accounts_placemint_holds",
        _close(body.get("fee_total_billable"), truth_total)
        and abs(truth_total) > 0.0,
        f"the extract totals {body.get('fee_total_billable')!r} over its billable rows; "
        f"the fees Placemint holds on the placements under a trading account come to "
        f"{truth_total}",
    )

    # -- the same extract again ------------------------------------------------
    code, _out, err = ctx.app.run()
    rerun = _read_extract(ctx)
    ctx.check_l1(
        "rerun_completed",
        code == 0 and isinstance(rerun, dict),
        f"exit={code} extract={type(rerun).__name__} stderr={err[:400]}",
    )
    ctx.check_l1(
        "rerun_reaches_the_same_extract",
        isinstance(rerun, dict) and isinstance(report, dict) and rerun == report,
        "the second run over an unchanged tenant produced a different extract"
        if isinstance(rerun, dict) and isinstance(report, dict)
        else "the second run produced no usable extract",
    )

    n_after_rerun = len(ctx.vendor(VENDOR).request_log())
    _placements_again, _accounts_again = _read_world(ctx)
    n_after_probe_2 = len(ctx.vendor(VENDOR).request_log())

    await builtin_l2(
        ctx,
        exclude_request_indices=[
            *range(n_after_run, n_after_probe_1),
            *range(n_after_rerun, n_after_probe_2),
        ],
        app_runs=2,
    )
