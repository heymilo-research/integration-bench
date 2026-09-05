# Placemint connector: webhooks, polling, and writeback for a high-volume tenant

**From:** Integrations / Customer Success Engineering
**Vendor:** Placemint (placement marketplace)
**Surface:** webhooks (push), polling (pull), writeback (push)

## Context

A high-volume tenant runs all three Placemint surfaces at once: it streams
placement, client, and note changes to us over webhooks, we also poll
Placemint directly, and we periodically push writeback (notes and
placement updates) into the tenant.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Placemint sandbox base URL (e.g. `http://vendor:8000`) |
| `PM_CLIENT_ID` / `PM_CLIENT_SECRET` | OAuth2 client credentials |
| `PM_WEBHOOK_SECRET` | HMAC secret for verifying inbound webhook deliveries |
| `SERVE_HOST` / `SERVE_PORT` | webhook listener bind address (default `0.0.0.0:4000`) |
| `OUTPUT_DIR` | where output files go |
| `STATE_PATH` | where the durable canonical store snapshot lives |
| `POLL_INTERVAL_S` | cadence of the background poll-reconciliation loop |

## Run contract

```bash
python -m placemint_summit serve

# Writeback push
python -m placemint_summit writeback

python -m placemint_summit dump
```

## Output format

### `placements.json`

JSON array of canonical rows sorted by placement id: `source_id`, `data`
(all fields minus `source_id`), `is_deleted`, `updated_at`. Don't change the
shape.

### `writeback_result.json`

```json
{
  "writes": [
    {"client_ref": "w-1", "ok": true, "kind": "note", "record": { ...the created note... }},
    {"client_ref": "w-2", "ok": true, "kind": "placement_update", "record": { ...the updated placement... }},
    {"client_ref": "w-3", "ok": false, "error": {"status": 422, "field_errors": {"body": ["is required"]}}}
  ]
}
```

- `writes` is sorted by `client_ref`.
- A rejected write carries the vendor's status and `field_errors` under
  `error`, and has no `record` key.

## Engineering expectations

This ships to production, so the usual bar applies:

- Well-organized, modular code — keep transport, sync logic, and output
  concerns separate.
- Tests, runnable with `pytest`, covering the core logic you add or change.
- Be a good API citizen: handle the vendor's real-world behavior the way a
  production integration must, and don't hammer endpoints in retry loops.
  Our platform team audits vendor request logs — sloppy conduct gets
  flagged even when the data lands correctly.

## Done when

All invocations exit 0 and the final canonical store matches the
tenant's actual upstream state exactly, in the canonical shapes above, with
the writeback contract holding throughout: each staged write lands exactly
once per `client_ref`, and a malformed item is reported as a failure — never
a crash, never a duplicate on retry.
