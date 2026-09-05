# Nightly roster census disagrees with CrewCall about who works here

**From:** Integrations / Workforce Analytics
**Vendor:** CrewCall
**Surface:** polling
**Category:** fix · **Track:** python · **Tier:** 3

## Context

Fenmarsh Care Group runs its staffing off CrewCall. Every night at 02:00 our
census job takes a snapshot of the roster and hands the capacity model a line
per crew member plus two breakdowns — one per role, one per status. The model
books agency cover off those breakdowns, so a number being out by three costs
real money in either direction.

The job runs clean every night and has done for a year. Three things came in
over the last fortnight and nobody has connected them yet:

> "The model has been ordering agency drivers all month and we have drivers
> sitting at home. It thinks we are short and we are not."

> "Priya shows up twice in the morning export, same worker id on both lines.
> Payroll noticed, we didn't."

> "Two of the night carers came up as leavers on Tuesday and were back on
> Wednesday. Neither of them has been near a resignation form."

The headline is not the problem: Ops cross-check the total against payroll every
Friday and it has matched every week since the job went in.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The census fixed, so the model can be trusted. Two rules the analytics desk
works to, which are ours and not CrewCall's:

- The census is **one line per crew member the tenant holds**, and every crew
  member the tenant holds gets one. A carer CrewCall has taken off the books is
  still one of the tenant's records: they are carried as `removed`, kept out of
  the active headcount and out of the per-status breakdown, and still counted in
  their role's `removed` column.
- The headline totals and the two breakdowns describe the same set of crew
  members as the census file itself, and that set is what CrewCall actually
  holds for us.

The test harness runs your code exactly as follows — this command is the
contract:

```bash
python -m fenmarsh_census
```

## Output artifacts

- `output/roster_census.csv` — one line per crew member, sorted by `worker_id`,
  columns `worker_id,role,status,standing`. `standing` is `active` or `removed`.
- `output/census_summary.json` — `roster_rows`, `active_headcount`,
  `removed_headcount`, `pages_read`, plus `by_role` (one entry per role, with
  `active` and `removed`) and `by_status` (one entry per status, with
  `headcount`).

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `CC_API_KEY` | Vendor credential injected by the test harness |
| `OUTPUT_DIR` | Directory where output artifacts land (defaults to `./output`) |
| `PAGE_LIMIT` | Page size the roster sweep asks for |

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

The run exits 0, both artifacts are written, and the census describes exactly
the crew members CrewCall holds for us — each of them once.
