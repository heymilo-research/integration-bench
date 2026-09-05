"""task-0033 — echo_suppressed_two_way_sync.

Two cycles of Northgate Talent Partners' placement sync. RecruitOS holds the
pipeline and is read-only for this tenant; Placemint holds the money and is
where every write lands. `input/placement_links.csv` is the 41-line crosswalk
Revenue Ops maintain by hand, and it is deliberately grubby: 14 of its lines
name placements Placemint has retired and two name placements Placemint has
never held.

Between the two cycles the world moves on BOTH sides. RecruitOS advances from
CHECKPOINT 43 to 53, which moves ten of the linked applications to a new stage.
Placemint has no seeded mutation between those two points, so this verifier
plays the account managers itself: four `PATCH /api/placements/{id}` calls,
issued over Placemint's published API exactly as a human in the Placemint UI
would produce, to stages that are neither the placement's seeded stage nor the
stage cycle 1 pushed into it.

`docs/` holds both vendors' own documentation, byte-identical to the vendor
bundles (`tools/rework/check_honest_vendor_docs.py`: RecruitOS and Placemint
are both zero-lie control vendors and neither is touched here), plus ONE
task-local document — `docs/northgate-placement-sync-runbook.md`, Revenue Ops'
internal note, disclaimed in its second line as "our own note, not RecruitOS's
documentation and not Placemint's". The vendors are honest; the runbook is
where this tenant's beliefs live, and two of them are false.

  D1. **THE ECHO.** The runbook states, in its own dedicated section: *"It does
      not move when we PATCH the record over the API — the platform treats an
      API write as a synchronisation, not as an edit"*, and draws the
      conclusion in as many words: *"our own writes never come back to us on
      the `modified_since` feed. Anything the feed hands us on the next cycle
      is an account manager's move."* The wire says the opposite. Placemint's
      `update_placement` ends with `placement["updated_at"] = _now_iso()`
      (placemint/main.py) — every accepted write stamps the record, and the
      stamp it uses is a write-clock anchored a full day past every seeded and
      mutated timestamp in the tenant. So every placement cycle 1 writes to is
      on cycle 2's `modified_since` feed, carrying a timestamp newer than any
      watermark the connector could plausibly hold. MEASURED: cycle 1 pushes 21
      placements and every one of them is back on the cycle-2 feed. A connector
      that believes the runbook reads 21 of its own writes as account-manager
      moves, hands the marketplace authority for all of them, and therefore
      refuses to push the ten stage moves the recruiters actually made.

      The suppression has to be exact, not by id: three of the four placements
      the account managers move in this run are placements cycle 1 also wrote
      to. Their feed row carries a NEW stamp on an id the connector has written
      before, and dropping the id wholesale loses a genuine move.

  D2. **RETIRED PLACEMENTS ARE STILL ON THE FEED, AND STILL WRITABLE.** The
      runbook states: *"Placemint's `total` is the count of live placements.
      When an account manager retires a placement it drops off the feed and out
      of `total` on the next crawl"*, and concludes *"there is no need to
      re-check the status of every placement on every cycle."* Placemint's own
      `docs/placemint/entities.md` § Soft deletes says the exact opposite —
      a deleted record "is **not** removed from list responses ... it stays
      visible with `is_deleted: true`" — and `docs/placemint/writeback.md`
      § Missing parents adds that such a record "is still a valid target for
      both reads and writes". The wire sides with the vendor: `_page` filters
      nothing, `update_placement` checks no flag. MEASURED: 14 of the 41
      crosswalk lines name a retired placement, and 13 of those 14 carry an ATS
      stage that maps to something other than the stage the placement is frozen
      at — so a connector that believes the runbook writes into 13 requisitions
      that were closed out months ago, on its first cycle, and cannot take any
      of it back.

Three more devices are not documentation divergences. The rules are in the
ticket; these are what make the job worth grading:

  * THE CROSSWALK NAMES PLACEMENTS THAT DO NOT EXIST. `plc_00901` and
    `plc_00902` are Ken's typos. They have to be reported and the cycle has to
    finish; a connector that treats a line it cannot act on as a failed cycle
    loses the other 39 lines with it.

  * BOTH SIDES MOVE, AND ONLY ONE OF THEM IS AUTHORITATIVE PER CYCLE. Ten links
    move on the ATS side between cycles and four move on the marketplace side.
    A connector with a blanket policy fails one group or the other by
    construction, and the two groups are graded by opposed checks: the
    `pushed` links can only be satisfied by writing and the `inbound` links
    only by NOT writing.

  * THE FEED IS INCLUSIVE OF THE BOUNDARY INSTANT. `modified_since` on both
    platforms is `updated_at >= value`, so the row sitting exactly on the
    stored position comes back unchanged on every cycle. It is not a move.

MEASURED (`tools/rework/probe/sweep.py`, this scenario): see the task's WORKLOG
entry for the current numbers; the shape is gold 1.000, starter 0.000, stub
0.000, `naive.patch` (a tidy implementation faithful to `docs/` INCLUDING the
runbook) well under the 0.75 bar, and two single-device repair variants under
`variants/` measured separately so neither device is carrying the task alone.

Evidence: every check reads the connector's declared artifacts against
`verifier/fixtures/answer_key.json` (generated by
`tools/rework/gen_answer_key_0097.py` against both live vendors), Placemint's
state crawled by this verifier over its own published port, or Placemint's
request log sliced to the connector's own cycle-2 window. Every check that
credits absence or stability first proves the connector wrote something.
`builtin_l2` fires once per RecruitOS boot epoch (two epochs, one `recreate`),
immediately after the app run and before this verifier touches anything.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

ATS = "recruitos"
MARKETPLACE = "placemint"

REPORT_COLUMNS = (
    "application_id",
    "placement_id",
    "outcome",
    "ats_stage",
    "target_stage",
    "resulting_stage",
)


# ---------------------------------------------------------------------------
# the connector's artifacts
# ---------------------------------------------------------------------------
def _read_json(ctx, name: str) -> Any:
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_report(ctx) -> list[dict[str, str]]:
    path = Path(ctx.output_dir) / "import_report.csv"
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return [dict(row) for row in csv.DictReader(fh)]
    except OSError:
        return []


def _by_application(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        aid = (row.get("application_id") or "").strip()
        if aid and aid not in out:
            out[aid] = row
    return out


# ---------------------------------------------------------------------------
# Placemint, read and written by this verifier over its own published port
# ---------------------------------------------------------------------------
class _Marketplace:
    """A verifier-side Placemint session. Never the connector's account of it.

    Placemint's access tokens live 60 seconds and this scenario spans two
    connector runs and a vendor recreate, so the session re-mints on a timer
    rather than holding one token for the whole run — an expired verifier token
    reads as a broken scenario, not as a broken submission.
    """

    PAGE = 100
    _TOKEN_REUSE_S = 30.0

    def __init__(self, ctx) -> None:
        self.base = (ctx.vendor(MARKETPLACE).base_url or "").rstrip("/")
        self._client_id = ctx.secrets.get("PM_CLIENT_ID", "")
        self._client_secret = ctx.secrets.get("PM_CLIENT_SECRET", "")
        self._token = ""
        self._minted_at = 0.0

    def _headers(self) -> dict[str, str]:
        if not self._token or (time.monotonic() - self._minted_at) > self._TOKEN_REUSE_S:
            form = urllib.parse.urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base}/oauth/token",
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                self._token = json.load(resp)["access_token"]
            self._minted_at = time.monotonic()
        return {"Authorization": f"Bearer {self._token}"}

    def placements(self) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        offset = 0
        while True:
            url = (
                f"{self.base}/api/placements?"
                + urllib.parse.urlencode({"offset": offset, "limit": self.PAGE})
            )
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=30) as resp:
                envelope = json.load(resp)
            for record in envelope.get("data") or []:
                rows[str(record.get("id"))] = record
            total = int(envelope.get("total") or 0)
            if offset + self.PAGE >= total:
                return rows
            offset += self.PAGE

    def move(self, placement_id: str, stage: str) -> dict[str, Any]:
        """One account manager, moving one placement in the Placemint UI."""
        req = urllib.request.Request(
            f"{self.base}/api/placements/{placement_id}",
            data=json.dumps({"stage": stage}).encode("utf-8"),
            headers={
                **self._headers(),
                "Content-Type": "application/json",
                "Idempotency-Key": f"placemint-ui-{placement_id}-{stage}",
            },
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)


# ---------------------------------------------------------------------------
# request-log helpers
# ---------------------------------------------------------------------------
def _accepted_patches(log_slice: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(placement_id, stage) for every write Placemint ACCEPTED in this slice.

    Attempts the platform refused are deliberately not counted: a connector
    whose every write bounced has not written anything.
    """
    out: list[tuple[str, str]] = []
    for entry in log_slice:
        if (entry.get("method") or "").upper() != "PATCH":
            continue
        path = str(entry.get("path") or "")
        if not path.startswith("/api/placements/"):
            continue
        try:
            status = int(entry.get("status"))
        except (TypeError, ValueError):
            continue
        if status != 200:
            continue
        body = entry.get("body")
        stage = str((body or {}).get("stage") or "") if isinstance(body, dict) else ""
        out.append((path.rsplit("/", 1)[-1], stage))
    return out


