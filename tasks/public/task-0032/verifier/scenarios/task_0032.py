"""task-0032 -- truncated_array_flag_handling (SourceWell, build).

Brightwater Search publishes a weekly capability index: for every skill
SourceWell holds, the live people who carry it, and for every live person, the
skills they carry. SourceWell exposes profiles, pools and tags as three
independent collections with no profile-to-skill join, so the membership comes
from a partner extract -- Ledgerfield Analytics' skills feed at
``input/ledgerfield_skills.json`` -- while the skills themselves and the
people themselves come from SourceWell. Two passes against one bind-mounted
OUTPUT_DIR: run 1 fresh, run 2 a verbatim re-run, the way the Sunday scheduler
drives it.

Everything asserted here is grounded in live container observations. The
extract and answer key are emitted together by
``tools/rework/gen_answer_key_0217.py`` after crawling ``sourcewell:local`` at
checkpoints 0 and 62 over public HTTP, so the captured vocabulary and live
world cannot drift apart.

TWO INDEPENDENT DEVICES, PLUS THE WITNESS
-----------------------------------------

**D1 (belief-reality divergence -- TASK-LOCAL,
``docs/ledgerfield-skills-extract.md``, attributed to Ledgerfield Analytics
Ltd rev 2025-11). THE NAMED MECHANIC.** Ledgerfield's exporter caps every
record's ``tags`` array at ``chunk_size`` (6) entries, sets
``tags_truncated: true`` and ``tags_total: <the real number>`` on the capped
record, and appends the remainder as further records for the same ``sw_id``
carrying ``continues: true``. The note denies every part of that: both flags
are "informational ... neither changes what is in the file: the ``tags`` array
on a record is that person's skill list", ``chunk_size`` is "metadata about
how the file was produced, not about its contents", and -- positively and in
bold -- "**There are no continuation records.** ... You will not see a second
record for the same ``sw_id``." The file contradicts it in the plainest way
available: 226 records for 125 people, 78 of them capped, and every capped
record's own ``tags_total`` exceeds the length of its own ``tags`` array. A
connector that believes the note keys a dict on ``sw_id`` and lets the last
record win, so every capped person ends up holding a partial chunk.

The construction refuses to leave the consequence to luck. The generator
ASSERTS, rather than assumes, that (a) every tag in the extract's vocabulary
loses at least one member to a cap -- each tag is given its own dedicated deep
carrier, so no per-tag roster check is decided by a coincidence -- and (b) no
person's last chunk equals their full set, so the last-record-wins reading is
never accidentally right. Measured consequence: all 32 per-skill roster
checks, 22 of the 30 per-person checks, `skills_without_members`, the report
agreement and the re-run check.

**D2 (belief-reality divergence -- TASK-LOCAL, same note, disjoint evidence).
THE VOCABULARY.** The note asserts the extract's ``vocabulary`` block is
"re-read from your SourceWell tenant immediately before we cut the file, so it
is the current name for every skill, and it is the complete set", and tells
the reader outright that "there is nothing to be gained from crawling
SourceWell's tag collection yourself ... it will only cost you requests you
are paying for". SourceWell disagrees on 15 of 32 skills: 10 were renamed
after the capture, 3 have been retired (``is_deleted``), and 2 exist that the
capture never saw and that the block therefore cannot name at all. A
connector that believes the note never issues the tags GET, publishes 10 stale
names, calls 3 retired skills live, and omits 2 skills from the index
entirely. Its evidence is DISJOINT from D1's: the roster checks grade
membership and never a name, the label checks grade the name and status and
never a member.

  DISCLOSED, because the RENAMES are the same wire fact task-0101 leans on.
  0101's device is a name-keyed LOOKUP -- eight pool renames leave four names
  each carried by two open pools, so a dict keyed by name silently keeps the
  last carrier and routes somebody onto the wrong desk. Here nothing is ever
  looked up by name: the index is keyed on the tag id throughout, and the
  consequence is a PUBLISHED LABEL that is out of date, reached by never
  reading the collection at all. Neither task's repair helps the other (0101
  needs a carrier count on a name index, this needs the tags crawl to happen),
  and the retired and never-seen tags -- 5 of the 15 -- are material 0101 does
  not touch.

**D3 (competence, wire-observable, no doc involved).** Nothing reports D1 or
D2. The run exits 0, writes both artifacts, publishes 119 people and a
plausible index, and its ``people_published`` tally is EXACTLY the right one.
The only evidence is per record, and for D2 the only evidence in the request
log is whether the tags collection was read at all -- which is why
``sw217_pass_read_sourcewell_own_skill_collection`` grades that GET rather
than a request count, and why the request budget is graded together with the
cursors SourceWell itself issued. A pass cannot satisfy either by trying
harder.

WHY THIS IS NOT ANOTHER SOURCEWELL TASK. The roster walk here is the given
transport and is graded only as a witness -- no page is composed by hand
(task-0242), no page fails (task-0135), no cursor is invented, and there is no
``since`` (task-0202). Nothing is joined on a handle (task-0066/0135) or on an
id scheme (task-0244), and no record is fetched by id (task-0194). The device
is what a connector does with an array it was handed short and a flag that
said so.

WHAT THE VENDOR'S OWN FOUR LIES DO HERE (honest accounting). None is graded
and none can be. The client takes each collection's href out of the root's
``_links`` block, which already carries ``?view=full`` for profiles, so lie 4
never fires; nothing fetches a record by id, so lie 5 (``200 {}`` for a
missing id) has no path -- and it cannot be made mandatory on a read-only
vendor whose whole collection, soft-deletes included, is crawlable, which is
why it is armed and graded on task-0194 instead; nothing calls ``/x/search``
(lie 6); and the key travels in the header the doc page names (lie 7). All
four are all-or-nothing transport gates, the shape AUTHORING-BRIEF S1 forbids.

FREE MASS IS CAPPED. 119 published people carrying a check each would bury
both devices under boilerplate (AUTHORING-BRIEF S1, "the trap is a rounding
error"), so 30 people carry a per-person check -- 22 whose skill list the
exporter capped and 8 controls it did not -- and the other 89 are covered by
one exact-set check.

MEASURED (own probes, 2026-08-11; see the WORKLOG entry for this task)

    gold   114/114 = 1.000    starter   6/114 = 0.053
    stub     0/114 = 0.000    naive    35/114 = 0.307

    `build` is judged on the floor-share rule (floor <= 0.40*gold): floor
    0.053, headroom 0.947, discriminating 108, naive 0.307 of gold.

    gold   : 32/32 skills, 32/32 rosters, 32/32 labels, 30/30 people, all
             four tallies, 32 report lines, cursors asked for in order
             [None, C50, C100] plus the tags GET -- 5 data-plane GETs per
             pass.
    stub   : passes ZERO checks -- no vacuity anywhere in this check set.
    starter: raises NotImplementedError out of `build_skill_map`, exits 1,
             writes nothing, and passes 6 -- the cursor witness, both request-
             budget checks, and three conduct checks. They are real evidence
             rather than vacuity: the given `client.py` completes the roster
             crawl before the first NotImplementedError fires, and the stub
             fails them.
    naive  : a tidy implementation written from the vendor's one doc page and
             Ledgerfield's note and nothing else -- one record per person with
             the last one winning, and the vocabulary block taken as the tag
             collection. It runs clean, publishes exactly the right 119
             people, and reports nothing wrong anywhere. It fails 79 checks:
             all 32 rosters, 15 labels, 22 of 30 people, the skill-set
             coverage and exact-cardinality checks, three of the four tallies,
             the report line count and agreement, the tags-collection witness,
             its tag-resync conduct check and the re-run check.

    starter-vs-naive differing checks:
      29 naive-passes/starter-fails, 0 starter-passes/naive-fails. That
      one-way dominance is structural for a `build` starter that raises
      NotImplementedError after using the complete transport layer.

THREE MORE BASINS WERE PROBED on the same rig (`naive.patch` swapped for
each, the real one restored afterwards):

    defensive     0.307  the naive plus the one guard a cautious engineer
                         adds without observing anything: `setdefault`
                         instead of assignment, so the FIRST record for a
                         person wins rather than the last. It has the same
                         check vector because every capped witness is still
                         partial and every roster is harmed; the guard changes
                         WHICH partial answer appears, never that it is
                         partial.
    second-guess  0.526  an engineer who notices the duplicate `sw_id`s,
                         concludes the exporter re-emits a person's whole
                         list on the newest record, and takes the LONGEST
                         array per person rather than the union. This version
                         correctly crawls SourceWell's live tag collection,
                         isolating D1 from D2, but is right only for people
                         whose skills fit one chunk.
    alt-correct   1.000  a structurally different correct solution: the map
                         accumulated into sets by `itertools.groupby` over a
                         sorted copy of the records, the vocabulary built by
                         dict comprehension over the crawl, and membership
                         inverted from the person side rather than scanned
                         per tag. Scoring 1.000 says these checks are not
                         over-fitted to the reference solution's shape.

    Classic Compose reproduces gold 114/114 and the highest wrong basin,
    second-guess, at 60/114 with no verifier error.

    Active gold/second-guess/alternative-correct implementations emit 118 raw
    events because four evidence-gated conduct names are also emitted by
    `builtin_l2`; duplicate names fold with logical AND into 114 unique checks.

Grading is entirely evidence-based: per-skill membership and per-skill label
comparison against ``verifier/fixtures/answer_key.json``, per-person skill
sets, exact coverage of both sections, four tallies, cross-artifact agreement,
and request-log forensics for the tags GET, the walk and the budget.
SourceWell is read-only (``writeback.enabled: false``), so there is no vendor
state a connector could have written and the verifier injects no traffic of
its own. No whole-file comparison anywhere.
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

VENDOR = "sourcewell"
PROFILES_LIST_PATH = "/x/profiles"
TAGS_LIST_PATH = "/x/tags"


def _load_json(ctx, name: str):
    path = Path(ctx.output_dir) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _load_report(ctx) -> list[dict] | None:
    path = Path(ctx.output_dir) / "skills_report.csv"
    if not path.is_file():
        return None
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != ["tag_id", "name", "status", "member_count"]:
                return None
            return list(reader)
    except (OSError, csv.Error):
        return None


def _norm(value):
    """`""` and `None` are the same absence; a CSV cell and a JSON number are
    the same value."""
    if value is None or value == "":
        return None
    return str(value)


def _section(doc, name: str, key: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(doc, dict):
        return out
    for row in doc.get(name) or []:
        if isinstance(row, dict) and row.get(key):
            out[str(row[key])] = row
    return out


def _id_list(row, field: str) -> list[str] | None:
    value = row.get(field) if isinstance(row, dict) else None
    if not isinstance(value, list):
        return None
    return sorted(str(v) for v in value)


def _data_plane_gets(request_log) -> list[dict]:
    return [
        e for e in request_log
        if e.get("method") == "GET" and str(e.get("path", "")).startswith("/x")
    ]


def _cursors_asked_for(gets) -> set[str]:
    return {
        str(e.get("query", {}).get("cursor"))
        for e in gets
        if str(e.get("path", "")) == PROFILES_LIST_PATH and e.get("query")
    }


def _read_the_tag_collection(gets) -> bool:
    return any(
        str(e.get("path", "")) == TAGS_LIST_PATH and int(e.get("status", 0)) == 200
        for e in gets
    )


def _exact_artifacts(index, report, key) -> tuple[bool, str]:
    if not isinstance(index, dict) or set(index) != {"skills", "people", "counts"}:
        return False, "capability_index.json has the wrong envelope"
    # The answer key also carries author-only diagnostic labels (`group` on a
    # skill and `capped` on a person) used to stratify witness checks.  They are
    # not part of either published artifact and must never become secret output
    # columns just because the fixture stores them beside contract fields.
    expected_skills = [
        {name: row[name] for name in ("tag_id", "name", "status", "members", "member_count")}
        for row in key["skills"]
    ]
    expected_people = [
        {name: row[name] for name in ("sw_id", "nm", "skills", "skill_count")}
        for row in key["people"]
    ]
    if (
        index.get("skills") != expected_skills
        or index.get("people") != expected_people
        or index.get("counts") != key["counts"]
    ):
        return False, "the complete ordered index differs from vendor/extract truth"
    expected_report = [{
        "tag_id": row["tag_id"], "name": "" if row["name"] is None else str(row["name"]),
        "status": row["status"], "member_count": str(row["member_count"]),
    } for row in key["skills"]]
    return report == expected_report, "skills_report.csv differs from the exact ordered skill projection"


async def run(ctx) -> None:
    key = json.loads((Path(ctx.fixtures) / "answer_key.json").read_text(encoding="utf-8"))
    ctx.vendor(VENDOR).recreate(checkpoint=key["checkpoint"])
    skill_truth = {r["tag_id"]: r for r in key["skills"]}
    person_truth = {r["sw_id"]: r for r in key["people"]}
    issued = list(key["cursor_tokens"])

    # == run 1: fresh ========================================================
    code, _out, err = ctx.app.run()
    index = _load_json(ctx, "capability_index.json")
    report = _load_report(ctx)
    exact1 = _exact_artifacts(index, report, key)

    ctx.check_l1(
        "sw217_index_run1_completed",
        code == 0 and exact1[0],
        f"exit={code} index={type(index).__name__} "
        f"report={type(report).__name__}; {exact1[1]}; stderr={err[:400]}",
    )

    skills = _section(index, "skills", "tag_id")
    people = _section(index, "people", "sw_id")
    expected_tags = set(key["all_tag_ids"])
    expected_people = set(key["all_published_ids"])
    raw_skills = index.get("skills") if isinstance(index, dict) else None
    raw_people = index.get("people") if isinstance(index, dict) else None

    skill_ids = [str(row.get("tag_id")) for row in raw_skills] if isinstance(raw_skills, list) and all(isinstance(row, dict) for row in raw_skills) else []
    people_ids = [str(row.get("sw_id")) for row in raw_people] if isinstance(raw_people, list) and all(isinstance(row, dict) for row in raw_people) else []
    ctx.check_l1(
        "sw217_sections_have_exact_cardinality_and_order",
        skill_ids == sorted(expected_tags) and people_ids == sorted(expected_people),
        f"skill_rows={len(skill_ids)} unique={len(set(skill_ids))}; "
        f"people_rows={len(people_ids)} unique={len(set(people_ids))}",
    )

    ctx.check_l1(
        "sw217_index_covers_every_skill_sourcewell_holds",
        set(skills) == expected_tags,
        f"SourceWell holds {len(expected_tags)} skill(s); the index carries "
        f"{len(skills)}, missing={sorted(expected_tags - set(skills))[:5]} "
        f"extra={sorted(set(skills) - expected_tags)[:5]}",
    )
    ctx.check_l1(
        "sw217_index_covers_every_person_sourcewell_publishes",
        set(people) == expected_people,
        f"SourceWell holds {len(expected_people)} people it has not retired; "
        f"the index carries {len(people)}, "
        f"missing={sorted(expected_people - set(people))[:5]} "
        f"extra={sorted(set(people) - expected_people)[:5]}",
    )

    # -- per skill: who carries it. D1 lands here, for every skill. ----------
    for tag_id in key["all_tag_ids"]:
        want = skill_truth[tag_id]
        row = skills.get(tag_id)
        got = _id_list(row, "members") if row else None
        ok = (
            row is not None
            and got == want["members"]
            and _norm(row.get("member_count")) == _norm(want["member_count"])
        )
        ctx.check_l1(
            f"sw217_skill_roster_{tag_id}",
            ok,
            f"{tag_id} ({want['group']}, {want['member_count']} live "
            f"carrier(s)): the index publishes "
            f"{len(got) if got is not None else 'no'} member(s)"
            + (f", first difference at {sorted(set(want['members']) ^ set(got))[:5]}"
               if got is not None else ""),
        )

    # -- per skill: what it is called and whether SourceWell still uses it.
    # D2 lands here, and only here.
    for tag_id in key["all_tag_ids"]:
        want = skill_truth[tag_id]
        row = skills.get(tag_id)
        ok = (
            row is not None
            and _norm(row.get("name")) == _norm(want["name"])
            and _norm(row.get("status")) == _norm(want["status"])
        )
        published = (
            f"name={row.get('name')!r} status={row.get('status')!r}"
            if row else "no row at all"
        )
        ctx.check_l1(
            f"sw217_skill_label_{tag_id}",
            ok,
            f"{tag_id} ({want['group']}): the index publishes {published}; "
            f"SourceWell calls it {want['name']!r} and holds it as "
            f"{want['status']}",
        )

    # -- per person: the skills they carry. D1's other half. -----------------
    for sw_id in key["person_witnesses"]:
        want = person_truth[sw_id]
        row = people.get(sw_id)
        got = _id_list(row, "skills") if row else None
        ok = (
            row is not None
            and got == want["skills"]
            and _norm(row.get("skill_count")) == _norm(want["skill_count"])
            and _norm(row.get("nm")) == _norm(want["nm"])
        )
        ctx.check_l1(
            f"sw217_person_skills_{sw_id}",
            ok,
            f"{sw_id} ({'capped' if want['capped'] else 'single-record'} in "
            f"the extract): the index gives it "
            f"{len(got) if got is not None else 'no'} skill(s), "
            f"Ledgerfield holds {want['skill_count']}; "
            f"difference={sorted(set(want['skills']) ^ set(got or []))[:5]}",
        )

    # -- tallies -------------------------------------------------------------
    counts = index.get("counts") if isinstance(index, dict) else None
    counts = counts if isinstance(counts, dict) else {}
    for name, expected_n in key["counts"].items():
        ctx.check_l1(
            f"sw217_count_{name}",
            counts.get(name) == expected_n,
            f"counts[{name!r}]={counts.get(name)!r}; the index has "
            f"{expected_n}",
        )

    # -- the report has to say the same thing as the index -------------------
    ctx.check_l1(
        "sw217_skills_report_line_count",
        report is not None and len(report) == len(expected_tags),
        f"skills_report.csv holds {len(report) if report is not None else 0} "
        f"line(s); one per skill SourceWell holds is {len(expected_tags)}",
    )
    report_by_tag = {str(r.get("tag_id")): r for r in (report or [])}
    disagree = [
        tag_id for tag_id in key["all_tag_ids"]
        if tag_id not in report_by_tag
        or _norm(report_by_tag[tag_id].get("name")) != _norm(skill_truth[tag_id]["name"])
        or _norm(report_by_tag[tag_id].get("status")) != _norm(skill_truth[tag_id]["status"])
        or _norm(report_by_tag[tag_id].get("member_count"))
        != _norm(skill_truth[tag_id]["member_count"])
    ]
    ctx.check_l1(
        "sw217_skills_report_agrees_with_the_index",
        bool(report_by_tag) and not disagree,
        f"{len(disagree)} skills_report.csv line(s) disagree with what "
        f"SourceWell and Ledgerfield together say (sample={disagree[:5]}); the "
        f"report carried {len(report or [])} line(s)",
    )

    # -- request-log forensics ----------------------------------------------
    # Requirement-shaped throughout, and the verifier injects no traffic.
    request_log = ctx.vendor(VENDOR).request_log()
    run1_gets = _data_plane_gets(request_log)
    cursors_sent = _cursors_asked_for(run1_gets)
    ctx.check_l1(
        "sw217_pass_read_sourcewell_own_skill_collection",
        _read_the_tag_collection(run1_gets),
        "the pass never asked SourceWell for its tag collection; it made "
        f"{len(run1_gets)} data-plane GET(s) under /x, on paths "
        f"{sorted({str(e.get('path')) for e in run1_gets})}",
    )
    ctx.check_l1(
        "sw217_roster_walk_asked_for_every_cursor_sourcewell_issued",
        all(token in cursors_sent for token in issued),
        f"SourceWell issues {issued} as the cursors for the pages after the "
        f"first; the pass asked for {sorted(cursors_sent)}",
    )
    ctx.check_l1(
        "sw217_pass_stays_inside_request_budget",
        all(token in cursors_sent for token in issued)
        and len(run1_gets) <= key["request_budget"],
        f"the pass spent {len(run1_gets)} data-plane GET(s) under /x on a "
        f"{key['roster_page_count']}-page roster and a one-page skill "
        f"collection; the budget is {key['request_budget']} and the walk must "
        "have followed SourceWell's own cursors",
    )

    # == run 2: nothing has changed upstream =================================
    before_run2 = len(request_log)
    code2, _out2, err2 = ctx.app.run()
    index2 = _load_json(ctx, "capability_index.json")
    report2 = _load_report(ctx)
    exact2 = _exact_artifacts(index2, report2, key)
    skills2 = _section(index2, "skills", "tag_id")
    people2 = _section(index2, "people", "sw_id")

    ctx.check_l1(
        "sw217_index_run2_completed",
        code2 == 0 and exact2[0],
        f"exit={code2} index={type(index2).__name__}; {exact2[1]}; stderr={err2[:400]}",
    )
    ctx.check_l1(
        "sw217_rerun_publishes_the_same_index",
        exact2[0] and bool(skills2)
        and set(skills2) == expected_tags
        and set(people2) == expected_people
        and all(
            _id_list(skills2.get(t, {}), "members") == skill_truth[t]["members"]
            for t in key["all_tag_ids"]
        ),
        f"run 1 published {len(skills)} skill(s) over {len(people)} people, "
        f"run 2 published {len(skills2)} over {len(people2)}. SourceWell is "
        "read-only and the extract did not move, so an index that was short "
        "both times is not stable",
    )
    run2_gets = _data_plane_gets(ctx.vendor(VENDOR).request_log()[before_run2:])
    run2_cursors = _cursors_asked_for(run2_gets)
    ctx.check_l1(
        "sw217_rerun_stays_inside_request_budget",
        all(token in run2_cursors for token in issued)
        and 0 < len(run2_gets) <= key["request_budget"],
        f"the second pass spent {len(run2_gets)} data-plane GET(s) under /x "
        f"and asked for {sorted(run2_cursors)}; the budget is "
        f"{key['request_budget']} per pass and the walk must have completed",
    )

    all_gets = _data_plane_gets(ctx.vendor(VENDOR).request_log())
    credential_keys = {"key", "token", "api_key", "client_secret", "password"}
    credential_query = [e for e in all_gets if credential_keys.intersection(str(k).lower() for k in (e.get("query") or {}))]
    ctx.check_l1("no_credentials_in_query_string", bool(all_gets) and not credential_query, f"data_requests={len(all_gets)} credential_query={len(credential_query)}")
    ctx.check_l1("no_secrets_echoed_to_vendor", bool(all_gets), f"data_requests={len(all_gets)}; builtin_l2 audits logged values")
    ctx.check_l1(
        "no_unnecessary_full_resync:profile",
        all(token in cursors_sent for token in issued)
        and all(token in run2_cursors for token in issued)
        and 0 < len(run1_gets) <= key["request_budget"]
        and 0 < len(run2_gets) <= key["request_budget"],
        f"run1_gets={len(run1_gets)} run2_gets={len(run2_gets)} budget={key['request_budget']}",
    )
    run1_tag_pages = [e for e in run1_gets if e.get("path") == TAGS_LIST_PATH and int(e.get("status", 0)) == 200]
    run2_tag_pages = [e for e in run2_gets if e.get("path") == TAGS_LIST_PATH and int(e.get("status", 0)) == 200]
    ctx.check_l1(
        "no_unnecessary_full_resync:tag",
        len(run1_tag_pages) == 1 and len(run2_tag_pages) == 1,
        f"run1_tag_pages={len(run1_tag_pages)} run2_tag_pages={len(run2_tag_pages)}",
    )

    await builtin_l2(ctx, app_runs=2)
