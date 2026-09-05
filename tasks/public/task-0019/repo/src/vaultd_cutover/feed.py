"""The Vettly change-feed cycle.

One cycle, resumed from the cursor vaultd handed over. The broker stored it in
ISO 8601, which is the format `modified_since` takes (docs/pagination.md), so
it goes back across the wire as it stands and the same string is what the next
cycle picks up from.

The one job vaultd used to do that has to be done here is stitching the person
onto every event: Vettly's records only name their immediate parent, so a
check's person comes from the check and a report's comes from the check the
report hangs off. Both are resolved by id and cached, since a moved report
usually names a check that has not itself moved.

Everything the feed carries is an upsert -- Vettly's list endpoints are the
live-records surface, so a record that reaches the cycle is a record the
warehouse should hold.
"""

from __future__ import annotations

from typing import Any

from vaultd_cutover.config import Config
from vaultd_cutover.handover import HandoverState, read_handover
from vaultd_cutover.store import FeedStore
from vaultd_cutover.vettly_client import VettlyClient

KINDS = ("subject", "check", "report")
COLLECTION = {"subject": "subjects", "check": "checks", "report": "reports"}
DETAIL_FIELD = {"subject": "email", "check": "status", "report": "result"}


class _World:
    """Parents the delta did not carry, fetched by id once each."""

    def __init__(self, vettly: VettlyClient, moved: dict[str, list[dict]]) -> None:
        self._vettly = vettly
        self._subjects = {str(r["id"]): r for r in moved["subject"]}
        self._checks = {str(r["id"]): r for r in moved["check"]}

    def subject(self, subject_id: str) -> dict[str, Any]:
        sid = str(subject_id)
        if sid not in self._subjects:
            self._subjects[sid] = self._vettly.get_record("subjects", sid) or {}
        return self._subjects[sid]

    def check(self, check_id: str) -> dict[str, Any]:
        cid = str(check_id)
        if cid not in self._checks:
            self._checks[cid] = self._vettly.get_record("checks", cid) or {}
        return self._checks[cid]


def build_entry(record: dict[str, Any], kind: str, world: _World) -> dict[str, Any]:
    """One Vettly record as a change-file entry."""
    if kind == "subject":
        subject: dict[str, Any] = record
    elif kind == "check":
        subject = world.subject(str(record.get("subject_id") or ""))
    else:
        parent = world.check(str(record.get("check_id") or ""))
        subject = world.subject(str(parent.get("subject_id") or ""))
    return {
        "record_id": str(record.get("id")),
        "kind": kind,
        "op": "upsert",
        "subject_id": subject.get("id"),
        "subject_email": subject.get("email"),
        "updated_at": record.get("updated_at"),
        "detail": record.get(DETAIL_FIELD[kind]),
    }


def run_cycle(cfg: Config) -> dict[str, Any]:
    vettly = VettlyClient(cfg)
    store = FeedStore(cfg.output_dir)

    handover: HandoverState = read_handover(cfg.input_dir)
    cursor = handover.last_delivered_cursor

    moved = {
        kind: vettly.crawl(COLLECTION[kind], modified_since=cursor)
        for kind in KINDS
    }
    world = _World(vettly, moved)

    changes = [
        build_entry(record, kind, world)
        for kind in KINDS
        for record in moved[kind]
    ]
    stamps = [entry["updated_at"] for entry in changes
              if entry.get("updated_at") is not None]
    next_cursor = max(stamps) if stamps else cursor

    return store.write(changes, cursor_used=cursor, next_cursor=next_cursor)
