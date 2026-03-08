import os

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.utils import encryption


def _reset_singleton() -> None:
    encryption._encryptor = None


def test_encrypt_decrypt_round_trip(monkeypatch):
    _reset_singleton()
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("FAMILY_VAULT_ENCRYPTION_KEY", key)

    plaintext = "secret-value"
    encrypted = encryption.encrypt(plaintext)
    assert encrypted is not None
    assert encrypted != plaintext
    assert encryption.decrypt(encrypted) == plaintext


def test_key_management_uses_env_first(monkeypatch, tmp_path):
    _reset_singleton()
    monkeypatch.setenv("HOME", str(tmp_path))
    env_key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("FAMILY_VAULT_ENCRYPTION_KEY", env_key)

    encryptor = encryption.APIKeyEncryptor()
    assert encryptor.key == env_key.encode("utf-8")


def test_key_management_creates_home_key_file(monkeypatch, tmp_path):
    _reset_singleton()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FAMILY_VAULT_ENCRYPTION_KEY", raising=False)

    encryptor = encryption.APIKeyEncryptor()
    key_file = tmp_path / ".family-vault" / "encryption.key"

    assert key_file.exists()
    assert key_file.read_bytes() == encryptor.key
    assert os.stat(key_file).st_mode & 0o777 == 0o600


def test_invalid_input_handling(monkeypatch):
    _reset_singleton()
    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("FAMILY_VAULT_ENCRYPTION_KEY", key)

    assert encryption.encrypt(None) is None
    assert encryption.decrypt(None) is None

    with pytest.raises(InvalidToken):
        encryption.decrypt("not-a-valid-token")


def test_invalid_key_raises(monkeypatch):
    _reset_singleton()
    monkeypatch.setenv("FAMILY_VAULT_ENCRYPTION_KEY", "invalid-key")

    with pytest.raises(ValueError):
        encryption.APIKeyEncryptor()
