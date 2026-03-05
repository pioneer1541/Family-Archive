"""
API Key 加密工具
使用 Fernet 对称加密算法加密敏感信息
"""

import os
import base64
from pathlib import Path
from cryptography.fernet import Fernet
from typing import Optional


class APIKeyEncryptor:
    """
    API Key 加密器
    
    加密密钥来源优先级：
    1. 环境变量 FAMILY_VAULT_ENCRYPTION_KEY
    2. 密钥文件 /app/secrets/encryption.key
    3. 用户目录 ~/.family-vault/encryption.key
    """
    
    def __init__(self):
        self.key = self._get_or_create_key()
        self.cipher = Fernet(self.key)
    
    def _get_or_create_key(self) -> bytes:
        """获取或创建加密密钥"""
        # 1. 从环境变量获取
        env_key = os.getenv("FAMILY_VAULT_ENCRYPTION_KEY")
        if env_key:
            return base64.urlsafe_b64decode(env_key)
        
        # 2. 从密钥文件获取
        key_paths = [
            Path("/app/secrets/encryption.key"),
            Path.home() / ".family-vault" / "encryption.key"
        ]
        
        for key_file in key_paths:
            if key_file.exists():
                with open(key_file, "rb") as f:
                    return f.read()
        
        # 3. 创建新密钥（仅创建在用户目录下）
        key = Fernet.generate_key()
        key_file = Path.home() / ".family-vault" / "encryption.key"
        key_file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with open(key_file, "wb") as f:
            f.write(key)
        # 设置文件权限为 600 (只有所有者可以读写)
        os.chmod(key_file, 0o600)
        return key
    
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
