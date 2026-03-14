import builtins
import os
from pathlib import Path

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
    monkeypatch.delenv("FAMILY_VAULT_ENCRYPTION_KEY", raising=False)
    key_file = tmp_path / "encryption.key"
    monkeypatch.setattr(
        encryption.APIKeyEncryptor,
        "_candidate_key_paths",
        staticmethod(lambda: [key_file]),
    )

    encryptor = encryption.APIKeyEncryptor()

    assert key_file.exists()
    assert key_file.read_bytes() == encryptor.key
    assert os.stat(key_file).st_mode & 0o777 == 0o600


def test_key_management_falls_back_when_primary_path_not_writable(monkeypatch, tmp_path):
    _reset_singleton()
    monkeypatch.delenv("FAMILY_VAULT_ENCRYPTION_KEY", raising=False)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    blocked_key_file = tmp_path / "blocked" / "encryption.key"
    fallback_key_file = home_dir / ".family-vault" / "encryption.key"
    monkeypatch.setattr(
        encryption.APIKeyEncryptor,
        "_candidate_key_paths",
        staticmethod(lambda: [blocked_key_file, fallback_key_file]),
    )

    original_exists = Path.exists
    original_open = builtins.open

    def fake_exists(self: Path) -> bool:
        if self == blocked_key_file:
            raise PermissionError(13, "Permission denied", str(self))
        return original_exists(self)

    def fake_open(file, mode="r", *args, **kwargs):
        if "w" in mode and Path(file) == blocked_key_file:
            raise PermissionError(13, "Permission denied", str(file))
        return original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(builtins, "open", fake_open)

    encryptor = encryption.APIKeyEncryptor()

    assert fallback_key_file.exists()
    assert fallback_key_file.read_bytes() == encryptor.key


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