# ---------------------------------------------------------------------------
# per-cycle assertions
# ---------------------------------------------------------------------------
def _row_check(expected: dict[str, str], got: dict[str, str] | None, aid: str, pid: str):
    if got is None:
        return False, f"{aid} ({pid}): no report row"
    wrong = []
    for column in ("placement_id", "outcome", "ats_stage", "target_stage", "resulting_stage"):
        want = pid if column == "placement_id" else str(expected.get(column, ""))
        have = str(got.get(column) or "").strip()
        if have != want:
            wrong.append(f"{column}={have!r} (want {want!r})")
    if wrong:
        return False, f"{aid} ({pid}): " + ", ".join(wrong)
    return True, f"{aid} ({pid}): {expected['outcome']}"


def _emit_rows(ctx, key: dict[str, Any], prefix: str, expected_rows: dict[str, dict],
               report: dict[str, dict[str, str]]) -> None:
    links = key["links"]
    for aid in key["link_order"]:
        ctx.check_l1(
            f"{prefix}_link_{aid}",
            *_row_check(expected_rows[aid], report.get(aid), aid, links[aid]),
        )


def _counts_check(expected: dict[str, int], result: Any):
    if not isinstance(result, dict):
        return False, "no result.json"
    got = result.get("counts")
    if not isinstance(got, dict):
        return False, f"result.json carries no counts object (got {type(got).__name__})"
    diffs = [
        f"{name}={got.get(name)} (want {want})"
        for name, want in sorted(expected.items())
        if int(got.get(name) or 0) != want
    ]
    if diffs:
        return False, "; ".join(diffs)
    return True, f"counts {expected}"


