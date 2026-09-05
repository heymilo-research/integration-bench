# Conduct Rulebook — L2 Assertions & Authoring Gates

Part of Integration-Bench. Binding for all verifier and task authors.

## The invariants-only principle

L2 (conduct) assertions grade **protocol citizenship from the request log**, and they may
test **invariant violations only** — things no correct integration may ever do. They must
never test *similarity to the gold solution's behavior*. Two correct connectors may cache
tokens differently, retry 3 vs 5 times, batch differently; all of that is legal variance.

Litmus test for every proposed L2 assert: *"Could a competent engineer write a fully
correct connector that trips this?"* If yes, the assert is forbidden. This rule exists
because SWE-bench Verified's autopsy found ~59% of hard tasks rejected functionally
correct solutions; we refuse to rebuild that bug at the conduct layer.

## Hard gates (any violation ⇒ task failed)

Sourced from the request log, webhook-delivery log, and token log:

1. **Credentials in a query string** when a header placement exists
   (any request with a known secret value in `query` — values matched exactly).
2. **Unverified webhook signature accepted**: the tamper-injected payload (bad HMAC)
   was applied to the canonical store.
3. **Tampered/expired-timestamp payload applied** (skew beyond `max_skew_s`).
4. **Secrets echoed to the vendor** in bodies/paths where they don't belong
   (e.g., client_secret sent to a data-plane endpoint).

Hard gates fold into *resolved*: a hard violation means the task is not resolved,
regardless of L1 state correctness — a cred-leaking integration is a failed integration.

## Soft score (reported separately; never affects resolved)

Each is a counted violation; the conduct score is `1 - violations/checks` aggregated
per run (formula default — revisit):

1. **Re-auth per request**: > N token mints where 1 suffices (N from route TTL math,
   generous margin ×3).
2. **Ignored `Retry-After`**: a retry of a 429'd request arrived before the advertised
   delay elapsed (judged by request-log timestamp ordering vs the advertised value —
   never by verifier wall-clock).
3. **Full re-sync when incremental possible**: repeated full-table pulls after a
   completed initial sync when `modified_since` (or cursors) would serve.
4. **Resume restarts from page 1**: after a mid-pagination failure, the next request
   re-fetched page 1 instead of resuming the cursor/offset watermark.
5. **Hot-loop on 401/5xx**: > K immediate retries with no changed request
   (K = 5 default — revisit).
6. **Blind retry of non-idempotent writes without an idempotency key** where the vendor
   supports keys.

Soft asserts must still pass the litmus test — each names a behavior with *no* correct
justification, with thresholds generous enough to admit all reasonable strategies.

## Diagnostic metrics (reported, never scored)

- **Discovery efficiency**: requests between first symptom of a doc lie (the 400, the
  loop, the wrong-format parse) and adapted behavior; whether the sandbox was probed
  before/after. Research signal only.

## Task-authoring fairness gates (validation checklist, every task)

1. **Gold green**: gold patch passes all scenarios, 5/5 runs.
2. **Empty patch red**: unmodified repo fails at least one L1 assert (else the task
   tests nothing).
3. **Flake gate**: 5 consecutive identical verdicts on gold and on empty
   (structural equality over check names, ok flags, and scores — the run
   identifier and free-text diagnostic details are excluded, since details
   may embed wall-clock-dependent values).
4. **Lie discoverability**: for every configured doc lie, a written walk-through showing
   the path from symptom to truth using only sandbox responses. No walk-through, no lie.
5. **Docs necessary-but-not-sufficient**: unguessable facts (signature algorithm, header
   names) are in docs and true; at least one task-relevant truth is *only* learnable
   from the sandbox when the vendor's profile has lies.
6. **Correct-but-different check** (human review): reviewer sketches ≥1 materially
   different valid implementation strategy and confirms every L1/L2 assert admits it.
7. **Spec-sufficiency check** (human review): PROBLEM.md + docs + sandbox contain enough
   information to derive every behavior L1 asserts. No grading on unstated requirements.
