from pathlib import Path

from bench.credentials import resolve_credential_env

SAMPLE_TASK = Path(__file__).parent / "fixtures" / "sample_task"
SECOND_TASK = Path(__file__).parent / "fixtures" / "compose_listenv"


def test_resolve_credential_env_dict_form():
    values = resolve_credential_env(
        SAMPLE_TASK, ["SV_CLIENT_ID", "SV_CLIENT_SECRET", "SV_WEBHOOK_SECRET"]
    )
    assert values == {
        "SV_CLIENT_ID": "sample-client-id",
        "SV_CLIENT_SECRET": "sample-client-secret",
        "SV_WEBHOOK_SECRET": "sample-webhook-secret",
    }


def test_resolve_credential_env_from_second_contract():
    values = resolve_credential_env(SECOND_TASK, ["SV_CLIENT_ID", "SV_CLIENT_SECRET"])
    assert values == {
        "SV_CLIENT_ID": "list-client-id",
        "SV_CLIENT_SECRET": "list-client-secret",
    }


def test_resolve_credential_env_missing_names_are_omitted_not_errors():
    values = resolve_credential_env(SAMPLE_TASK, ["SV_CLIENT_ID", "DOES_NOT_EXIST"])
    assert values == {"SV_CLIENT_ID": "sample-client-id"}


def test_resolve_credential_env_without_task_contract(tmp_path):
    assert resolve_credential_env(tmp_path, ["ANYTHING"]) == {}
