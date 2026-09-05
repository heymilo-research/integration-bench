# Attachments

An attachment is a file hanging off a **note**. There is no way to attach a file
to a candidate, a job or an application directly: create the note first, then
upload the file against the note you got back.

That makes every attached document a two-call write.

```
POST /candidates/{candidate_id}/notes     -> the note
POST /notes/{note_id}/attachments         -> the file
```

## Upload

```
POST /notes/{note_id}/attachments
Content-Type: application/json
Authorization: Bearer <access_token>

{
  "filename": "quarterly-summary.pdf",
  "content_type": "application/pdf",
  "sha256": "0000...0000",
  "content_b64": "JVBERi0xLjUK..."
}
```

| Field | Type | Notes |
|---|---|---|
| `filename` | string | required |
| `content_type` | string | required; see accepted types below |
| `sha256` | string | required; lowercase hex SHA-256 of the **file bytes**, not of the base64 text |
| `content_b64` | string | required; standard base64 of the file bytes |

Response `201 Created`:

```json
{
  "attachment_id": "att_90001",
  "note_id": "note_90001",
  "filename": "quarterly-summary.pdf",
  "size_bytes": 0,
  "state": "stored"
}
```

The `201` is the platform's confirmation that the bytes are persisted, and the
`state` in the response body is the attachment's settled state. Keep the
`attachment_id` — it is the handle for everything else in this document.

## Accepted content types

`application/pdf`, `text/plain`, `image/png`. Anything else is refused.

## Errors

Uploads are validated on the way in, and a rejection is returned inline as a
`422` with the usual `field_errors` body:

```json
{
  "errors": {
    "content_type": ["unsupported_content_type"]
  }
}
```

The reasons the platform returns are:

| Reason | Meaning |
|---|---|
| `unsupported_content_type` | `content_type` is not in the accepted list |
| `checksum_mismatch` | `sha256` does not match the digest of the decoded bytes |
| `malformed_encoding` | `content_b64` is not valid base64 |

`filename` and `content_b64` are required; omitting either is also a `422`.

The parent note must exist: `404 Not Found` for a note id that was never issued,
`410 Gone` for one that was deleted.

## Idempotency

`POST /notes/{note_id}/attachments` accepts the same `Idempotency-Key` header as
the rest of the write surface, with the same one-hour window
(see [writeback.md](writeback.md)). Reuse the key when you are retrying the same
upload after a timeout; use a fresh key for a genuinely different upload, or the
window will hand you back the earlier result.

## Listing a note's attachments

```
GET /notes/{note_id}/attachments
```

```json
{
  "data": [
    {
      "attachment_id": "att_90001",
      "note_id": "note_90001",
      "filename": "quarterly-summary.pdf",
      "content_type": "application/pdf",
      "size_bytes": 0,
      "declared_sha256": "0000...0000",
      "sha256": "0000...0000",
      "state": "stored",
      "reason": null,
      "created_at": "2026-03-14T10:00:00Z"
    }
  ],
  "cursor": null
}
```

| Field | Type | Notes |
|---|---|---|
| `attachment_id` | string | `att_NNNNN` |
| `declared_sha256` | string | the digest the uploader sent |
| `sha256` | string | the digest the platform computed over the bytes it received |
| `state` | enum | `stored` or `rejected` |
| `reason` | string or null | null unless `state` is `rejected` |
| `created_at` | string (ISO 8601) | |

Unlike `GET /candidates/{candidate_id}/notes`, this listing is **immediately
consistent** — it is not subject to the 20-second read-after-write lag described
in [writeback.md](writeback.md). It is also never paginated beyond one page in
practice; `cursor` follows the usual convention and is `null` on the last page.

`GET /attachments/{attachment_id}` returns a single row in the same shape, or
`404 Not Found`.
