"""
API Key 加密工具
使用 Fernet 对称加密算法加密敏感信息
"""

import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet


class APIKeyEncryptor:
    """
    API Key 加密器

    加密密钥来源优先级：
    1. 环境变量 FAMILY_VAULT_ENCRYPTION_KEY
    2. 密钥文件 /app/secrets/encryption.key
    3. 数据目录 /app/data/.family-vault/encryption.key
    """

    def __init__(self):
        self.key = self._get_or_create_key()
        self.cipher = Fernet(self.key)

    @staticmethod
    def _candidate_key_paths() -> list[Path]:
        custom_key_file = str(os.getenv("FAMILY_VAULT_ENCRYPTION_KEY_FILE") or "").strip()
        candidates: list[Path] = []
        if custom_key_file:
            candidates.append(Path(custom_key_file).expanduser())

        # Keep backward-compatible default paths, then add writable fallbacks.
        candidates.extend(
            [
                Path("/app/secrets/encryption.key"),
                Path("/app/data/.family-vault/encryption.key"),
                Path.home() / ".family-vault" / "encryption.key",
                Path("/tmp/family-vault/encryption.key"),
            ]
        )

        deduped: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            marker = str(path)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(path)
        return deduped

    def _get_or_create_key(self) -> bytes:
        """获取或创建加密密钥"""
        # 1. 从环境变量获取
        env_key = os.getenv("FAMILY_VAULT_ENCRYPTION_KEY")
        if env_key:
            return env_key.encode()

        key_paths = self._candidate_key_paths()
        errors: list[str] = []

        # 2. 从可访问密钥文件读取
        for key_file in key_paths:
            try:
                if not key_file.exists():
                    continue
                with open(key_file, "rb") as f:
                    existing_key = f.read()
            except OSError as exc:
                errors.append(f"read {key_file}: {exc}")
                continue

            if existing_key:
                return existing_key

        # 3. 生成新密钥并写入首个可写路径
        key = Fernet.generate_key()
        for key_file in key_paths:
            try:
                key_file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                with open(key_file, "wb") as f:
                    f.write(key)
                # 设置文件权限为 600 (只有所有者可以读写)
                os.chmod(key_file, 0o600)
                return key
            except OSError as exc:
                errors.append(f"write {key_file}: {exc}")

        details = "; ".join(errors) if errors else "no candidate path available"
        raise PermissionError(f"Unable to read or create encryption key. {details}")

    def encrypt(self, plaintext: Optional[str]) -> Optional[str]:
        """
        加密字符串

        Args:
            plaintext: 要加密的明文

        Returns:
            加密后的密文，如果输入为 None 则返回 None
        """
        if plaintext is None:
            return None
        return self.cipher.encrypt(plaintext.encode()).decode()

    def decrypt(self, encrypted: Optional[str]) -> Optional[str]:
        """
        解密字符串

        Args:
            encrypted: 要解密的密文

        Returns:
            解密后的明文，如果输入为 None 则返回 None
        """
        if encrypted is None:
            return None
        return self.cipher.decrypt(encrypted.encode()).decode()


# 全局单例实例
_encryptor: Optional[APIKeyEncryptor] = None


def get_encryptor() -> APIKeyEncryptor:
    """获取加密器单例实例"""
    global _encryptor
    if _encryptor is None:
        _encryptor = APIKeyEncryptor()
    return _encryptor


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    """便捷函数：加密字符串"""
    return get_encryptor().encrypt(plaintext)


def decrypt(encrypted: Optional[str]) -> Optional[str]:
    """便捷函数：解密字符串"""
    return get_encryptor().decrypt(encrypted)
