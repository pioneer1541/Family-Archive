from types import SimpleNamespace

from app.api.routes import _list_models_from_provider, _normalize_provider_base_url
from app.llm_models.llm_provider import ProviderType as ModelProviderType
from app.services.llm_provider import LLMConfig, OllamaProvider, ProviderType as ServiceProviderType, normalize_ollama_base_url


def test_normalize_ollama_base_url_accepts_root_v1_and_api() -> None:
    # Basic URLs
    assert normalize_ollama_base_url("http://192.168.1.162:11434") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/") == "http://192.168.1.162:11434"
    
    # /v1 suffix removal
    assert normalize_ollama_base_url("http://192.168.1.162:11434/v1") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/v1/") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/V1") == "http://192.168.1.162:11434"  # case insensitive
    assert normalize_ollama_base_url("http://192.168.1.162:11434/V1/") == "http://192.168.1.162:11434"
    
    # /api suffix removal (MUST handle /api and /api/)
    assert normalize_ollama_base_url("http://192.168.1.162:11434/api") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/api/") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/API") == "http://192.168.1.162:11434"  # case insensitive
    assert normalize_ollama_base_url("http://192.168.1.162:11434/API/") == "http://192.168.1.162:11434"
    
    # Combined suffixes
    assert normalize_ollama_base_url("http://192.168.1.162:11434/v1/api/") == "http://192.168.1.162:11434"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/api/v1") == "http://192.168.1.162:11434"
    
    # Nested paths with /api
    assert normalize_ollama_base_url("http://192.168.1.162:11434/custom/api") == "http://192.168.1.162:11434/custom"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/custom/api/") == "http://192.168.1.162:11434/custom"
    assert normalize_ollama_base_url("http://192.168.1.162:11434/path/to/api") == "http://192.168.1.162:11434/path/to"


def test_normalize_ollama_base_url_strips_api_suffix() -> None:
    """Explicitly test that /api and /api/ suffixes are stripped (Claude Review requirement)."""
    # Standard /api suffix
    assert normalize_ollama_base_url("http://localhost:11434/api") == "http://localhost:11434"
    assert normalize_ollama_base_url("http://localhost:11434/api/") == "http://localhost:11434"
    
    # With trailing slashes
    assert normalize_ollama_base_url("http://host:11434/api//") == "http://host:11434"
    
    # Case insensitive
    assert normalize_ollama_base_url("http://localhost:11434/API") == "http://localhost:11434"
    assert normalize_ollama_base_url("http://localhost:11434/Api/") == "http://localhost:11434"
    
    # With subpath
    assert normalize_ollama_base_url("http://host:11434/ollama/api") == "http://host:11434/ollama"
    assert normalize_ollama_base_url("http://host:11434/ollama/api/") == "http://host:11434/ollama"
    
    # Combined with /v1
    assert normalize_ollama_base_url("http://localhost:11434/v1/api") == "http://localhost:11434"
    assert normalize_ollama_base_url("http://localhost:11434/api/v1") == "http://localhost:11434"


def test_normalize_provider_base_url_only_for_ollama() -> None:
    assert (
        _normalize_provider_base_url(ModelProviderType.OLLAMA, "http://192.168.1.162:11434/v1")
        == "http://192.168.1.162:11434"
    )
    assert (
        _normalize_provider_base_url(ModelProviderType.OPENAI, "https://api.openai.com/v1")
        == "https://api.openai.com/v1"
    )


def test_ollama_provider_chat_and_health_use_normalized_url(monkeypatch) -> None:
    seen_urls: dict[str, str] = {}

    class _PostResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "message": {"content": "ok"},
                "model": "qwen3:8b",
                "done": True,
            }

    class _GetResp:
        status_code = 200

    def _fake_post(url: str, json: dict, timeout: float):
        seen_urls["chat"] = url
        return _PostResp()

    def _fake_get(url: str, timeout: float):
        seen_urls["tags"] = url
        return _GetResp()

    monkeypatch.setattr("app.services.llm_provider.requests.post", _fake_post)
    monkeypatch.setattr("app.services.llm_provider.requests.get", _fake_get)

    provider = OllamaProvider(
        LLMConfig(
            provider_type=ServiceProviderType.OLLAMA,
            base_url="http://192.168.1.162:11434/v1",
            model_name="qwen3:8b",
        )
    )

    resp = provider.chat_completion(messages=[{"role": "user", "content": "hi"}])
    assert resp.content == "ok"
    assert provider.health_check() is True
    assert seen_urls["chat"] == "http://192.168.1.162:11434/api/chat"
    assert seen_urls["tags"] == "http://192.168.1.162:11434/api/tags"


def test_list_models_from_provider_uses_normalized_ollama_url(monkeypatch) -> None:
    seen: dict[str, str] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"models": [{"name": "qwen3:8b"}, {"name": "deepseek:8b"}]}

    def _fake_get(url: str, timeout: float):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr("app.api.routes.requests.get", _fake_get)

    provider = SimpleNamespace(
        base_url="http://192.168.1.162:11434/v1",
        provider_type=ModelProviderType.OLLAMA,
        api_key_encrypted=None,
        model_name="",
    )

    names = _list_models_from_provider(provider)
    assert names == ["qwen3:8b", "deepseek:8b"]
    assert seen["url"] == "http://192.168.1.162:11434/api/tags"
