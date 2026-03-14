import pytest

from app.runtime_config import invalidate_runtime_cache


@pytest.fixture(autouse=True)
def _isolate_summary_model_env(monkeypatch):
    monkeypatch.delenv("FAMILY_VAULT_SUMMARY_MODEL", raising=False)
    monkeypatch.delenv("FAMILY_VAULT_OLLAMA_BASE_URL", raising=False)
    invalidate_runtime_cache("summary_model")
    invalidate_runtime_cache("ollama_base_url")
    yield
    monkeypatch.delenv("FAMILY_VAULT_SUMMARY_MODEL", raising=False)
    monkeypatch.delenv("FAMILY_VAULT_OLLAMA_BASE_URL", raising=False)
    invalidate_runtime_cache("summary_model")
    invalidate_runtime_cache("ollama_base_url")


def test_patch_settings_does_not_require_restart_when_effective_value_is_unchanged(authed_client, monkeypatch):
    monkeypatch.setenv("FAMILY_VAULT_SUMMARY_MODEL", "env-summary")

    response = authed_client.patch("/v1/settings", json={"summary_model": "env-summary"})

    assert response.status_code == 200
    assert response.json()["restart_required"] is False


def test_patch_settings_requires_restart_when_effective_value_changes(authed_client, monkeypatch):
    monkeypatch.setenv("FAMILY_VAULT_SUMMARY_MODEL", "env-summary")

    response = authed_client.patch("/v1/settings", json={"summary_model": "new-summary"})

    assert response.status_code == 200
    assert response.json()["restart_required"] is True


def test_patch_settings_ollama_equivalent_api_suffix_does_not_require_restart(authed_client, monkeypatch):
    monkeypatch.setenv("FAMILY_VAULT_OLLAMA_BASE_URL", "http://ollama:11434")

    response = authed_client.patch("/v1/settings", json={"ollama_base_url": "http://ollama:11434/api"})

    assert response.status_code == 200
    assert response.json()["restart_required"] is False


def test_patch_settings_ollama_actual_change_does_not_require_restart(authed_client, monkeypatch):
    # ollama_base_url is NOT in RESTART_REQUIRED_KEYS anymore
    # Provider UI handles this directly without needing a restart
    monkeypatch.setenv("FAMILY_VAULT_OLLAMA_BASE_URL", "http://ollama:11434")

    response = authed_client.patch("/v1/settings", json={"ollama_base_url": "http://ollama:11435"})

    assert response.status_code == 200
    assert response.json()["restart_required"] is False
