# Candidate status batches

Set absolute candidate stages with:

```http
POST /v1/candidates/status-batch
Content-Type: application/json
X-GH-Key: <key>

{
  "updates": [
    {"candidate_id": "cand_00123", "pipeline_stage": "interview"}
  ]
}
```

A successful response is `200` with an `updated` array containing the current
wire records for every accepted item. The endpoint validates the whole request
before applying it: an invalid candidate or stage returns `422` and applies
nothing from that request.

The gateway can reject a request that exceeds its current item capacity with:

```http
HTTP/1.1 413 Payload Too Large
Content-Type: application/json

{"error":"payload_too_large","max_items":4}
```

The refused request applies no updates. `max_items` is response data, not a
permanent tenant setting, and every later request has its own ordinary
200/413/422 outcome.

Stages accepted on the wire are `sourced`, `screening`, `submitted`,
`interview`, `offer`, and `placed`.
