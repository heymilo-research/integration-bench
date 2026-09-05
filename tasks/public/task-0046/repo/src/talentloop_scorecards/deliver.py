"""Deliver the scorecard export into TalentLoop.

Placing a document takes two calls, one per docs/attachments.md:

    POST /candidates/{candidate_id}/notes   -> the note
    POST /notes/{note_id}/attachments       -> the file, 201 + the settled state

Both are idempotent under `Idempotency-Key`, so a document is keyed on its
`doc_ref` and a retry of the same document is a no-op rather than a duplicate
(docs/writeback.md). The upload's `201` body carries the attachment id and the
attachment's state; content the platform will not take is refused inline as a
`422` whose `field_errors` body names the reason, so the reason we quarantine on
comes straight out of that response.

Candidate resolution comes off one crawl of `/candidates`. Soft-deleted records
stay in list responses with `is_deleted: true` (docs/entities.md), so one pass
over the collection tells us both which ids are active and which have been
deleted, and an export id that the collection does not account for at all is an
id TalentLoop never issued. Writing to an id we already know is not there would
just be a round trip we can skip.

A `Ledger` in the output directory keeps what is already placed, so a second
pass only picks up what is still outstanding.
"""

from __future__ import annotations

from typing import Any

from talentloop_scorecards.client import (
    TalentLoopClient,
    TalentLoopHTTPError,
)
from talentloop_scorecards.config import Config
from talentloop_scorecards.manifest import Document, load_documents, note_body_for
from talentloop_scorecards.store import Ledger, ScorecardStore

TERMINAL_OUTCOMES = ("delivered", "quarantined")


def resolve_candidates(client: TalentLoopClient,
                       documents: list[Document]) -> dict[str, str]:
    """Map every candidate id in the export to `live`, `deleted` or `missing`."""
    wanted = {doc.candidate_id for doc in documents}
    states: dict[str, str] = {}
    for rec in client.crawl("candidates"):
        cid = rec.get("id")
        if cid in wanted:
            states[cid] = "deleted" if rec.get("is_deleted") else "live"
    for cid in wanted - set(states):
        states[cid] = "missing"
    return states


def _rejection_reason(payload: Any) -> str:
    """Pull the platform's reason out of a field_errors body."""
    errors = (payload or {}).get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, dict):
        for messages in errors.values():
            if messages:
                return str(messages[0])
    return "rejected"


def deliver_document(client: TalentLoopClient, doc: Document) -> dict[str, Any]:
    """Create the note, upload the file, and report what the platform said."""
    note = client.create_note(
        doc.candidate_id, note_body_for(doc), doc.author,
        idempotency_key=f"sc:{doc.doc_ref}:note",
    )
    note_id = note["id"]

    payload = {
        "filename": doc.filename,
        "content_type": doc.content_type,
        "sha256": doc.sha256,
        "content_b64": doc.content_b64,
    }
    try:
        created = client.upload_attachment(
            note_id, payload, idempotency_key=f"sc:{doc.doc_ref}:att",
        ) or {}
    except TalentLoopHTTPError as exc:
        if exc.status != 422:
            raise
        reason = _rejection_reason(exc.payload)
        return {
            "doc_ref": doc.doc_ref,
            "candidate_id": doc.candidate_id,
            "outcome": "quarantined",
            "reason": reason,
            "note_id": note_id,
            "attachment_id": None,
            "attachment_state": "rejected",
            "attachment_reason": reason,
            "sha256": doc.sha256,
        }

    return {
        "doc_ref": doc.doc_ref,
        "candidate_id": doc.candidate_id,
        "outcome": "delivered",
        "reason": None,
        "note_id": note_id,
        "attachment_id": created.get("attachment_id"),
        "attachment_state": created.get("state", "stored"),
        "attachment_reason": None,
        "sha256": doc.sha256,
    }


def _unresolved_row(doc: Document, state: str) -> dict[str, Any]:
    return {
        "doc_ref": doc.doc_ref,
        "candidate_id": doc.candidate_id,
        "outcome": "unresolved",
        "reason": {"deleted": "candidate_deleted",
                   "missing": "candidate_missing"}.get(state, "candidate_unavailable"),
        "note_id": None,
        "attachment_id": None,
        "attachment_state": None,
        "attachment_reason": None,
        "sha256": None,
    }


def run_delivery(cfg: Config) -> dict[str, Any]:
    client = TalentLoopClient(cfg)
    store = ScorecardStore(cfg.output_dir)
    ledger = Ledger(cfg.output_dir)

    documents = load_documents(cfg.input_file)
    outstanding = [
        doc for doc in documents
        if (ledger.get(doc.doc_ref) or {}).get("outcome") not in TERMINAL_OUTCOMES
    ]

    states = resolve_candidates(client, outstanding) if outstanding else {}

    rows: list[dict[str, Any]] = []
    for doc in documents:
        done = ledger.get(doc.doc_ref)
        if done and done.get("outcome") in TERMINAL_OUTCOMES:
            rows.append(done)
            continue

        state = states.get(doc.candidate_id, "missing")
        row = deliver_document(client, doc) if state == "live" else _unresolved_row(doc, state)
        rows.append(row)
        if row["outcome"] in TERMINAL_OUTCOMES:
            ledger.put(doc.doc_ref, row)

    ledger.save()
    return store.write(rows)
