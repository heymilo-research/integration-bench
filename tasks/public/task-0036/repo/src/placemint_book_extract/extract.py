"""Build the nightly extract of the Placemint placement book.

One row per placement on the book, carrying the account it sits under and
whether that account is one we can still invoice.

The account side comes out of `input/account_book.json`, the copy the weekly
account sync leaves behind. `docs/meridian-account-book-note.md` covers why:
the account list barely moves, and the nightly window is for the placement
crawl.

Billable is the same note's rule. Placemint drops an account's record when the
account stops trading with us, so an account that is still in the book is one we
can still invoice, and one we have not snapshotted yet is a new account and
bills as normal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from placemint_book_extract.client import PlacemintClient
from placemint_book_extract.config import Config
from placemint_book_extract.report import ReportWriter

# The note's rule for an account the weekly snapshot has not caught up with.
BILLABLE_WHEN_ACCOUNT_UNSEEN = True


def read_accounts(cfg: Config, client: PlacemintClient) -> dict[str, dict[str, Any]]:
    """The account book this extract resolves placements against, by client id."""
    snapshot = json.loads(Path(cfg.account_book_file).read_text(encoding="utf-8"))
    return {
        str(account.get("client_id")): account
        for account in (snapshot.get("accounts") or [])
    }


def read_book(client: PlacemintClient) -> list[dict[str, Any]]:
    """Every placement record Placemint currently holds, verbatim."""
    book: list[dict[str, Any]] = []
    offset = 0
    while True:
        envelope = client.placement_page(offset=offset)
        book.extend(envelope.get("data") or [])
        offset += int(envelope.get("limit") or 100)
        if offset >= int(envelope.get("total") or 0):
            return book


def is_billable(account: dict[str, Any] | None) -> bool:
    """Is the account this placement sits under one we can still invoice?"""
    if account is None:
        return BILLABLE_WHEN_ACCOUNT_UNSEEN
    return not account.get("is_deleted")


def extract_row(record: dict[str, Any], account: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "placement_id": str(record.get("id")),
        "client_id": str(record.get("client_id")),
        "client_name": str(account.get("name")) if account else "",
        "client_industry": str(account.get("industry")) if account else "",
        "candidate_name": str(record.get("candidate_name")),
        "role_title": str(record.get("role_title")),
        "stage": str(record.get("stage")),
        "fee_amount": record.get("fee_amount"),
        "billable": is_billable(account),
    }


def run_book_extract(cfg: Config) -> dict[str, Any]:
    client = PlacemintClient(cfg)
    writer = ReportWriter(cfg.output_dir)

    accounts = read_accounts(cfg, client)
    book = read_book(client)

    rows = [
        extract_row(record, accounts.get(str(record.get("client_id"))))
        for record in book
        if not record.get("is_deleted")
    ]

    report = writer.write(rows)
    return {
        "placement_count": report["placement_count"],
        "billable_count": report["billable_count"],
        "on_hold_count": report["on_hold_count"],
        "fee_total_billable": report["fee_total_billable"],
        "accounts_resolved": len(accounts),
        "placement_pages": client.placement_pages,
        "client_pages": client.client_pages,
        "token_mints": client.token_mints,
    }
