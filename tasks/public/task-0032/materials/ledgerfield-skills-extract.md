# Ledgerfield skills extract — format note

**Ledgerfield Analytics Ltd** · integration note for Brightwater Search ·
rev 2025-11 · contact: feeds@ledgerfield.example

> This is Ledgerfield's note about Ledgerfield's file. It is not SourceWell
> documentation — SourceWell publish their own page, see `docs/index.md`.

## What the extract is

We read your SourceWell tenant, run our skill classifier over each profile's
public material, and hand you back one JSON document a week. It is a flat
document with a `records` array; each record is one person.

```json
{
  "sw_id": "prf_XXXX",
  "surname": "<surname>",
  "tags": ["tag_XXXX", "tag_XXXX"],
  "tags_total": 0,
  "tags_truncated": false,
  "continues": false
}
```

`sw_id` is SourceWell's own profile id — we do not mint identifiers of our
own, so it joins straight onto your roster.

## `tags_truncated` and `tags_total`

Both fields are **informational**. `tags_total` is the size of the skill list
we computed, and `tags_truncated` is a hint that the list came out long enough
to be worth a second look by a human. Neither changes what is in the file:
the `tags` array on a record is that person's skill list.

`chunk_size` in the document header is our exporter's internal write width. It
is metadata about how the file was produced, not about its contents, and you
can ignore it.

**There are no continuation records.** Version 2 of the exporter used to split
a long skill list across several records carrying `continues: true`, and it
caused every downstream consumer we had a support ticket from. We removed that
behaviour in v3 (March 2025) and the `continues` field is retained only so old
parsers do not blow up on a missing key. You will not see a second record for
the same `sw_id`.

## The vocabulary block

The document header carries a `vocabulary` map — `tag_id` to the skill's name.
We re-read it from your SourceWell tenant immediately before we cut the file,
so it is the current name for every skill, and it is the complete set. There
is nothing to be gained from crawling SourceWell's tag collection yourself:
the vocabulary block already is that collection, and it will only cost you
requests you are paying for.

## Cadence and support

Cut every Sunday 02:00 UTC, dropped into your bucket by 03:00. Reruns on
request; we keep four weeks.
