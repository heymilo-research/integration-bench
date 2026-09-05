# VENDORED COPY -- do not edit here.
#
# Canonical source: tools/rework/webhook_family.py
# Refresh with:     python3 tools/rework/sync_family_lib.py task-0017
#
# The grading workspace copies only the task directory, so a scenario cannot
# import from tools/. Edit the canonical file and re-run the sync instead of
# patching this copy -- drift between the two is a suite-lint failure.

"""Reusable listener plumbing and invariants for the webhook mechanic family.

Unlocks the webhook-surface tasks, which are the bulk of what is left on
talentloop and appear on most other vendors too. The plumbing here is the
convention the validated 50-task tree already uses (see public task-0012's
`_scenario_util.py`); this is that convention factored out so each task does not
re-derive it, plus the invariants.

How the listener works, because none of it is guessable:

- The vendor POSTs deliveries to the URL in its `WEBHOOK_TARGET` env, which points
  at `http://connector:4000/...`. The app compose service carries `connector` as a
  network ALIAS so that name resolves; the service itself is still called `app`.
- The app image's default command runs the listener. One-shot subcommands go
  through `docker compose run app <cmd>` as usual, so a scenario brings the
  service UP only while it wants deliveries consumed, then stops it.
- Readiness cannot be probed from the verifier host, which has no route to
  `connector`. It is probed from INSIDE the vendor container instead.
- Every vendor boot TRUNCATES `webhook_deliveries.jsonl`, and the dispatcher
  queues only the half-open window `(checkpoint-1, checkpoint]` for that boot --
  never cumulatively. So walking several checkpoints means recreating once per
  step and draining after each one; a single jump to the final checkpoint silently
  delivers only that step's events. This is the same truncation trap as the
  request log, and it is why `drain_checkpoint_events` exists.

Verified wire contract (measured 2026-08-04 against talentloop:local with a probe
listener aliased `connector` on a shared docker network -- none of this is in the
vendor.yaml, and the shipped docs get one part of it wrong):

- Signature header is `X-TL-Signature`, a BARE lowercase hex HMAC-SHA256. No
  `sha256=` prefix.
- The signed payload is `f"{timestamp}.{raw_body}"`, where timestamp is the
  `X-TL-Timestamp` header value. Signing the body alone does not verify.
- `X-TL-Timestamp` is a WALL-CLOCK epoch (seconds), not the vendor's seeded
  BASE_EPOCH. It moves with real time, so it cannot be asserted against a fixture.
- The seeded "tamper" is a clock-skew attack, not a corrupted body: the signature
  is zeroed AND the timestamp is shifted 100000s into the past, well beyond
  webhooks.signing.max_skew_s (300). A listener that verifies the HMAC but ignores
  the timestamp skew still accepts it -- so rejecting it requires BOTH checks.
- The event payload's `occurred_at` is epoch MILLISECONDS (13 digits) while the
  docs describe ISO 8601 (talentloop LIES.md lie 2).
- Delivery log rows carry: event_id, event, entity_id, attempt, target,
  status_code, response_code, skew_s, `duplicate` and `tampered`. Prefer the
  explicit `duplicate` flag over counting event_id occurrences.
- Duplicate delivery is seeded ON by default, so a re-delivery arrives without any
  fault being configured.

The invariants:

  H1 delivered and acked   every expected event reached the listener with a 2xx
  H2 tamper rejected       a tampered delivery was NOT acked
  H3 duplicates collapse   a re-delivered event was applied once
  H4 poll-only observed    changes that emit no event were still picked up
  H5 drops reconciled      an event the vendor never sent was recovered by polling
  H6 evidence exists       deliveries actually happened, so H1-H5 mean something
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Sequence

APP_SERVICE = "app"
LISTENER_ALIAS = "connector"
LISTENER_PORT = 4000

_DRAIN_TIMEOUT_S = 90.0
_DRAIN_POLL_S = 1.0
# After the expected set is acked, give the listener a moment to finish writing
# whatever it does with the last delivery before the scenario reads its output.
_SETTLE_S = 2.0


def _stack(ctx) -> Any:
    """The ComposeStack behind ctx, via the vendor handle.

    Private-ish by necessity: the verifier context exposes no stack of its own,
    and bringing a service up is not something VendorHandle covers.
    """
    return ctx.vendor(ctx.vendor_metadata.name)._stack


def serve_start(ctx, *, vendor: str, timeout_s: float = 60.0) -> None:
    stack = ctx.vendor(vendor)._stack
    stack.up(service=APP_SERVICE, force_recreate=True)
    wait_listener(ctx, vendor=vendor, timeout_s=timeout_s)


def serve_stop(ctx, *, vendor: str) -> None:
    stack = ctx.vendor(vendor)._stack
    try:
        stack.stop_service(APP_SERVICE)
    except Exception:
        # A listener that is already down is the state we wanted.
        pass


def wait_listener(ctx, *, vendor: str, timeout_s: float = 60.0) -> bool:
    """Block until the vendor container can reach the listener.

    Probed from inside the vendor rather than from the verifier host, which has no
    route to the `connector` alias.

    The compose SERVICE name is taken from the vendor handle, not hardcoded. The
    validated 50-task tree names its vendor service `vendor`, but a task may name it
    after the product (`talentloop`, `hirewire`), and exec-ing a service that does
    not exist fails silently forever: the probe never succeeds, every step burns its
    full timeout, and readiness is never actually confirmed. Measured on task-0114's
    first real run before this was fixed.
    """
    handle = ctx.vendor(vendor)
    stack = handle._stack
    service = getattr(handle, "_service", None) or vendor
    deadline = time.monotonic() + timeout_s
    script = (
        "import urllib.request,sys;"
        f"sys.exit(0 if urllib.request.urlopen('http://{LISTENER_ALIAS}:{LISTENER_PORT}/',"
        "timeout=2).status==200 else 1)"
    )
    while time.monotonic() < deadline:
        probe = stack.exec(service, "python", "-c", script, check=False)
        if getattr(probe, "returncode", 1) == 0:
            return True
        time.sleep(0.5)
    return False


def _is_2xx(code: Any) -> bool:
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


def acked_event_ids(deliveries: Sequence[dict[str, Any]]) -> set[str]:
    return {str(d.get("event_id")) for d in deliveries if _is_2xx(d.get("status_code"))}


def drain_webhooks(
    ctx,
    *,
    vendor: str,
    expect_events: Iterable[str],
    timeout_s: float = _DRAIN_TIMEOUT_S,
) -> tuple[bool, list[dict[str, Any]]]:
    """Wait until every expected event id has a 2xx ack. Returns (ok, deliveries)."""
    want = {str(e) for e in expect_events}
    handle = ctx.vendor(vendor)
    deadline = time.monotonic() + timeout_s
    deliveries: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        deliveries = handle.webhook_deliveries()
        if want.issubset(acked_event_ids(deliveries)):
            time.sleep(_SETTLE_S)
            return True, handle.webhook_deliveries()
        time.sleep(_DRAIN_POLL_S)
    return False, deliveries


def drain_checkpoint_events(
    ctx,
    steps: Sequence[tuple[int, Iterable[str]]],
    *,
    vendor: str,
    env: dict[str, str] | None = None,
    timeout_s: float = _DRAIN_TIMEOUT_S,
) -> tuple[bool, list[dict[str, Any]]]:
    """Walk `(checkpoint, expected_event_ids)` steps IN ORDER, draining each.

    One recreate per step is mandatory, not tidiness: the dispatcher queues only
    the half-open window `(checkpoint-1, checkpoint]` on a given boot, and each
    boot truncates the delivery log. Jumping straight to the last checkpoint
    delivers only its own events and silently loses every earlier step's.

    Returns (all steps drained, deliveries accumulated across steps).
    """
    all_ok = True
    accumulated: list[dict[str, Any]] = []
    for checkpoint, expected in steps:
        ctx.vendor(vendor).recreate(checkpoint=checkpoint, env=env)
        wait_listener(ctx, vendor=vendor)
        ok, deliveries = drain_webhooks(
            ctx, vendor=vendor, expect_events=expected, timeout_s=timeout_s
        )
        all_ok = all_ok and ok
        accumulated += deliveries
    return all_ok, accumulated


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def h6_deliveries_observed(
    deliveries: Sequence[dict[str, Any]], *, minimum: int = 1
) -> tuple[bool, str]:
    """Deliveries actually happened.

    Without this, every other invariant here is vacuously true on a run where the
    listener never came up: no deliveries means no failed acks, no tampered
    deliveries and no duplicates. Same no-evidence-is-not-compliance rule as the
    other families.
    """
    return (
        len(deliveries) >= minimum,
        f"{len(deliveries)} delivery attempt(s) recorded, need >={minimum} for the "
        "webhook invariants to be measurable at all",
    )


def h1_expected_events_acked(
    deliveries: Sequence[dict[str, Any]], expected: Iterable[str]
) -> tuple[bool, str]:
    want = {str(e) for e in expected}
    acked = acked_event_ids(deliveries)
    missing = sorted(want - acked)
    return (
        not missing,
        f"event(s) never acked with a 2xx: {missing[:6]} "
        f"({len(acked)} acked of {len(want)} expected)",
    )


def h2_tampered_rejected(
    deliveries: Sequence[dict[str, Any]], *, listener_acked: bool
) -> tuple[bool, str]:
    """A delivery the vendor tampered with must not have been accepted.

    Requires the vendor to have injected one; a run with no tampered delivery
    cannot judge signature verification and says so rather than passing.

    `listener_acked` is not optional and has no default: a listener that never
    served anything acks the forged deliveries too, which is how a stub that
    serves no one used to bank this check — "0 of 6 tampered deliveries was
    acked" is true of a listener that acked nothing at all. Rejecting a forgery
    is only a claim about signature verification if something was verified.
    Callers pass the evidence that the listener answered at least one delivery.
    """
    if not listener_acked:
        return False, (
            "the listener never acked any delivery with a 2xx — refusing the "
            "forged one is not evidence of signature verification when nothing "
            "was verified"
        )
    tampered = [d for d in deliveries if d.get("tampered")]
    if not tampered:
        return False, "no tampered delivery was injected — signature handling is unmeasurable"
    accepted = [d for d in tampered if _is_2xx(d.get("status_code"))]
    return (
        not accepted,
        f"{len(accepted)} of {len(tampered)} tampered delivery/deliveries was acked with a 2xx",
    )


def h3_duplicates_collapsed(
    deliveries: Sequence[dict[str, Any]],
    applied_counts: dict[str, int],
    *,
    listener_acked: bool,
) -> tuple[bool, str]:
    """A re-delivered event was applied once.

    `applied_counts` maps event id -> how many times the submission's own output
    shows it applied, which is the only place double application is visible.

    `listener_acked` is not optional and has no default: a listener that never
    served anything applied the re-delivery zero times, same as one time, same
    as any other count — collapsing duplicates is not observable in a connector
    that never applied a single event. Callers pass the evidence that the
    listener answered at least one delivery.
    """
    if not listener_acked:
        return False, (
            "the listener never acked any delivery with a 2xx — there is no "
            "applied event here to judge for duplication"
        )
    # The vendor flags a re-delivery explicitly; fall back to counting only if a
    # vendor does not.
    flagged = {str(d.get("event_id")) for d in deliveries if d.get("duplicate")}
    if flagged:
        redelivered = flagged
    else:
        seen: dict[str, int] = {}
        for delivery in deliveries:
            key = str(delivery.get("event_id"))
            seen[key] = seen.get(key, 0) + 1
        redelivered = {k for k, n in seen.items() if n > 1}
    if not redelivered:
        return False, "no event was delivered more than once — dedup is unmeasurable"
    doubled = {k: applied_counts.get(k, 0) for k in sorted(redelivered) if applied_counts.get(k, 0) > 1}
    return (
        not doubled,
        f"re-delivered event(s) applied more than once: {doubled}",
    )


def h4_poll_only_change_observed(
    held: dict[str, Any], *, entity: str, field: str, expected: Any
) -> tuple[bool, str]:
    """A change that emits NO event was still picked up.

    Selective subscription means some entities never produce a webhook at all, so
    the only way to see their changes is to poll. A connector that treats the
    event stream as the whole story misses them permanently and silently.
    """
    got = (held or {}).get(field)
    return (
        got == expected,
        f"{entity}.{field}={got!r}, expected {expected!r} — this entity emits no "
        "webhook, so polling is the only way it is ever observed",
    )


def h5_dropped_event_reconciled(
    held: dict[str, Any], *, entity: str, field: str, expected: Any, dropped_event: str
) -> tuple[bool, str]:
    got = (held or {}).get(field)
    return (
        got == expected,
        f"{entity}.{field}={got!r}, expected {expected!r} after event {dropped_event} "
        "was never delivered — a reconcile pass has to catch what the stream lost",
    )