def _writeback_check(expected_ids: list[str], log: Any, connector_wrote: bool):
    if not connector_wrote:
        return False, "the connector accepted no write on this cycle"
    if not isinstance(log, dict):
        return False, "no writeback_log.json"
    writes = log.get("writes")
    if not isinstance(writes, list):
        return False, "writeback_log.json carries no writes array"
    landed = sorted(
        {
            str(w.get("placement_id"))
            for w in writes
            if isinstance(w, dict) and int(w.get("status") or 0) == 200
        }
    )
    want = sorted(set(expected_ids))
    if landed != want:
        missing = [p for p in want if p not in landed]
        extra = [p for p in landed if p not in want]
        return False, (
            f"{len(landed)} accepted write(s) logged, expected {len(want)}; "
            f"missing={missing[:4]} unexpected={extra[:4]}"
        )
    return True, f"{len(landed)} accepted write(s) logged"


def _artifacts_exact(
    *,
    cycle: int,
    key: dict[str, Any],
    result: Any,
    raw_report: list[dict[str, str]],
    writeback: Any,
) -> tuple[bool, str]:
    """Validate the complete published artifact schemas and cross-links."""
    expected_rows = key[f"cycle{cycle}_rows"]
    expected_counts = key[f"cycle{cycle}_counts"]
    order = key["link_order"]
    links = key["links"]
    expected_write_ids = [
        links[aid] for aid in order if expected_rows[aid]["outcome"] == "pushed"
    ]
    if not isinstance(result, dict) or set(result) != {
        "cycle", "links", "counts", "ats_watermark", "marketplace_watermark"
    }:
        return False, "result.json schema mismatch"
    if result.get("cycle") != cycle or result.get("links") != len(order):
        return False, f"result cycle/links={result.get('cycle')!r}/{result.get('links')!r}"
    if result.get("counts") != expected_counts:
        return False, f"result counts={result.get('counts')!r}, expected={expected_counts!r}"
    if not isinstance(result.get("ats_watermark"), str) or not isinstance(
        result.get("marketplace_watermark"), str
    ):
        return False, "watermarks must be strings"
    if cycle == 1 and (
        result["ats_watermark"] != key["ats_watermark_after_cycle1"]
        or result["marketplace_watermark"] != key["marketplace_watermark_after_cycle1"]
    ):
        return False, "cycle-1 watermarks do not match the crawled collections"

    if len(raw_report) != len(order):
        return False, f"report has {len(raw_report)} rows, expected {len(order)}"
    if any(set(row) != set(REPORT_COLUMNS) for row in raw_report):
        return False, "report carries missing or extra columns"
    for aid, row in zip(order, raw_report, strict=True):
        ok, detail = _row_check(expected_rows[aid], row, aid, links[aid])
        if not ok or row.get("application_id") != aid:
            return False, detail

    if not isinstance(writeback, dict) or set(writeback) != {"cycle", "writes"}:
        return False, "writeback_log.json schema mismatch"
    writes = writeback.get("writes")
    if writeback.get("cycle") != cycle or not isinstance(writes, list):
        return False, "writeback cycle/writes mismatch"
    if len(writes) != len(expected_write_ids):
        return False, f"writeback has {len(writes)} rows, expected {len(expected_write_ids)}"
    got_ids: list[str] = []
    keys: list[str] = []
    for row in writes:
        if not isinstance(row, dict) or set(row) != {
            "placement_id", "stage", "status", "idempotency_key"
        }:
            return False, "writeback row schema mismatch"
        pid = str(row.get("placement_id") or "")
        expected_stage = next(
            (expected_rows[aid]["target_stage"] for aid in order if links[aid] == pid), ""
        )
        if row.get("status") != 200 or row.get("stage") != expected_stage:
            return False, f"writeback {pid} status/stage mismatch"
        got_ids.append(pid)
        keys.append(str(row.get("idempotency_key") or ""))
    if sorted(got_ids) != sorted(expected_write_ids):
        return False, "writeback placement multiset mismatch"
    if not all(keys) or len(set(keys)) != len(keys):
        return False, "idempotency keys must be nonempty and unique per logical write"
    return True, "all three artifacts are schema-, order-, value-, and cardinality-exact"


