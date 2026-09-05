"""Sync loop: crawl Vettly collections into the canonical store. See ``PROBLEM.md``."""

from __future__ import annotations

from .auth import VettlyAuth
from .client import VettlyClient
from .config import Config
from .store import Store

COLLECTIONS = ("subjects", "checks", "reports")


def sync(config: Config, incremental: bool) -> None:
    store = Store(config.output_dir)
    auth = VettlyAuth(config.vendor_base_url, config.client_id, config.client_secret)
    client = VettlyClient(config.vendor_base_url, auth)

    for table in COLLECTIONS:
        _sync_collection(store, client, table, incremental=incremental)


def _sync_collection(store: Store, client: VettlyClient, table: str, incremental: bool) -> None:
    raise NotImplementedError
