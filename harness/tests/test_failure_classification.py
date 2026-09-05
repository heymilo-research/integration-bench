from bench.commands.eval_core import rollout_failure_class
from bench.commands.grading_core import failure_class_for_exception, failure_class_for_phase
from bench.compose import ParticipantDiskLimitExceeded


def test_grading_failure_ownership_is_explicit():
    assert failure_class_for_phase("candidate_build") == "candidate_build_failure"
    assert failure_class_for_phase("compose_render") == "benchmark_infrastructure_failure"
    assert failure_class_for_phase("benchmark_startup") == "benchmark_infrastructure_failure"
    assert failure_class_for_phase("verifier") == "benchmark_verifier_failure"


def test_registry_dns_failure_during_build_is_infrastructure():
    exc = RuntimeError(
        'failed to fetch anonymous token: Get "https://auth.docker.io/token": '
        "dial tcp: lookup auth.docker.io: no such host"
    )
    assert failure_class_for_exception("candidate_build", exc) == (
        "benchmark_infrastructure_failure"
    )


def test_candidate_dockerfile_failure_remains_candidate_owned():
    exc = RuntimeError("Dockerfile: RUN pytest returned a non-zero code: 1")
    assert failure_class_for_exception("candidate_build", exc) == "candidate_build_failure"


def test_participant_resource_breach_is_candidate_owned_in_any_phase():
    exc = ParticipantDiskLimitExceeded("participant disk limit exceeded")
    assert failure_class_for_exception("verifier", exc) == "candidate_runtime_failure"


def test_rollout_stop_reason_does_not_make_candidate_failure_retryable():
    assert rollout_failure_class("wall_clock", "candidate_result") == "candidate_runtime_failure"
    assert rollout_failure_class("turn_cap", "candidate_result") == "candidate_runtime_failure"
    assert (
        rollout_failure_class("provider_error", "candidate_result")
        == "provider_infrastructure_failure"
    )
    assert (
        rollout_failure_class("usage_limit", "candidate_result")
        == "provider_infrastructure_failure"
    )
    assert rollout_failure_class("done", "candidate_build_failure") == "candidate_build_failure"
