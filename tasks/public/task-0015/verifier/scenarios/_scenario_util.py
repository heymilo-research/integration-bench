"""Shared helpers for the task-0015 (StaffLine bulk import) scenarios.

Recreating the vendor with this task's two bulk-quirk env toggles
(``SL_LYING_REF`` / ``SL_RAW_LAG_REQS``) needs more than
``VendorHandle.recreate()`` sets (CHECKPOINT only), so ``recreate_vendor``
below reaches into the same ``ComposeStack`` override-file mechanism that
method itself uses (see ``bench.verifier.context.VendorHandle.recreate``) and
adds the extra keys. Nothing here mutates the harness — it only uses the
stack objects the harness hands the scenario, the same way task-0004's and
task-0006's ``_scenario_util.py`` reach into ``ctx.app._stack``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from typing import Any

from bench.verifier.io import read_json_output

SL_APP_TOKEN = "sl-test-app-token"
SL_HMAC_SECRET = "sl-test-hmac-secret"


# ---------------------------------------------------------------------------
# Vendor lifecycle
# ---------------------------------------------------------------------------

def recreate_vendor(
    ctx,
    *,
    checkpoint: int = 0,
    sl_lying_ref: str | None = None,
    sl_raw_lag_reqs: int | None = None,
) -> None:
    """Recreate the staffline vendor at ``checkpoint`` with SL_BULK_ENABLED=1
    plus this scenario's SL_LYING_REF / SL_RAW_LAG_REQS (unset when not
    given, matching "off by default" for bulk_ingest_mixed_results and
    retry_and_dedupe)."""
    handle = ctx.vendor("staffline")
    stack = handle._stack
    # Compose-unit indexes vendor_envs by the declared vendor block, which is
    # not guaranteed to equal VendorHandle._service (product/service alias).
    # Assigning through vendor_env resolves the primary block and is the same
    # portable path used by the other repaired scenario helpers.
    env = dict(stack.vendor_env)
    env["CHECKPOINT"] = str(checkpoint)
    env["SL_BULK_ENABLED"] = "1"
    if sl_lying_ref is not None:
        env["SL_LYING_REF"] = sl_lying_ref
    else:
        env.pop("SL_LYING_REF", None)
    if sl_raw_lag_reqs is not None:
        env["SL_RAW_LAG_REQS"] = str(sl_raw_lag_reqs)
    else:
        env.pop("SL_RAW_LAG_REQS", None)
    stack.vendor_env = env
    handle.recreate(checkpoint=checkpoint)


def reset_store(ctx) -> None:
    """Drop the canonical sqlite DB so each scenario starts empty.

    Scenarios share one DB file on the ``canonical-data`` volume for the whole
    grade; without this, tombstones/watermarks from an earlier scenario leak.
    """
    from bench.canonical_sqlite import reset_canonical_on_stack

    reset_canonical_on_stack(ctx.app._stack)


# ---------------------------------------------------------------------------
# Output inspection
# ---------------------------------------------------------------------------

def read_result(ctx) -> dict[str, Any] | None:
    out = ctx.output_dir / "bulk_result.json"
    return read_json_output(out, timeout_s=15.0)


def clear_result(ctx) -> None:
    out = ctx.output_dir / "bulk_result.json"
    try:
        out.unlink()
    except OSError:
        pass


def load_fixture(ctx, name: str) -> Any:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Request-log analysis
# ---------------------------------------------------------------------------

def bulk_post_entries(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e for e in request_log
        if e.get("method") == "POST" and e.get("path") == "/svc/candidates/bulk"
    ]


def bulk_payload_refs(entry: dict[str, Any]) -> list[str]:
    body = entry.get("body")
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []
    return [i.get("client_ref") for i in items if isinstance(i, dict) and i.get("client_ref")]


def bulk_payload_fields(entry: dict[str, Any], client_ref: str) -> dict[str, Any] | None:
    """The submitted candidate fields (fname/lname/email/phone/cand_status)
    for one client_ref within one bulk POST payload, or None if that ref
    wasn't in this submission."""
    body = entry.get("body")
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("client_ref") == client_ref:
            return {k: v for k, v in item.items() if k != "client_ref"}
    return None


def get_by_id_entries(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """GET /svc/candidates/{id} reads -- excludes the plain list endpoint
    (no trailing id) and the (unrelated, never used by this task) export
    stub."""
    out = []
    for e in request_log:
        if e.get("method") != "GET":
            continue
        path = e.get("path", "")
        if not path.startswith("/svc/candidates/"):
            continue
        if path == "/svc/candidates/bulk_export":
            continue
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# Verifier-side signed GET (independent of the connector under test)
# ---------------------------------------------------------------------------

def _sign(ts: str, body: bytes) -> str:
    msg = ts.encode("utf-8") + b"." + body
    return hmac.new(SL_HMAC_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _signed_get(ctx, path: str) -> tuple[int, Any]:
    base = ctx.vendor("staffline").base_url
    ts = str(int(time.time()))
    headers = {
        "X-SL-Token": SL_APP_TOKEN,
        "X-SL-Timestamp": ts,
        "X-SL-Signature": _sign(ts, b""),
    }
    req = urllib.request.Request(f"{base}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as err:
        raw, status = err.read(), err.code
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw.decode("utf-8", errors="replace")


# client_ref -> {fname, lname} for every item in repo/input/candidate_batch.json
# (mirrored here, not read from the repo, since the verifier never depends on
# repo/ content -- see gold outline). Used for content-match cross-checks
# against the live vendor, independent of any id the connector may or may not
# have recorded.
BATCH_FIELDS: dict[str, dict[str, str]] = {
    "batch-0001": {"fname": "Nora", "lname": "Calder"},
    "batch-0004": {"fname": "Grace", "lname": "Okafor"},
    "batch-0005": {"fname": "Marcus", "lname": "Webb"},
    "batch-0006": {"fname": "Elin", "lname": "Sorensen"},
    "batch-0007": {"fname": "Devon", "lname": "Achebe"},
    "batch-0009": {"fname": "Tomasz", "lname": "Wrenn"},
    "batch-0010": {"fname": "Sable", "lname": "Quintana"},
}


def candidate_exists_by_fields(crawl: list[dict[str, Any]], fname: str, lname: str) -> bool:
    return any(c.get("fname") == fname and c.get("lname") == lname for c in crawl)


def vendor_crawl_candidates(ctx) -> list[dict[str, Any]]:
    """Signed crawl of the FULL candidate list, issued directly by the
    verifier, not routed through the connector under test -- used to
    independently confirm a fabricated record truly never landed, by content
    (fname/lname/email), rather than by an id whose exact value depends on
    the connector's own (also-correct) submission order, and rather than
    trusting the connector's own account of it."""
    out: list[dict[str, Any]] = []
    start = 0
    while True:
        status, body = _signed_get(ctx, f"/svc/candidates?start={start}&count=50")
        if status != 200 or not isinstance(body, dict):
            break
        rows = body.get("rows", [])
        out.extend(rows)
        if not body.get("more"):
            break
        start += 50
    return out
