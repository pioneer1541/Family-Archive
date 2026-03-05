"""
LLM Provider 数据模型
支持多种 LLM Provider（OpenAI、Kimi、GLM、Ollama、Custom）
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import String, Boolean, DateTime, Text, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProviderType(str, PyEnum):
    """Provider 类型枚举"""
    OLLAMA = "ollama"      # 本地 Ollama
    OPENAI = "openai"      # OpenAI 官方
    KIMI = "kimi"          # Moonshot Kimi
    GLM = "glm"            # 智谱 GLM
    CUSTOM = "custom"      # 自定义 OpenAI 兼容


class LLMProvider(Base):
    """
    LLM Provider 配置表
    
    存储各种 LLM Provider 的配置信息，包括 API 密钥、基础 URL 等
    """
    __tablename__ = "llm_providers"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    """Provider 唯一标识 UUID"""
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    """Provider 显示名称，如 'OpenAI', 'Kimi'"""
    
    provider_type: Mapped[ProviderType] = mapped_column(
        SQLEnum(ProviderType, native_enum=False),
        nullable=False,
        default=ProviderType.OLLAMA
    )
    """Provider 类型：ollama, openai, kimi, glm, custom"""
    
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    """API Base URL，如 https://api.openai.com/v1"""
    
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    """加密后的 API Key（云端 Provider 需要，Ollama 不需要）"""
    
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    """默认模型名称，用于向后兼容"""
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    """是否启用此 Provider"""
    
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    """是否为默认 Provider"""
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )
    """创建时间"""
    
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    """更新时间"""
    
    def __repr__(self) -> str:
        return f"<LLMProvider(id={self.id}, name={self.name}, type={self.provider_type}, is_active={self.is_active})>"
    
    def to_dict(self, include_api_key: bool = False) -> dict:
        """
        转换为字典格式
        
        Args:
            include_api_key: 是否包含 API Key（密文）
        
        Returns:
            Provider 配置字典
        """
        result = {
            "id": self.id,
            "name": self.name,
            "provider_type": self.provider_type.value,
            "base_url": self.base_url,
            "model_name": self.model_name,
            "is_active": self.is_active,
            "is_default": self.is_default,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_api_key:
            result["api_key_encrypted"] = self.api_key_encrypted
        return result
