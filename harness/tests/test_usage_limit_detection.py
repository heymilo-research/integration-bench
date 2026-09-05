"""`detect_usage_limit` must fire on the CLI's own limit notice and NEVER on the
agent talking about rate limiting.

The second half is the point. Integration-Bench is a benchmark about rate
limiting: a correct agent solving task-0022 or task-0047 prints "429",
"rate limit" and "Retry-After" repeatedly. A detector broad enough to catch those
would mark successful runs on the most important tasks in the suite as
`rate_limited` and drop them from the results — worse than having no detector,
because the loss would be silent and biased toward the hardest tasks.
"""

from __future__ import annotations

from bench.commands.eval_core import detect_usage_limit


def test_detects_cli_limit_notice_with_reset_epoch():
    hit, reset = detect_usage_limit("Claude AI usage limit reached|1786500000")
    assert hit is True
    assert reset == 1786500000


def test_detects_limit_notice_without_epoch():
    hit, reset = detect_usage_limit("Claude AI usage limit reached")
    assert hit is True
    assert reset is None


def test_detects_five_hour_session_limit_notice():
    hit, reset = detect_usage_limit("You've hit your session limit · resets 11:20am (UTC)")
    assert hit is True
    assert reset is None


def test_detects_reset_phrasing():
    assert detect_usage_limit("Your usage limit will reset at 3pm")[0] is True
    assert detect_usage_limit("You are approaching your usage limit")[0] is True


def test_ignores_the_agents_own_rate_limit_talk():
    """Every one of these is normal output from a CORRECT solution."""
    agent_lines = [
        "The vendor returned 429 with Retry-After: 8, so I back off before retrying.",
        "Implementing rate limit handling in client.py",
        "GET /v1/candidates -> 429 (rate limited), sleeping 8s",
        "The rate limiter is a fixed 25 requests per 60s window.",
        "I hit the vendor's rate limit 16 times during the backfill.",
        "Added a token bucket so we never exceed the documented limit.",
        "429 Too Many Requests",
    ]
    for line in agent_lines:
        assert detect_usage_limit(line) == (False, None), line


def test_ignores_empty_and_unrelated():
    assert detect_usage_limit("") == (False, None)
    assert detect_usage_limit("Wrote repo/client.py") == (False, None)
