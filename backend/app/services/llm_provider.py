"""
LLM Provider 抽象层
支持多种 Provider：Ollama、OpenAI、Kimi、GLM、Custom
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

import requests
from openai import OpenAI


class ProviderType(str, Enum):
    """Provider 类型"""

    OLLAMA = "ollama"
    OPENAI = "openai"
    KIMI = "kimi"
    GLM = "glm"
    CUSTOM = "custom"


@dataclass
class LLMResponse:
    """LLM 响应数据类"""

    content: str
    reasoning_content: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    model: Optional[str] = None
    finish_reason: Optional[str] = None


@dataclass
class LLMConfig:
    """LLM 配置数据类"""

    provider_type: ProviderType
    base_url: str
    api_key: Optional[str] = None
    model_name: str = ""
    timeout: float = 30.0
    max_retries: int = 2


class LLMProviderInterface(ABC):
    """
    LLM Provider 抽象接口

    所有 LLM Provider 必须实现此接口，以提供统一的调用方式
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client: Optional[Any] = None

    @abstractmethod
    def create_client(self) -> Any:
        """
        创建并返回 OpenAI 兼容的客户端实例

        Returns:
            OpenAI 兼容客户端（openai.OpenAI 或兼容接口）
        """
        pass

    @abstractmethod
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        """
        执行聊天补全

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}, ...]
            model: 模型名称，默认使用配置中的 model_name
            temperature: 温度参数
            max_tokens: 最大 token 数
            **kwargs: 其他参数

        Returns:
            LLMResponse 响应对象
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """
        健康检查

        Returns:
            如果 Provider 可用返回 True，否则返回 False
        """
        pass

    def get_model_name(self) -> str:
        """获取模型名称"""
        return self.config.model_name


class OpenAICompatibleProvider(LLMProviderInterface):
    """
    OpenAI 兼容 Provider 基类

    适用于 OpenAI、Kimi、GLM 等 OpenAI API 兼容的 Provider
    """

    def create_client(self) -> OpenAI:
        """创建 OpenAI 兼容客户端"""
        if self._client is None:
            self._client = OpenAI(
                api_key=self.config.api_key or "sk-dummy",
                base_url=self.config.base_url,
                timeout=self.config.timeout,
                max_retries=self.config.max_retries,
            )
        return self._client

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        """执行聊天补全"""
        client = self.create_client()

        params = {
            "model": model or self.config.model_name,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens

        response = client.chat.completions.create(**params)
        message = response.choices[0].message
        reasoning_content = getattr(message, "reasoning_content", None)
        content = message.content or ""
        if not content and reasoning_content:
            content = reasoning_content

        return LLMResponse(
            content=content,
            reasoning_content=reasoning_content,
            usage=response.usage.model_dump() if response.usage else None,
            model=response.model,
            finish_reason=response.choices[0].finish_reason,
        )

    def health_check(self) -> bool:
        """健康检查：尝试列出模型"""
        try:
            client = self.create_client()
            client.models.list()
            return True
        except Exception:
            return False


class OllamaProvider(LLMProviderInterface):
    """
    Ollama 本地 Provider

    使用 requests 直接与 Ollama HTTP API 交互
    """

    def create_client(self) -> "OllamaProvider":
        """返回自身作为客户端（Ollama 不使用 OpenAI 客户端）"""
        return self

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        """执行聊天补全"""
        url = self.config.base_url.rstrip("/") + "/api/chat"

        payload = {
            "model": model or self.config.model_name,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        response = requests.post(url, json=payload, timeout=self.config.timeout)
        response.raise_for_status()

        data = response.json()

        return LLMResponse(
            content=data.get("message", {}).get("content", ""),
            usage=data.get("usage"),
            model=data.get("model"),
            finish_reason="stop" if not data.get("done", False) else None,
        )

    def health_check(self) -> bool:
        """健康检查"""
        try:
            url = self.config.base_url.rstrip("/") + "/api/tags"
            response = requests.get(url, timeout=5)
            return response.status_code == 200
        except Exception:
            return False


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI 官方 Provider"""

    pass


class KimiProvider(OpenAICompatibleProvider):
    """Moonshot Kimi Provider"""

    pass


class GLMProvider(OpenAICompatibleProvider):
    """智谱 GLM Provider"""

    pass


class CustomProvider(OpenAICompatibleProvider):
    """自定义 OpenAI 兼容 Provider"""

    pass


# Provider 工厂映射
PROVIDER_REGISTRY: Dict[ProviderType, type] = {
    ProviderType.OLLAMA: OllamaProvider,
    ProviderType.OPENAI: OpenAIProvider,
    ProviderType.KIMI: KimiProvider,
    ProviderType.GLM: GLMProvider,
    ProviderType.CUSTOM: CustomProvider,
}


def create_provider(config: LLMConfig) -> LLMProviderInterface:
    """
    创建 Provider 实例

    Args:
        config: LLM 配置

    Returns:
        LLMProviderInterface 实例

    Raises:
        ValueError: 如果 provider_type 不受支持
    """
    provider_class = PROVIDER_REGISTRY.get(config.provider_type)
    if provider_class is None:
        raise ValueError(f"不支持的 Provider 类型: {config.provider_type}")

    return provider_class(config)


# 预设 Provider 配置模板
PROVIDER_PRESETS = {
    "openai": {
        "name": "OpenAI",
        "provider_type": ProviderType.OPENAI,
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
    },
    "kimi": {
        "name": "Kimi (Moonshot)",
        "provider_type": ProviderType.KIMI,
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    },
    "glm": {
        "name": "智谱 GLM",
        "provider_type": ProviderType.GLM,
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4", "glm-4-flash", "glm-4-plus"],
    },
    "ollama": {
        "name": "Ollama (本地)",
        "provider_type": ProviderType.OLLAMA,
        "base_url": "http://localhost:11434",
        "models": [],
    },
}
