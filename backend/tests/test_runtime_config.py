from app import runtime_config
from app.db import SessionLocal


def test_get_runtime_setting_default_and_unknown_key(monkeypatch):
    runtime_config.invalidate_runtime_cache()
    monkeypatch.delenv("FAMILY_VAULT_PLANNER_MODEL", raising=False)

    value = runtime_config.get_runtime_setting("planner_model", db=None)
    assert value == "qwen3:1.7b"

    try:
        runtime_config.get_runtime_setting("does_not_exist", db=None)
    except KeyError:
        pass
    else:
        raise AssertionError("Expected KeyError for unknown key")


def test_set_runtime_setting_and_get_runtime_setting_db_override(monkeypatch):
    runtime_config.invalidate_runtime_cache()
    monkeypatch.setenv("FAMILY_VAULT_PLANNER_MODEL", "env-model")

    with SessionLocal() as db:
        saved = runtime_config.set_runtime_setting("planner_model", "db-model", db)
        assert saved == "db-model"

        value = runtime_config.get_runtime_setting("planner_model", db)
        assert value == "db-model"


def test_cache_behavior_for_env_values(monkeypatch):
    runtime_config.invalidate_runtime_cache()
    monkeypatch.setenv("FAMILY_VAULT_SUMMARY_MODEL", "model-a")

    first = runtime_config.get_runtime_setting("summary_model", db=None)
    assert first == "model-a"

    monkeypatch.setenv("FAMILY_VAULT_SUMMARY_MODEL", "model-b")
    cached = runtime_config.get_runtime_setting("summary_model", db=None)
    assert cached == "model-a"

    runtime_config.invalidate_runtime_cache("summary_model")
    refreshed = runtime_config.get_runtime_setting("summary_model", db=None)
    assert refreshed == "model-b"


def test_set_runtime_setting_invalidates_cache(monkeypatch):
    runtime_config.invalidate_runtime_cache()
    monkeypatch.setenv("FAMILY_VAULT_EMBED_MODEL", "env-embed")

    cached = runtime_config.get_runtime_setting("embed_model", db=None)
    assert cached == "env-embed"

    with SessionLocal() as db:
        runtime_config.set_runtime_setting("embed_model", "db-embed", db)
        value = runtime_config.get_runtime_setting("embed_model", db)

    assert value == "db-embed"
