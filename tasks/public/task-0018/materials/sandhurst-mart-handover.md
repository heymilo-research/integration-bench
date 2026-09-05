# Requisition rollup — Data Services handover

**Sandhurst Recruitment / Data Services.** This is our own internal note, not
RecruitOS documentation. Written when Data Services still owned the nightly
rollup; last revised in November, before the mart decommission notice landed.

Whoever picks the rollup up after us: this is everything we knew about it.

## What the job is for

Finance's revenue loader reads one file, `rollup.csv`, every morning at 05:20.
It is the only thing standing between the ATS and the invoice run, and it has
been produced the same way since 2022 — one line per application, plus a small
`result.json` beside it that the on-call dashboard scrapes.

The file itself was never ours. RecruitOS's **Reporting Mart** — the nightly
extract product on their old pricing sheet — dropped it into
`/srv/feeds/recruitos/` at 02:00 and our job did little more than copy it
across, rename two columns and hand it to the loader. The mart is the piece
being switched off. The rest of the pipeline downstream of `rollup.csv` is
staying exactly as it is; Finance have been very clear that the file's shape
is not up for negotiation.

## The columns, as the mart produced them

`application_id`, `candidate_id`, `requisition_id`, `stage`, `disposition`,
`last_change_at`. Ids are RecruitOS's own (`app_0123`, `cand_0042`,
`job_0007`), verbatim, no re-keying. `stage` is the raw application stage
string.

### disposition

The mart wrote one of `dropped`, `placed`, `lost`, `frozen`, `working` per
line, in that order of precedence. Two things about that list are worth
knowing before anyone spends a week on it.

**`frozen` never fired.** It is a defensive branch and always was. In
RecruitOS an application sitting against a requisition that is no longer open
has already been resolved one way or the other — it is `hired` or it is
`rejected`, because that is how the recruiters close a requisition out. In
four years of drops we never saw a line come out `frozen`. The mart derived
the disposition from **the application's own stage and nothing else**, and we
never once had to look at the requisition ledger to produce this file.

**`dropped` never fired either**, for a simpler reason: RecruitOS's list
endpoints only ever hand you live records. Retired candidates and retired
requisitions are gone from the feed the moment someone retires them, and the
envelope's `total` is the count of live records. So there was never anything
to drop, and we never had to read the candidate or requisition ledgers for
that either. If you find yourself writing a reconciliation pass to work out
what has been retired, stop — the API has already done it for you.

### last_change_at

The instant the line last changed, ISO 8601, straight off RecruitOS.

RecruitOS cascades its timestamps. Touching a requisition bumps `updated_at`
on every application attached to it, and the same is true of a candidate, so
an application's own `updated_at` already accounts for all three records on
the line. We took it as-is. There is nothing to reconcile here and no `max()`
to compute — the platform has done it for you upstream.

## Operational notes that are still true

- One drop per night, and the loader is not idempotent: it reads whatever is
  in `rollup.csv` at 05:20 and stops. Re-running the job before 05:20 is
  fine; re-running it after is not, and that is Finance's problem, not ours.
- The rollup covers **every** application in the tenant, not a delta. Finance
  reconcile against the whole book each morning. The mart never sent us a
  partial file and the loader would reject one that had fewer lines than
  yesterday's.
- Ids are stable once assigned. Nothing in the pipeline re-keys them.
- `result.json` is only read by the on-call dashboard. Nobody has ever
  complained about it, so nobody has ever changed it.
- RecruitOS's sandbox and production credentials are different clients but
  the same API shape. The tokens last an hour; the client library in
  `client.py` re-mints when it needs to and has never given us trouble.

## Things we never got round to

- The mart's own runbook is on their side of the fence and we never had a
  copy. Everything above is what we worked out from the drops.
- Nobody here has ever compared the mart's file against the RecruitOS API
  directly. There was never a reason to.
