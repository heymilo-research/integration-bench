"""Regenerate / cross-check task-0049's per-checkpoint placement fixtures.

Added for the 2026-08 re-ladder: the `compounding_faults_summit` scenario's
walk now banks an
intermediate L1 snapshot after each of checkpoints 2-6 (checkpoint 7 rolls
into the pre-existing `placements_summit.json`), instead of a single
all-or-nothing check at the very end. `placements_cp2.json`/`placements_cp3.json`
are reused byte-for-byte from the other scenarios (`poll_reconciliation_
recovers_dropped_event` / `webhook_freshness_clean`) -- confirmed safe to reuse
because a mid-walk `writeback` push (targeting plc_00002, see
repo/input/pending_writes.json) never survives a subsequent vendor
`recreate(checkpoint=...)`: each recreate rebuilds `APP_STATE` from
`placemint.state.build_state(seed, checkpoint)` alone (a pure function of
seed+checkpoint, see vendors/placemint/src/placemint/main.py:74), and the
connector's own poll-reconciliation resyncs plc_00002 back to that pure value
on the very next pass (mutations.yaml never touches plc_00002, so its
`updated_at` never advances past the original seed timestamp -- the writeback
patch's real-wall-clock `updated_at` is what makes it durable ONLY within a
single, never-recreated vendor boot, exactly as `writeback_under_pressure.py`
exercises). `placements_summit.json` itself already reflects this: its
plc_00002 record is byte-identical to the untouched seed value in
`placements_cp0/cp2/cp3.json`, NOT the writeback-patched one in
`writeback_result.json` -- empirically confirmed below in `_cross_check_all()`.

Two independent generation paths, cross-checked against each other:

  1. ``_pure(checkpoint)`` -- calls ``placemint.state.build_state()`` directly
     (no process, no port; byte-identical to what `main.py` computes at
     import time for the identical env).
  2. ``_via_http(checkpoint)`` -- boots the REAL `placemint.main:app` FastAPI
     app in-process on an EPHEMERAL localhost port (stdlib `socket` to reserve
     it, `uvicorn.Server` run in a background thread -- no Docker), mints an
     OAuth token, and pages `/api/placements` to exhaustion via the documented
     offset/limit/total envelope (DATASET_SIZE=200 for this task -> 2 pages).
     Proves the wire/JSON round-trip introduces no drift versus the pure
     function.

Both paths are mapped through ``_canonical()``, which mirrors
``repo/src/placemint_summit/store.py::canonical_row`` EXACTLY (same field
selection, same sort key) -- this is a read-only, side-effect-free copy for
fixture generation; the shipped connector code is not imported or changed.

Usage (no Docker required):

    python3 tasks/task-0049/verifier/fixtures/generate_fixtures.py
    python3 tasks/task-0049/verifier/fixtures/generate_fixtures.py --write

Without ``--write`` this only cross-checks the already-shipped fixtures
(cp0/cp2/cp3/summit) against both generation paths and prints a diff summary
for cp4/cp5/cp6 (computed but not persisted). ``--write`` persists
``placements_cp4.json``, ``placements_cp5.json``, ``placements_cp6.json``.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_DEFAULT_VENDOR_SRC = (
    Path(__file__).resolve().parents[5] / "vendors" / "placemint" / "src"
)
VENDOR_SRC = Path(os.environ.get("PLACEMINT_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))

FIXTURES_DIR = Path(__file__).resolve().parent
SEED = 5090
DATASET_SIZE = 200  # matches docker-compose.yaml's vendor.environment.DATASET_SIZE for this task
CLIENT_ID = "pm-test-client-id"
CLIENT_SECRET = "pm-test-client-secret"

# checkpoint -> (fixture filename, already shipped?)
_CHECKPOINT_FIXTURES: dict[int, tuple[str, bool]] = {
    0: ("placements_cp0.json", True),
    2: ("placements_cp2.json", True),
    3: ("placements_cp3.json", True),
    4: ("placements_cp4.json", False),
    5: ("placements_cp5.json", False),
    6: ("placements_cp6.json", False),
    7: ("placements_summit.json", True),
}


def _canonical(record: dict[str, Any]) -> dict[str, Any]:
    """Mirrors repo/src/placemint_summit/store.py::canonical_row exactly."""
    return {
        "source_id": record.get("source_id") or record["id"],
        "data": {k: v for k, v in record.items() if k != "source_id"},
        "is_deleted": bool(record.get("is_deleted", False)),
        "updated_at": str(record.get("updated_at", "")),
    }


def _canonical_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = sorted(records, key=lambda r: r["id"])
    return [_canonical(r) for r in records]


# ---------------------------------------------------------------------------
# Path 1: pure function, no process at all.
# ---------------------------------------------------------------------------

def _pure(checkpoint: int) -> list[dict[str, Any]]:
    sys.path.insert(0, str(VENDOR_SRC))
    from placemint import state  # noqa: E402

    app_state = state.build_state(seed=SEED, checkpoint=checkpoint, placement_count=DATASET_SIZE)
    return _canonical_rows(list(app_state["placements"].values()))


# ---------------------------------------------------------------------------
# Path 2: real placemint.main:app, in-process, ephemeral port (no Docker).
# ---------------------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _request(method: str, url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class _EphemeralPlacemint:
    """Boots placemint.main:app in-process via uvicorn, bound to an ephemeral
    localhost port. No subprocess, no Docker -- a fresh module import per
    checkpoint (each checkpoint needs its own `APP_STATE`, which main.py only
    computes once at import time), so each instance runs in its own
    interpreter via ``multiprocessing`` to get a clean import.
    """

    def __init__(self, checkpoint: int) -> None:
        self.checkpoint = checkpoint
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._proc = None

    def __enter__(self) -> "_EphemeralPlacemint":
        import multiprocessing

        env_overrides = {
            "CHECKPOINT": str(self.checkpoint),
            "VENDOR_SEED": str(SEED),
            "DATASET_SIZE": str(DATASET_SIZE),
            "PM_CLIENT_ID": CLIENT_ID,
            "PM_CLIENT_SECRET": CLIENT_SECRET,
            "PM_WEBHOOK_SECRET": "pm-fixture-gen-secret",
            "PORT": str(self.port),
            "WEBHOOK_TARGET": "",
            "REQUEST_LOG_PATH": f"/tmp/pm-fixturegen-{self.port}-requests.jsonl",
            "TOKEN_LOG_PATH": f"/tmp/pm-fixturegen-{self.port}-tokens.jsonl",
        }
        ctx = multiprocessing.get_context("spawn")
        self._proc = ctx.Process(target=_run_server, args=(str(VENDOR_SRC), env_overrides), daemon=True)
        self._proc.start()
        self._wait_ready()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc.join(timeout=5)

    def _wait_ready(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                code, _ = _request("GET", self.base + "/")
                if code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"in-process placemint (checkpoint={self.checkpoint}) never became ready")

    def token(self) -> str:
        body = urllib.parse.urlencode(
            {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
        ).encode()
        code, resp = _request(
            "POST", self.base + "/oauth/token", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert code == 200, f"token mint failed: {code} {resp!r}"
        return json.loads(resp)["access_token"]

    def paginate_all(self, token: str, kind: str = "placements") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        limit = 100
        hops = 0
        while True:
            hops += 1
            url = f"{self.base}/api/{kind}?offset={offset}&limit={limit}"
            code, resp = _request("GET", url, headers={"Authorization": f"Bearer {token}"})
            assert code == 200, f"page fetch failed at offset={offset}: {code} {resp!r}"
            page = json.loads(resp)
            items.extend(page["data"])
            offset += limit
            if offset >= page["total"]:
                break
            if hops > 500:
                raise RuntimeError("pagination did not terminate (page efficiency bug?)")
        return items


def _run_server(vendor_src: str, env_overrides: dict[str, str]) -> None:
    """Child-process entry point: apply env, import the app fresh, serve it."""
    import os as _os
    import sys as _sys

    _os.environ.update(env_overrides)
    _sys.path.insert(0, vendor_src)
    import uvicorn

    from placemint.main import app  # noqa: E402  (import AFTER env is set)

    uvicorn.run(app, host="127.0.0.1", port=int(env_overrides["PORT"]), log_level="warning")


def _via_http(checkpoint: int) -> list[dict[str, Any]]:
    with _EphemeralPlacemint(checkpoint) as vendor:
        token = vendor.token()
        raw = vendor.paginate_all(token, "placements")
    return _canonical_rows(raw)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _load_shipped(name: str) -> list[dict[str, Any]] | None:
    path = FIXTURES_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _diff_summary(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> str:
    if a == b:
        return "IDENTICAL"
    ids_a = {r["source_id"] for r in a}
    ids_b = {r["source_id"] for r in b}
    only_a, only_b = ids_a - ids_b, ids_b - ids_a
    mismatched = [
        r["source_id"]
        for r in a
        if r["source_id"] in ids_b and r != next(x for x in b if x["source_id"] == r["source_id"])
    ]
    return (
        f"DIFFERS: len_a={len(a)} len_b={len(b)} only_in_a={sorted(only_a)[:5]} "
        f"only_in_b={sorted(only_b)[:5]} field_mismatches={mismatched[:5]}"
    )


def main() -> int:
    write = "--write" in sys.argv
    ok = True

    for checkpoint, (fname, shipped) in sorted(_CHECKPOINT_FIXTURES.items()):
        pure_rows = _pure(checkpoint)
        http_rows = _via_http(checkpoint)
        cross = _diff_summary(pure_rows, http_rows)
        print(f"checkpoint={checkpoint} ({fname}): pure vs in-process-HTTP -> {cross}")
        if cross != "IDENTICAL":
            ok = False

        if shipped:
            existing = _load_shipped(fname)
            if existing is None:
                print(f"  ! {fname} not found on disk (expected pre-existing)")
                ok = False
            else:
                shipped_cross = _diff_summary(pure_rows, existing)
                print(f"  vs shipped {fname}: {shipped_cross}")
                if shipped_cross != "IDENTICAL":
                    ok = False
        else:
            print(f"  {len(pure_rows)} rows computed for {fname} (new fixture)")
            if write:
                out_path = FIXTURES_DIR / fname
                out_path.write_text(json.dumps(pure_rows, indent=2), encoding="utf-8")
                print(f"  -> wrote {out_path}")

    print("\nALL CROSS-CHECKS PASSED" if ok else "\nCROSS-CHECK FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
