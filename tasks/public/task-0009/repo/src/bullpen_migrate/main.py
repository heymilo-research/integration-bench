"""Bullpen migrate CLI entrypoint (provided). See ``PROBLEM.md``."""

from __future__ import annotations

from bullpen_migrate import config, store
from bullpen_migrate.client import BullpenClient
from bullpen_migrate.mapping import to_wire_modified_since
from bullpen_migrate.state import load_state, save_state
from bullpen_migrate.sync import fetch_collection

STATE_PATH = config.OUTPUT_DIR / "state.json"
OUTPUT_FILES = {
    "candidates": "candidates.json",
    "jobs": "jobs.json",
    "applications": "applications.json",
}


def main() -> None:
    state = load_state(STATE_PATH)
    client = BullpenClient(auth_mode=state.get("auth_mode", "legacy"))

    for kind, filename in OUTPUT_FILES.items():
        watermark = state["watermarks"].get(kind)
        wire_since = to_wire_modified_since(kind, watermark) if watermark else None
        records = fetch_collection(client, kind, wire_since)

        out_path = config.OUTPUT_DIR / filename
        existing = store.read_existing(out_path)
        merged = store.merge_rows(existing, records)
        store.write_store(out_path, merged)

        if records:
            state["watermarks"][kind] = max(r["modified_at"] for r in records)

    state["auth_mode"] = client.auth_mode
    save_state(STATE_PATH, state)


if __name__ == "__main__":
    main()
