"""
LLM Router 路由器
根据 model_key 选择本地 Ollama 或云端 Provider
支持回退机制
"""

import re
from dataclasses import dataclass
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm_models.llm_provider import LLMProvider, ProviderType
from app.logging_utils import get_logger
from app.runtime_config import get_model_setting
from app.services.llm_provider import (
    LLMConfig,
    LLMProviderInterface,
    create_provider,
)
from app.services.llm_provider import (
    ProviderType as ServiceProviderType,
)
from app.utils.encryption import decrypt

logger = get_logger(__name__)
settings = get_settings()
_UUID_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass
class ModelKey:
    """
    解析后的 model_key

    格式: local:<model_name> 或 cloud:<provider_name>/<model_name>
    示例:
        - local:qwen3:4b-instruct
        - cloud:kimi/moonshot-v1-8k
        - cloud:openai/gpt-4o
    """

    source: str  # "local" 或 "cloud"
    model_name: str
    provider_name: Optional[str] = None  # 仅 cloud 时有

    @classmethod
    def parse(cls, model_key: str) -> "ModelKey":
        """
        解析 model_key 字符串

        Args:
            model_key: 格式为 local:<model> 或 cloud:<provider>/<model>

        Returns:
            ModelKey 对象
        """
        value = str(model_key or "").strip()
        if ":" not in value:
            # 向后兼容：没有前缀的默认为 local
            return cls(source="local", model_name=value)

        parts = value.split(":", 1)
        source = str(parts[0] or "").strip()
        rest = str(parts[1] or "").strip()

        if source == "local":
            return cls(source="local", model_name=rest)
        if source == "cloud":
            if "/" in rest:
                provider_name, model_name = rest.split("/", 1)
                return cls(source="cloud", model_name=model_name, provider_name=provider_name)
            # cloud:<model> 格式，provider_name 为 None
            return cls(source="cloud", model_name=rest)

        # 新格式：{provider_id}:{model_name}，provider_id 为 UUID。
        # 旧格式本地模型（如 qwen3:4b-instruct）仍按 local 处理。
        if _UUID_PREFIX_RE.match(source):
            return cls(source="cloud", model_name=rest, provider_name=source)

        # 未知前缀，作为向后兼容处理
        logger.warning(f"未知的 model_key 前缀: {source}，作为 local 处理")
        return cls(source="local", model_name=value)

    def __str__(self) -> str:
        if self.source == "local":
            return f"local:{self.model_name}"
        else:
            if self.provider_name:
                return f"cloud:{self.provider_name}/{self.model_name}"
            return f"cloud:{self.model_name}"


