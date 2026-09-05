# Build the weekly capability index

**From:** Integrations / Research Tooling
**Vendor:** SourceWell
**Surface:** polling
**Category:** build · **Track:** python · **Tier:** 3

## Context

Brightwater's researchers spend their Monday morning answering the same
question — "who have we got who can do X?" — and today they answer it by
grepping a spreadsheet somebody exports by hand. We want the index built for
them, weekly, from SourceWell plus the skills feed we buy from Ledgerfield
Analytics.

SourceWell is the system of record for who exists and for the skill
vocabulary. Ledgerfield's feed is the system of record for who can do what,
and nothing else: it is a classifier's output, dropped into
`input/ledgerfield_skills.json` every Sunday night. Their own format note is
in `docs/ledgerfield-skills-extract.md` — it is Ledgerfield's document about
Ledgerfield's file, and they last revised it in 2025.

The index goes out to the whole research desk, so a skill nobody can be found
under is a week of people not being contacted.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The test harness runs your code exactly as follows — this command is the
contract:

```bash
python -m skill_index
# -> writes the output artifacts listed below
```

The harness invokes that command more than once against the same mounted
output directory, the way the Sunday scheduler does.

1. One entry per skill SourceWell holds. Each carries the `name` SourceWell
   holds for it now and a `status` of `live`, or `retired` where SourceWell
   has retired it. Retired skills stay in the index — the desk still wants to
   know who used to be findable under them.
2. Each skill entry lists the `sw_id` of every person who carries that skill,
   ordered by id, and a `member_count`. A skill nobody carries still gets an
   entry, with an empty list.
3. One entry per person SourceWell holds and has not retired, carrying their
   `nm`, every skill they carry ordered by tag id, and a `skill_count`. People
   SourceWell has retired are not published, in either section.
4. `counts` reports `skills_live`, `skills_retired`, `skills_without_members`
   and `people_published`.
5. We pay SourceWell per call, so keep each pass economical.

Ledgerfield's feed is the only source for who carries what. Everything else —
who exists, what the skills are called, which of them are still in use — comes
from SourceWell.

## Output artifacts

- `output/capability_index.json` — `{"skills": [...], "people": [...],
  "counts": {...}}`. Each `skills` entry carries `tag_id`, `name`, `status`,
  `members` and `member_count`, ordered by `tag_id`. Each `people` entry
  carries `sw_id`, `nm`, `skills` and `skill_count`, ordered by `sw_id`.
- `output/skills_report.csv` — header row `tag_id,name,status,member_count`
  plus one line per skill, empty string where a value does not apply.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://sourcewell:8000`) |
| `OUTPUT_DIR` | Directory where output artifacts land (defaults to `./output`) |
| `SW_API_KEY` | Vendor credential injected by the test harness |

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

Every skill SourceWell holds is in the index under the name SourceWell uses
for it, every published person's skills are all of the skills Ledgerfield
credits them with, the report reconciles with the index, and next week's pass
costs no more than this one.
