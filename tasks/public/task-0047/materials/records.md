# Record reads

Authenticate every data request with:

```http
X-GH-Key: <key>
```

The bounded record endpoints are:

```text
GET /v1/candidates/{candidate_id}
GET /v1/placements/{placement_id}
GET /v1/agencies/{agency_id}
```

A missing record returns `404`. Other non-2xx responses are failures.

Candidate objects contain `id`, `first_name`, `last_name`, `email`, `status`,
`is_deleted`, `created_at`, and `modified_at`. Placement objects contain `id`,
`candidate_id`, `agency_id`, `placement_state`, `bill_rate`, and
`is_deleted`. Agency objects contain `id`, `name`, `country`, and
`is_deleted`.

Record reads can be served through GlobalHire's HTTP cache. Standard response
headers (`Cache-Control`, `Age`, and `Warning`) describe freshness. A workflow
that requires a current decision may request revalidation with
`Cache-Control: no-cache`; the response to a revalidated read is not reusable
across process invocations.

Credentials belong only in the authentication header. Do not put them in query
parameters, logs, or output files.
