from types import SimpleNamespace

from sqlalchemy import select

from app.api import routes
from app.db import SessionLocal
from app.llm_models.llm_provider import LLMProvider
from app.llm_models.llm_provider_model import LLMProviderModel


class _FakeModelsAPI:
    def __init__(self, values: list[str]):
        self._values = values

    def list(self):
        return SimpleNamespace(data=[SimpleNamespace(id=value) for value in self._values])


class _FakeClient:
    def __init__(self, values: list[str]):
        self.models = _FakeModelsAPI(values)


class _FakeProvider:
    def __init__(self, values: list[str]):
        self._values = values

    def create_client(self):
        return _FakeClient(self._values)


class _FakeOllamaResponse:
    def __init__(self, models: list[dict[str, str]]):
        self._models = models

    def raise_for_status(self):
        return None

    def json(self):
        return {"models": self._models}


def test_create_cloud_provider_validates_and_persists_models(admin_client, monkeypatch):
    monkeypatch.setattr(routes, "create_provider", lambda config: _FakeProvider(["gpt-4o-mini", "gpt-4o"]))

    response = admin_client.post(
        "/v1/llm/providers",
        json={
            "name": "OpenAI Cloud",
            "provider_type": "openai",
            "base_url": "https://api.openai.com",
            "api_key": "sk-test",
            "model_name": "gpt-4o-mini",
            "is_active": True,
            "is_default": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["base_url"] == "https://api.openai.com/v1"

    models_response = admin_client.get(f"/v1/llm/providers/{body['id']}/models")
    assert models_response.status_code == 200
    assert models_response.json() == ["gpt-4o", "gpt-4o-mini"]

    with SessionLocal() as db:
        stored_models = db.execute(
            select(LLMProviderModel.model_name).where(LLMProviderModel.provider_id == body["id"])
        ).scalars().all()
    assert sorted(stored_models) == ["gpt-4o", "gpt-4o-mini"]


def test_create_cloud_provider_rejects_missing_api_key(admin_client):
    response = admin_client.post(
        "/v1/llm/providers",
        json={
            "name": "Broken Cloud",
            "provider_type": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini",
            "is_active": True,
            "is_default": False,
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "llm_provider_api_key_required"}

    with SessionLocal() as db:
        providers = db.execute(select(LLMProvider)).scalars().all()
    assert providers == []


def test_validate_provider_reuses_existing_api_key_when_edit_form_leaves_it_blank(admin_client, monkeypatch):
    monkeypatch.setattr(routes, "create_provider", lambda config: _FakeProvider(["gpt-4.1-mini"]))
    create_response = admin_client.post(
        "/v1/llm/providers",
        json={
            "name": "Existing Cloud",
            "provider_type": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-existing",
            "model_name": "gpt-4.1-mini",
            "is_active": True,
            "is_default": False,
        },
    )
    assert create_response.status_code == 200
    provider_id = create_response.json()["id"]

    validate_response = admin_client.post(
        "/v1/llm/providers/validate",
        json={
            "provider_id": provider_id,
            "provider_type": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model_name": "gpt-4.1-mini",
            "is_active": True,
        },
    )

    assert validate_response.status_code == 200
    assert validate_response.json()["ok"] is True


def test_validate_ollama_with_no_models_does_not_probe(admin_client, monkeypatch):
    probe_calls: list[str] = []

    def fake_get(url: str, timeout: int):
        assert url == "http://localhost:11434/api/tags"
        assert timeout == 15
        return _FakeOllamaResponse([])

    def fake_probe(**kwargs):
        probe_calls.append("called")
        raise AssertionError("probe should not run for an empty ollama model list")

    monkeypatch.setattr(routes.requests, "get", fake_get)
    monkeypatch.setattr(routes, "_probe_llm_provider_connection", fake_probe)

    response = admin_client.post(
        "/v1/llm/providers/validate",
        json={
            "provider_type": "ollama",
            "base_url": "http://localhost:11434",
            "model_name": "",
            "is_active": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "latency_ms": response.json()["latency_ms"],
        "models": [],
        "normalized_base_url": "http://localhost:11434",
        "warning": None,
        "error": None,
    }
    assert probe_calls == []


def test_create_update_and_test_provider_propagate_warning_when_models_fallback(admin_client, monkeypatch):
    probe_calls: list[str] = []

    def fake_list_models_from_config(*args, **kwargs):
        return [], False

    def fake_probe(**kwargs):
        probe_calls.append(kwargs["model_name"])

    monkeypatch.setattr(routes, "_list_models_from_config", fake_list_models_from_config)
    monkeypatch.setattr(routes, "_probe_llm_provider_connection", fake_probe)

    create_response = admin_client.post(
        "/v1/llm/providers",
        json={
            "name": "Fallback Cloud",
            "provider_type": "openai",
            "base_url": "https://api.openai.com",
            "api_key": "sk-test",
            "model_name": "gpt-4o-mini",
            "is_active": True,
            "is_default": False,
        },
    )

    assert create_response.status_code == 200
    create_body = create_response.json()
    assert create_body["warning"] == (
        "Provider connection validated via chat completion, but /models is unavailable. Configure the model name manually."
    )
    provider_id = create_body["id"]

    update_response = admin_client.put(
        f"/v1/llm/providers/{provider_id}",
        json={
            "name": "Fallback Cloud Updated",
            "model_name": "gpt-4.1-mini",
            "is_active": True,
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["warning"] == create_body["warning"]

    test_response = admin_client.post(f"/v1/llm/providers/{provider_id}/test")

    assert test_response.status_code == 200
    assert test_response.json()["ok"] is True
    assert test_response.json()["models"] == []
    assert test_response.json()["warning"] == create_body["warning"]
    assert probe_calls == ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-mini"]


def test_validate_ollama_request_failure_returns_error_without_probe(admin_client, monkeypatch):
    probe_calls: list[str] = []

    def fake_get(url: str, timeout: int):
        raise RuntimeError("ollama offline")

    def fake_probe(**kwargs):
        probe_calls.append("called")
        raise AssertionError("probe should not run when ollama model listing fails")

    monkeypatch.setattr(routes.requests, "get", fake_get)
    monkeypatch.setattr(routes, "_probe_llm_provider_connection", fake_probe)

    response = admin_client.post(
        "/v1/llm/providers/validate",
        json={
            "provider_type": "ollama",
            "base_url": "http://localhost:11434",
            "model_name": "",
            "is_active": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["models"] == []
    assert response.json()["warning"] is None
    assert response.json()["error"] == "ollama_models_list_unavailable"
    assert probe_calls == []