def _traffic_exact(
    expected_rows: dict[str, dict[str, str]],
    links: dict[str, str],
    order: list[str],
    writes: list[tuple[str, str]],
    log_slice: list[dict[str, Any]],
) -> tuple[bool, str]:
    expected = sorted(
        (links[aid], expected_rows[aid]["target_stage"])
        for aid in order
        if expected_rows[aid]["outcome"] == "pushed"
    )
    accepted_entries = [
        entry for entry in log_slice
        if (entry.get("method") or "").upper() == "PATCH"
        and str(entry.get("path") or "").startswith("/api/placements/")
        and entry.get("status") == 200
    ]
    keys = [str(entry.get("idempotency_key") or "") for entry in accepted_entries]
    ok = (
        sorted(writes) == expected
        and len(accepted_entries) == len(expected)
        and all(keys)
        and len(set(keys)) == len(keys)
    )
    return ok, (
        f"accepted PATCHes={len(writes)}, expected={len(expected)}, "
        f"nonempty unique idempotency keys={len({key for key in keys if key})}"
    )


# ---------------------------------------------------------------------------
async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    links: dict[str, str] = key["links"]
    order: list[str] = key["link_order"]
    seed_state: dict[str, dict] = key["placement_seed_state"]
    moves: dict[str, str] = key["account_manager_moves"]

    # =====================================================================
    # CYCLE 1
    # =====================================================================
    market_log_before_1 = len(ctx.vendor(MARKETPLACE).request_log())
    code, _out, err = ctx.app.run()
    market_log_after_1 = len(ctx.vendor(MARKETPLACE).request_log())
    result1 = _read_json(ctx, "result.json")
    raw_report1 = _read_report(ctx)
    report1 = _by_application(raw_report1)
    ctx.check_l1(
        "placement_sync_cycle1_completed",
        code == 0 and isinstance(result1, dict) and len(report1) == len(order),
        f"exit={code} result={type(result1).__name__} rows={len(report1)} "
        f"(want {len(order)}) stderr={err[:300]}",
    )

    # Conduct for the first RecruitOS boot epoch, before this verifier issues
    # anything at all. The epoch's request log is unlinked by the recreate
    # below, so it can only be graded here.
    await builtin_l2(ctx, app_runs=1)

    _emit_rows(ctx, key, "cycle1", key["cycle1_rows"], report1)
    ctx.check_l1(
        "cycle1_outcome_counts",
        *_counts_check(key["cycle1_counts"], result1),
    )
    ctx.check_l1(
        "cycle1_all_artifacts_match_the_complete_contract",
        *_artifacts_exact(
            cycle=1, key=key, result=result1, raw_report=raw_report1,
            writeback=_read_json(ctx, "writeback_log.json"),
        ),
    )

    market = _Marketplace(ctx)
    state_after_1 = market.placements()

    pushed_1 = [aid for aid in order if key["cycle1_rows"][aid]["outcome"] == "pushed"]
    pushed_1_ids = [links[aid] for aid in pushed_1]
    market_log = ctx.vendor(MARKETPLACE).request_log()
    cycle1_writes = _accepted_patches(market_log[market_log_before_1:market_log_after_1])
    connector_wrote_1 = bool(cycle1_writes)
    ctx.check_l1(
        "cycle1_accepted_write_traffic_is_exact",
        *_traffic_exact(
            key["cycle1_rows"], links, order, cycle1_writes,
            market_log[market_log_before_1:market_log_after_1],
        ),
    )

    landed = [
        pid
        for pid in pushed_1_ids
        if str(state_after_1.get(pid, {}).get("stage") or "")
        == key["placement_state_after_cycle1"][pid]["stage"]
    ]
    ctx.check_l1(
        "cycle1_pushed_placements_carry_the_ats_stage",
        len(landed) == len(pushed_1_ids) and bool(pushed_1_ids),
        f"{len(landed)}/{len(pushed_1_ids)} placement(s) hold the mapped ATS stage on "
        f"Placemint after cycle 1",
    )

    retired_ids = key["retired_placements"]
    disturbed = sorted(
        {pid for pid, _stage in cycle1_writes if pid in set(retired_ids)}
        | {
            pid
            for pid in retired_ids
            if str(state_after_1.get(pid, {}).get("stage") or "")
            != str(seed_state[pid]["stage"])
        }
    )
    ctx.check_l1(
        "cycle1_retired_placements_not_written_to",
        connector_wrote_1 and not disturbed,
        (
            f"{len(disturbed)} retired placement(s) were written to: {disturbed[:5]}"
            if disturbed
            else (
                f"all {len(retired_ids)} retired placement(s) untouched"
                if connector_wrote_1
                else "the connector wrote nothing at all, so touching nothing proves nothing"
            )
        ),
    )
    ctx.check_l1(
        "cycle1_writeback_log_lists_the_accepted_writes",
        *_writeback_check(pushed_1_ids, _read_json(ctx, "writeback_log.json"), connector_wrote_1),
    )

    # =====================================================================
    # BETWEEN THE CYCLES — both sides move
    # =====================================================================
    for placement_id, stage in sorted(moves.items()):
        market.move(placement_id, stage)

    ctx.vendor(ATS).recreate(checkpoint=key["checkpoints"]["recruitos_cycle2"])

    # =====================================================================
    # CYCLE 2
    # =====================================================================
    market_log_before = len(ctx.vendor(MARKETPLACE).request_log())
    code, _out, err = ctx.app.run()
    market_log_after = len(ctx.vendor(MARKETPLACE).request_log())
    result2 = _read_json(ctx, "result.json")
    raw_report2 = _read_report(ctx)
    report2 = _by_application(raw_report2)
    ctx.check_l1(
        "placement_sync_cycle2_completed",
        code == 0 and isinstance(result2, dict) and len(report2) == len(order),
        f"exit={code} result={type(result2).__name__} rows={len(report2)} "
        f"(want {len(order)}) stderr={err[:300]}",
    )

    # Conduct for the second RecruitOS boot epoch.
    await builtin_l2(ctx, app_runs=1)

    _emit_rows(ctx, key, "cycle2", key["cycle2_rows"], report2)
    ctx.check_l1(
        "cycle2_outcome_counts",
        *_counts_check(key["cycle2_counts"], result2),
    )
    ctx.check_l1(
        "cycle2_all_artifacts_match_the_complete_contract",
        *_artifacts_exact(
            cycle=2, key=key, result=result2, raw_report=raw_report2,
            writeback=_read_json(ctx, "writeback_log.json"),
        ),
    )

    report_order = [
        (row.get("application_id") or "").strip() for row in _read_report(ctx)
    ]
    ctx.check_l1(
        "cycle2_report_follows_the_crosswalk_order",
        report_order == order,
        f"report row order differs from the crosswalk at index "
        f"{next((i for i, (a, b) in enumerate(zip(report_order, order)) if a != b), 'n/a')}"
        f" (rows={len(report_order)}, crosswalk={len(order)})",
    )

    # -- the connector's own cycle-2 traffic, verifier traffic excluded -----
    market_log = ctx.vendor(MARKETPLACE).request_log()
    cycle2_slice = market_log[market_log_before:market_log_after]
    cycle2_writes = _accepted_patches(cycle2_slice)
    connector_wrote_2 = bool(cycle2_writes)
    ctx.check_l1(
        "cycle2_accepted_write_traffic_is_exact",
        *_traffic_exact(key["cycle2_rows"], links, order, cycle2_writes, cycle2_slice),
    )

    for aid in order:
        row = key["cycle2_rows"][aid]
        if row["outcome"] != "pushed":
            continue
        pid = links[aid]
        want = row["target_stage"]
        for_this = [stage for placement_id, stage in cycle2_writes if placement_id == pid]
        ctx.check_l1(
            f"cycle2_push_landed_{pid}",
            bool(for_this) and all(stage == want for stage in for_this),
            f"{pid} ({aid}): Placemint accepted {len(for_this)} write(s) "
            f"{sorted(set(for_this))}, expected the ATS stage {want!r}",
        )

    moved_ids = sorted(moves)
    overwritten = sorted({pid for pid, _stage in cycle2_writes if pid in moves})
    ctx.check_l1(
        "cycle2_account_manager_moves_not_overwritten",
        connector_wrote_2 and not overwritten,
        (
            f"the connector wrote over {len(overwritten)} placement(s) the account managers "
            f"had just moved: {overwritten}"
            if overwritten
            else (
                f"all {len(moved_ids)} account-manager move(s) left alone"
                if connector_wrote_2
                else "the connector accepted no write in cycle 2, so writing over nothing "
                     "proves nothing"
            )
        ),
    )

    pushed_2_ids = [
        links[aid] for aid in order if key["cycle2_rows"][aid]["outcome"] == "pushed"
    ]
    ctx.check_l1(
        "cycle2_writeback_log_lists_the_accepted_writes",
        *_writeback_check(pushed_2_ids, _read_json(ctx, "writeback_log.json"), connector_wrote_2),
    )

    # -- where Placemint actually ends up, per linked placement -------------
    #
    # For a link the rules say to LEAVE ALONE — a retired requisition, an
    # account manager's own move, a link that was already in step — the expected
    # end state is the state Placemint was already in, which a submission that
    # never wrote anything holds for free. Those are gated on a witness that the
    # connector actually wrote: it has to have had a write accepted in each
    # cycle before "and it did not write here" means anything. The links whose
    # expected end state DIFFERS from the seeded one need no gate; nothing but a
    # correct write produces them.
    final = market.placements()
    expected_final: dict[str, str] = key["placement_state_after_cycle2"]
    acted = connector_wrote_1 and connector_wrote_2
    for aid in order:
        pid = links[aid]
        if pid not in expected_final:
            continue
        record = final.get(pid)
        want = expected_final[pid]
        got = str((record or {}).get("stage") or "")
        # The stage this placement would hold if the connector had never run at
        # all: the seeded one, or the account manager's if they moved it. When
        # the expected end state IS that stage, the check credits stability and
        # needs a witness; otherwise nothing but a correct write produces it.
        untouched = moves.get(pid, str(seed_state.get(pid, {}).get("stage") or ""))
        credits_stability = want == untouched
        ok = record is not None and got == want
        if credits_stability and not acted:
            ok = False
            detail = (
                f"{pid} ({aid}, {key['cycle2_rows'][aid]['outcome']}): this link's end state is "
                "the state Placemint was already in, and the connector had no write accepted "
                "in both cycles — leaving it alone proves nothing"
            )
        else:
            detail = (
                f"{pid} ({aid}, {key['cycle2_rows'][aid]['outcome']}): Placemint holds "
                f"stage={got!r}, expected {want!r}"
            )
        ctx.check_l1(f"final_placement_{pid}", ok, detail)
