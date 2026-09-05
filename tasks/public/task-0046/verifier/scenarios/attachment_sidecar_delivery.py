"""task-0046 — attachment_sidecar_upload.

One delivery pass over an 18-row scorecard export, then a second pass to prove
the ledger is real. Every document has to end up as a note on its candidate with
the file attached; the interesting part is that neither write's response says
whether that happened.

WHAT IS PLANTED, WHERE IT BITES, AND HOW BROADLY IT IS ASSERTED
---------------------------------------------------------------

D1. THE UPLOAD RECEIPT IS NOT A SETTLEMENT (doc-vs-wire, attachment surface).
    docs/attachments.md says `POST /notes/{id}/attachments` answers `201` with
    a body carrying `attachment_id` and `state: "stored"`, and that content the
    platform will not keep comes back inline as `422` naming the reason. The
    wire answers `202` with `{"upload_id", "note_id", "status": "accepted",
    "received_bytes"}` — no attachment id, no state — and NEVER refuses content
    inline. A file the platform will not keep is accepted and then settled
    `rejected`, visible only at `GET /notes/{id}/attachments`.
    Where it bites: all thirteen documents whose candidate is writable. Five of
    them do not survive settlement, and a connector that trusts the receipt
    reports all thirteen as delivered.
    Asserted at three layers, per document: the request log (did the connector
    read the settled state back at all — 13 checks), the platform's own listing
    (is the file actually there, 13 checks), and the report against that
    listing (does the connector's claim match what the platform kept, 13
    checks).

D2. PURGED CANDIDATES VANISH; THEY DO NOT CARRY A FLAG (talentloop LIES.md #3).
    docs/entities.md § Soft deletes says a deleted record stays in list
    responses with `is_deleted: true` and tells the reader to filter on it. No
    record on this platform has an `is_deleted` key at all: a purged candidate
    is removed from `GET /candidates` outright, and only get-by-id separates
    purged (410 Gone) from never-issued (404 Not Found).
    Where it bites: five export rows name a candidate the crawl cannot account
    for — three purged in the CP8–CP22 retention batch, two ids above the
    tenant's 400-record seed. A crawl-only connector cannot tell them apart and
    files all five as "never issued".
    Asserted per row at two layers: the classification in the report (5) and
    the get-by-id reconcile in the request log (5), plus the quarantine
    artifact (5 of its 6 entries) and the unresolved count.

D3. THE EXPORT'S DIGESTS ARE STALE (stale snapshot vs truth).
    Four rows carry the digest of the pre-conversion original rather than of
    the bytes in the row, so the platform settles them `checksum_mismatch`.
    Those are the repairable rejections: recompute from the bytes, re-send, and
    the file lands. Nothing but the settled row says so.
    Where it bites: only a connector that read D1's settled state back is even
    in a position to notice. Asserted as 4 request-log checks (a second upload
    carrying the recomputed digest), and again inside D1's per-document
    platform-state and report checks.

D4. ONE REJECTION IS NOT REPAIRABLE. SC-0008 is `application/msword`, outside
    the accepted list, and re-sending identical bytes settles identically. It
    must be quarantined with the platform's own reason, and it must not be
    re-uploaded in a loop. Asserted as its platform-state check, its report
    check, its quarantine entry, the quarantined count, and a prohibition on a
    second upload.

MEASURED (rig, 2026-08-06, 72 checks): gold 1.000 (72/72) · starter 0.000 ·
stub 0.000 · naive 0.444 (32/72). The docs-faithful naive connector fails 40 of
them: all 13 settlement readbacks (it never calls the listing), 4
platform-state checks (the
stale-digest files are simply not on the platform), 5 report-vs-platform checks
(it claims `stored` for five files the platform rejected), 3 unresolved
classifications and all 5 get-by-id reconciles (it trusts the `is_deleted`
flag), 4 quarantine entries, 2 of the 3 counts, and all 4 repair checks.

WRONG-ANSWER BASINS (re-measured 2026-08-06 by adversarial review; every patch
is committed under `variants/` and probed with `--mode naive`):

| variant | what it adds to naive | score |
|---|---|---|
| `A_readback` | the single most obvious cautious guard: read the note's attachment listing back after the upload and report what it says. No repair, no reconcile. | **0.653** (47/72) |
| `B_reconcile` | the other single obvious guard: an id the crawl did not return is confirmed by get-by-id before being written off (any error -> missing). | **0.514** (37/72) |
| `B2_reconcile_410` | B, plus the 410-vs-404 branch a careful reader can infer from docs/writeback.md's "deleted / missing parents". | **0.597** (43/72) |
| `C_both` | A + B. | **0.722** (52/72) |
| `C2_both_410` | A + B2 -- the ceiling of a docs-only connector that adds every cautious guard without ever observing the sandbox. | **0.806** (58/72) |
| `D_altcorrect` | alternative-correct: batched (all notes, then all uploads, then one readback sweep, then a repair batch), reverse doc order, repair decided by comparing the recomputed digest to the declared one rather than by matching a `checksum_mismatch` literal. | **1.000** (72/72) |
| `E_nocrawl` | alternative-correct by a different route: no crawl at all, every export id resolved by get-by-id. | **0.986** (71/72) |

The margin is not one line wide. Each single guard lands at 0.514-0.653, well
under 0.75; it takes BOTH guards plus a doc inference (C2) to reach 0.806, and
even then D3's stale digests -- which are invisible to anything that has not
read the settled row AND compared it to a digest of its own bytes -- still cost
14 checks. That is the second independent device doing its job: A alone (which
defeats D1) is stopped by D3 and D2; B2 alone (which defeats D2) is stopped by
D1 and D3.

D_altcorrect at 1.000 is the over-fitting control: the checks grade the
platform's state and the request log, not this repo's control flow.

Every expectation comes from verifier/fixtures/answer_key.json, which
tools/rework/gen_answer_key_0069.py measures against a live vendor — the
candidate-resolution statuses and the settlement outcomes are both observed, not
asserted.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "talentloop"


# --------------------------------------------------------------------------
# reading the submission's artifacts
# --------------------------------------------------------------------------
def _read_json(ctx, name: str):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _rows_by_ref(report) -> dict[str, dict[str, Any]]:
    if not isinstance(report, dict):
        return {}
    out = {}
    for row in report.get("documents") or []:
        if isinstance(row, dict) and row.get("doc_ref"):
            out[str(row["doc_ref"])] = row
    return out


# --------------------------------------------------------------------------
# the verifier's own view of the vendor, over HTTP
# --------------------------------------------------------------------------
class VendorProbe:
    """Crawls the vendor directly. Never the connector's account of it."""

    def __init__(self, base_url: str, client_id: str, client_secret: str) -> None:
        self.base = base_url.rstrip("/")
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/token", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            self.token = json.load(resp)["access_token"]

    def get(self, path: str) -> tuple[int, Any]:
        req = urllib.request.Request(
            self.base + path, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except urllib.error.URLError:
            return 0, None

    def attachments(self, note_id: str) -> list[dict[str, Any]]:
        status, payload = self.get(f"/notes/{note_id}/attachments")
        if status != 200 or not isinstance(payload, dict):
            return []
        return [r for r in (payload.get("data") or []) if isinstance(r, dict)]


# --------------------------------------------------------------------------
# request-log slices
# --------------------------------------------------------------------------
def _note_posts(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in log
            if e.get("method") == "POST"
            and str(e.get("path", "")).startswith("/candidates/")
            and str(e.get("path", "")).endswith("/notes")]


def _uploads_to(log: list[dict[str, Any]], note_id: str) -> list[dict[str, Any]]:
    path = f"/notes/{note_id}/attachments"
    return [e for e in log if e.get("method") == "POST" and e.get("path") == path]


def _readbacks_of(log: list[dict[str, Any]], note_id: str) -> list[dict[str, Any]]:
    path = f"/notes/{note_id}/attachments"
    return [e for e in log if e.get("method") == "GET" and e.get("path") == path]


def _candidate_gets(log: list[dict[str, Any]], candidate_id: str) -> list[dict[str, Any]]:
    return [e for e in log
            if e.get("method") == "GET" and e.get("path") == f"/candidates/{candidate_id}"]


def _candidate_lists(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in log if e.get("method") == "GET" and e.get("path") == "/candidates"]


def _attachment_posts(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in log
            if e.get("method") == "POST"
            and str(e.get("path", "")).startswith("/notes/")
            and str(e.get("path", "")).endswith("/attachments")]


# --------------------------------------------------------------------------
async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    docs = {d["doc_ref"]: d for d in key["documents"]}
    live_refs = [d["doc_ref"] for d in key["documents"] if d["candidate_state"] == "live"]
    unresolved_refs = [d["doc_ref"] for d in key["documents"] if d["candidate_state"] != "live"]
    unwritable_ids = set(key["deleted_candidate_ids"]) | set(key["missing_candidate_ids"])

    handle = ctx.vendor(VENDOR)
    handle.recreate(checkpoint=key["checkpoint"], env=key["vendor_env"])

    # -- pass one -----------------------------------------------------------
    code, _out, err = ctx.app.run(["deliver"])
    report = _read_json(ctx, "attachment_report.json")
    quarantine = _read_json(ctx, "quarantine.json")
    ctx.check_l1(
        "scorecard_delivery_run_completed",
        code == 0 and isinstance(report, dict) and isinstance(quarantine, dict),
        f"exit={code} report={type(report).__name__} quarantine={type(quarantine).__name__} "
        f"stderr={err[:400]}",
    )
    if not isinstance(report, dict):
        return

    raw_rows = report.get("documents") if isinstance(report.get("documents"), list) else []
    rows = _rows_by_ref(report)
    log1 = handle.request_log()

    ctx.check_l1(
        "scorecard_report_covers_each_document_exactly_once",
        len(raw_rows) == key["document_count"] and set(rows) == set(docs),
        f"report has {len(raw_rows)} row(s), {len(rows)} distinct refs; "
        f"expected {key['document_count']} unique documents",
    )

    # -- pass two: the ledger has to mean something -------------------------
    code2, _out2, err2 = ctx.app.run(["deliver"])
    report2 = _read_json(ctx, "attachment_report.json")
    log2 = handle.request_log()
    rerun_slice = log2[len(log1):]

    # Conduct, once per vendor lifetime, and before the verifier injects any
    # traffic of its own: two app runs, so two legitimate token mints.
    await builtin_l2(ctx, app_runs=2)

    probe = VendorProbe(handle.base_url,
                        ctx.secrets.get("TL_CLIENT_ID", ""),
                        ctx.secrets.get("TL_CLIENT_SECRET", ""))

    posted_notes = _note_posts(log1)

    # The note is part of the tenant-visible delivery, not mere plumbing.
    bad_notes = []
    for ref in live_refs:
        want = docs[ref]
        hits = [e for e in posted_notes
                if e.get("path") == f"/candidates/{want['candidate_id']}/notes"
                and (e.get("body") or {}).get("body") == want["note_body"]]
        if (len(hits) != 1
                or (hits[0].get("body") or {}).get("author") != want["author"]
                or not (hits[0].get("headers") or {}).get("idempotency-key")):
            bad_notes.append(ref)
    note_keys = [(e.get("headers") or {}).get("idempotency-key") for e in posted_notes]
    ctx.check_l1(
        "scorecard_notes_match_candidate_wording_author_and_key",
        len(posted_notes) == len(live_refs)
        and len(set(note_keys)) == len(posted_notes)
        and not bad_notes,
        f"{len(posted_notes)} note POST(s), expected {len(live_refs)}; bad refs={bad_notes[:6]}",
    )

    # ---- D1/D3/D4: per document with a writable candidate -----------------
    for ref in live_refs:
        want = docs[ref]
        row = rows.get(ref) or {}
        note_id = row.get("note_id")
        settled = probe.attachments(str(note_id)) if note_id else []
        mine = [r for r in settled if r.get("filename") == want["filename"]]
        stored = [r for r in mine if r.get("state") == "stored"]
        last = mine[-1] if mine else None

        # layer 1 — the request log: was the settled state read back at all?
        # Non-vacuous: the connector must first have created a note for it.
        created = bool(note_id) and any(
            e.get("path") == f"/candidates/{want['candidate_id']}/notes" for e in posted_notes)
        readbacks = _readbacks_of(log1, str(note_id)) if note_id else []
        ctx.check_l1(
            f"sidecar_settlement_read_back_{ref}",
            created and len(readbacks) >= 1,
            f"note_id={note_id!r} note_created={created} "
            f"GET /notes/{note_id}/attachments x{len(readbacks)}",
        )

        uploads = _uploads_to(log1, str(note_id)) if note_id else []
        expected_digests = [want["declared_sha256"]]
        if want["repairable"]:
            expected_digests.append(want["actual_sha256"])
        upload_digests = [(e.get("body") or {}).get("sha256") for e in uploads]
        upload_keys = [(e.get("headers") or {}).get("idempotency-key") for e in uploads]
        ctx.check_l1(
            f"sidecar_upload_contract_{ref}",
            upload_digests == expected_digests
            and all(upload_keys)
            and len(set(upload_keys)) == len(expected_digests)
            and all((e.get("body") or {}).get("filename") == want["filename"]
                    and (e.get("body") or {}).get("content_type") == want["content_type"]
                    for e in uploads),
            f"upload digests={upload_digests}, keys={upload_keys}; "
            f"expected digests={expected_digests}",
        )

        # layer 2 — the platform: is the file there, in the state the tenant's
        # rule says it should end in?
        if want["expected_attachment_state"] == "stored":
            ok = (len(stored) == 1
                  and stored[0].get("sha256") == want["expected_stored_sha256"])
            detail = (f"{len(stored)} stored row(s) for {want['filename']}; "
                      f"rows={[(r.get('state'), r.get('reason')) for r in mine]}")
        else:
            ok = (not stored and last is not None
                  and last.get("state") == want["expected_attachment_state"]
                  and last.get("reason") == want["expected_attachment_reason"])
            detail = (f"expected no stored row and a final "
                      f"{want['expected_attachment_state']}/"
                      f"{want['expected_attachment_reason']}; "
                      f"rows={[(r.get('state'), r.get('reason')) for r in mine]}")
        ctx.check_l1(f"sidecar_on_platform_{ref}", ok, detail)

        # layer 3 — the report against the platform, not against a fixture: a
        # connector may not claim a state the platform does not hold.
        #
        # NON-VACUITY: `last is None` means the platform holds no settled row
        # for this document at all, which for a live candidate can only happen
        # if the connector never uploaded it. Without this guard a submission
        # that made no request whatsoever and reported
        # `attachment_state: null, attachment_reason: null, outcome: delivered`
        # matched null-against-null and passed all thirteen of these. Every
        # live document is uploaded exactly once by any connector that tried,
        # and a rejection settles into the listing too, so a correct run always
        # has a row here.
        claimed_state = row.get("attachment_state")
        claimed_reason = row.get("attachment_reason")
        actual_state = last.get("state") if last else None
        actual_reason = last.get("reason") if last else None
        ctx.check_l1(
            f"sidecar_report_matches_platform_{ref}",
            last is not None
            and claimed_state == actual_state and claimed_reason == actual_reason
            and row.get("outcome") == want["expected_outcome"]
            and row.get("reason") == want["expected_reason"]
            and row.get("candidate_id") == want["candidate_id"]
            and row.get("note_id") == last.get("note_id")
            and row.get("attachment_id") == last.get("attachment_id")
            and row.get("sha256") == last.get("sha256")
            and last.get("content_type") == want["content_type"],
            f"report says {claimed_state}/{claimed_reason} outcome={row.get('outcome')}; "
            f"platform holds {actual_state}/{actual_reason} "
            f"({len(mine)} settled row(s) for {want['filename']}); "
            f"expected outcome {want['expected_outcome']}",
        )

    # ---- D3: the repairable rejections were actually repaired -------------
    for ref in key["repairable_doc_refs"]:
        want = docs[ref]
        note_id = (rows.get(ref) or {}).get("note_id")
        uploads = _uploads_to(log1, str(note_id)) if note_id else []
        resent = [e for e in uploads
                  if isinstance(e.get("body"), dict)
                  and e["body"].get("sha256") == want["actual_sha256"]]
        ctx.check_l1(
            f"stale_digest_resent_{ref}",
            len(uploads) >= 1 and len(resent) >= 1,
            f"{len(uploads)} upload(s) to note {note_id}; "
            f"{len(resent)} carried the digest of the bytes actually sent",
        )

    # ---- D4: the unrepairable one is not re-sent in a loop ----------------
    for ref in [r for r in live_refs
                if docs[r]["expected_outcome"] == "quarantined"]:
        note_id = (rows.get(ref) or {}).get("note_id")
        uploads = _uploads_to(log1, str(note_id)) if note_id else []
        ctx.check_l1(
            f"unrepairable_not_resent_{ref}",
            len(uploads) == 1,
            f"{len(uploads)} upload(s) of {ref}; the platform's refusal is not "
            f"a transient the same bytes can beat",
        )

    # ---- D2: the export ids the crawl cannot account for ------------------
    listed = _candidate_lists(log1)
    for ref in unresolved_refs:
        want = docs[ref]
        row = rows.get(ref) or {}
        ctx.check_l1(
            f"unwritable_candidate_classified_{ref}",
            row.get("outcome") == "unresolved"
            and row.get("reason") == want["expected_reason"]
            and row.get("candidate_id") == want["candidate_id"]
            and all(row.get(field) is None for field in (
                "note_id", "attachment_id", "attachment_state",
                "attachment_reason", "sha256",
            )),
            f"{ref} -> {row.get('outcome')}/{row.get('reason')}; "
            f"expected unresolved/{want['expected_reason']} "
            f"(get-by-id answers {want['candidate_get_status']})",
        )
        # This is a POSITIVE assertion -- the connector must have reconciled
        # this id by get-by-id -- so it is non-vacuous by construction: zero
        # observations fails it. It deliberately does NOT also require a
        # `GET /candidates` crawl. Resolving every export id by get-by-id and
        # never listing the collection is a legitimate, structurally different
        # correct solution (variants/E_nocrawl.patch: 0.917 with the crawl
        # conjunct, 0.986 without it -- its only remaining loss is builtin_l2's
        # `no_unnecessary_full_resync:candidate`, which records no observation
        # when there is no list traffic to score). Coupling the two graded
        # style, not correctness.
        gets = _candidate_gets(log1, want["candidate_id"])
        ctx.check_l1(
            f"unwritable_candidate_reconciled_by_id_{ref}",
            len(gets) >= 1,
            f"{len(listed)} candidate list request(s), "
            f"{len(gets)} GET /candidates/{want['candidate_id']}",
        )

    # ---- nothing is written to a candidate that cannot take it ------------
    stray = [e for e in posted_notes
             if str(e.get("path", "")).split("/")[2] in unwritable_ids]
    ctx.check_l1(
        "no_note_posted_to_unwritable_candidate",
        bool(posted_notes) and not stray,
        f"{len(posted_notes)} note create(s), {len(stray)} of them aimed at a "
        f"purged or never-issued candidate",
    )

    # ---- the quarantine artifact -----------------------------------------
    held = {}
    held_rows = []
    if isinstance(quarantine, dict):
        held_rows = quarantine.get("documents") or []
        held = {str(r.get("doc_ref")): r for r in held_rows
                if isinstance(r, dict)}
    expected_held = {ref for ref, want in docs.items()
                     if want["expected_outcome"] != "delivered"}
    ctx.check_l1(
        "quarantine_contains_exact_non_delivered_subset",
        isinstance(quarantine, dict)
        and quarantine.get("count") == len(expected_held)
        and len(held_rows) == len(expected_held)
        and set(held) == expected_held,
        f"quarantine count={quarantine.get('count') if isinstance(quarantine, dict) else None}; "
        f"rows={len(held_rows)} distinct={len(held)}, expected={len(expected_held)}",
    )
    for ref, want in sorted(docs.items()):
        if want["expected_outcome"] == "delivered":
            continue
        entry = held.get(ref)
        ctx.check_l1(
            f"quarantine_holds_{ref}",
            entry is not None
            and entry.get("candidate_id") == want["candidate_id"]
            and entry.get("outcome") == want["expected_outcome"]
            and entry.get("reason") == want["expected_reason"]
            and entry.get("note_id") == (rows.get(ref) or {}).get("note_id"),
            f"quarantine entry for {ref}: {entry!r}; expected reason "
            f"{want['expected_reason']!r}",
        )

    # ---- the tenant's scoreboard -----------------------------------------
    for field in ("document_count", "delivered_count", "quarantined_count", "unresolved_count"):
        ctx.check_l1(
            f"scoreboard_{field}",
            report.get(field) == key[field],
            f"{field}={report.get(field)}, expected {key[field]}",
        )

    # ---- pass two --------------------------------------------------------
    ctx.check_l1(
        "rerun_completed",
        code2 == 0 and isinstance(report2, dict),
        f"exit={code2} stderr={err2[:400]}",
    )
    ctx.check_l1(
        "rerun_created_no_second_note",
        bool(posted_notes)
        and not _note_posts(rerun_slice)
        and not _attachment_posts(rerun_slice),
        f"pass one created {len(posted_notes)} note(s); pass two created "
        f"{len(_note_posts(rerun_slice))} more against an unchanged export",
    )
    # NON-VACUITY: two empty reports are trivially equal, so the outcome map has
    # to cover the whole export before "unchanged" means anything.
    outcomes1 = {r.get("doc_ref"): r.get("outcome") for r in report.get("documents") or []}
    outcomes2 = ({r.get("doc_ref"): r.get("outcome") for r in report2.get("documents") or []}
                 if isinstance(report2, dict) else {})
    quarantine2 = _read_json(ctx, "quarantine.json")
    ctx.check_l1(
        "rerun_report_is_stable",
        set(outcomes1) == set(docs)
        and outcomes2 == outcomes1
        and report2 == report
        and quarantine2 == quarantine,
        f"pass one reported {len(outcomes1)} of {len(docs)} export rows; "
        f"pass two reported {len(outcomes2)}; the per-document outcomes must be "
        f"identical on a re-run against an unchanged export",
    )