class LLMRouter:
    """
    LLM 路由器

    根据 model_key 路由到对应的 Provider，支持回退机制
    """

    def __init__(self):
        self._providers: Dict[str, LLMProviderInterface] = {}
        self._local_provider: Optional[LLMProviderInterface] = None

    def _get_local_provider(self, db: Session | None = None) -> LLMProviderInterface:
        """获取本地 Ollama Provider（单例）"""
        if self._local_provider is None:
            config = LLMConfig(
                provider_type=ServiceProviderType.OLLAMA,
                base_url=settings.ollama_base_url,
                model_name=get_model_setting("summary_model", db),  # 使用默认模型
            )
            self._local_provider = create_provider(config)
        return self._local_provider

    def _get_cloud_provider(self, db: Session, provider_name: Optional[str] = None) -> Optional[LLMProviderInterface]:
        """
        获取云端 Provider

        Args:
            db: 数据库会话
            provider_name: Provider 名称或 ID，为 None 时返回默认 Provider

        Returns:
            LLMProviderInterface 实例，如果没有可用的返回 None
        """
        # 构建缓存 key
        cache_key = provider_name or "__default__"

        if cache_key in self._providers:
            return self._providers[cache_key]

        # 查询数据库
        query = db.query(LLMProvider).filter(LLMProvider.is_active.is_(True))

        if provider_name:
            # 按名称或 ID 匹配
            query = query.filter((LLMProvider.name == provider_name) | (LLMProvider.id == provider_name))
        else:
            # 获取默认 Provider
            query = query.filter(LLMProvider.is_default.is_(True))

        provider_record = query.first()

        if not provider_record:
            return None

        # 解密 API Key
        api_key = None
        if provider_record.api_key_encrypted:
            api_key = decrypt(provider_record.api_key_encrypted)

        # 转换 ProviderType
        provider_type_map = {
            ProviderType.OLLAMA: ServiceProviderType.OLLAMA,
            ProviderType.OPENAI: ServiceProviderType.OPENAI,
            ProviderType.KIMI: ServiceProviderType.KIMI,
            ProviderType.GLM: ServiceProviderType.GLM,
            ProviderType.CUSTOM: ServiceProviderType.CUSTOM,
        }

        config = LLMConfig(
            provider_type=provider_type_map.get(provider_record.provider_type, ServiceProviderType.CUSTOM),
            base_url=provider_record.base_url,
            api_key=api_key,
            model_name=provider_record.model_name,
        )

        provider = create_provider(config)
        self._providers[cache_key] = provider
        return provider

    def get_llm_client(self, db: Session, model_key: str, fallback: bool = True) -> tuple[LLMProviderInterface, str]:
        """
        获取 LLM 客户端

        Args:
            db: 数据库会话
            model_key: 模型 key，格式为 local:<model> 或 cloud:<provider>/<model>
            fallback: 如果指定的 Provider 不可用，是否回退到本地 Ollama

        Returns:
            (provider, actual_model_name) 元组
        """
        parsed = ModelKey.parse(model_key)

        if parsed.source == "local":
            provider = self._get_local_provider(db)
            model_name = parsed.model_name

        else:
            provider = self._get_cloud_provider(db, parsed.provider_name)
            model_name = parsed.model_name

            if provider is None and fallback:
                logger.warning(f"云端 Provider {parsed.provider_name} 不可用，回退到本地 Ollama")
                provider = self._get_local_provider(db)
                model_name = get_model_setting("summary_model", db)

        return provider, model_name

    def clear_cache(self):
        """清除 Provider 缓存（用于配置更新后）"""
        self._providers.clear()
        self._local_provider = None


# 全局路由器实例
_router: Optional[LLMRouter] = None


def get_router() -> LLMRouter:
    """获取 LLM Router 单例"""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


def get_llm_client(db: Session, model_key: str, fallback: bool = True) -> tuple[LLMProviderInterface, str]:
    """
    便捷函数：获取 LLM 客户端

    Args:
        db: 数据库会话
        model_key: 模型 key，格式为 local:<model> 或 cloud:<provider>/<model>
            向后兼容：纯模型名称（如 qwen3:4b-instruct）会作为 local 处理
        fallback: 是否启用回退机制

    Returns:
        (provider, actual_model_name) 元组

    示例:
        >>> provider, model = get_llm_client(db, "local:qwen3:4b-instruct")
        >>> provider, model = get_llm_client(db, "cloud:kimi/moonshot-v1-8k")
        >>> provider, model = get_llm_client(db, "qwen3:4b-instruct")  # 向后兼容
    """
    return get_router().get_llm_client(db, model_key, fallback)


def migrate_legacy_model_key(model: str, db: Session | None = None) -> str:
    """
    将旧格式的模型名称迁移为新的 model_key 格式

    Args:
        model: 旧格式模型名称（如 qwen3:4b-instruct）

    Returns:
        新格式 model_key（如 local:qwen3:4b-instruct）
    """
    if not model:
        return "local:" + get_model_setting("summary_model", db)

    # 如果已经是新格式，直接返回
    if model.startswith("local:") or model.startswith("cloud:"):
        return model

    # 旧格式迁移为 local:<model>
    return f"local:{model}"
