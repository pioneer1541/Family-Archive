#!/usr/bin/env python3
"""
LLM 配置迁移脚本

将现有 model 配置迁移为新的 model_key 格式（local:xxx 或 cloud:xxx）
向后兼容：保留旧配置，添加新配置

用法:
    cd backend && python3 app/scripts/migrate_llm_config.py [--dry-run]
"""

import argparse
import sys
import os

# 添加项目路径（从 backend 目录运行）
if os.path.exists("app"):
    sys.path.insert(0, os.getcwd())
else:
    # 从脚本目录运行
    backend_dir = os.path.join(os.path.dirname(__file__), "..")
    sys.path.insert(0, backend_dir)

from sqlalchemy import inspect
from sqlalchemy.orm import Session
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.llm_models.llm_provider import LLMProvider, ProviderType


def table_exists(table_name: str) -> bool:
    """检查表是否存在"""
    try:
        inspector = inspect(engine)
        return table_name in inspector.get_table_names()
    except Exception:
        return False


def create_default_ollama_provider(db: Session, dry_run: bool = False):
    """创建默认 Ollama Provider"""
    settings = get_settings()

    if dry_run:
        print("[DRY-RUN] 将检查/创建默认 Ollama Provider")
        return

    # 检查是否已存在默认 Ollama Provider
    existing = (
        db.query(LLMProvider)
        .filter(
            LLMProvider.provider_type == ProviderType.OLLAMA,
            LLMProvider.is_default is True,
        )
        .first()
    )

    if existing:
        print(f"默认 Ollama Provider 已存在: {existing.id}")
        return existing

    # 创建新的默认 Ollama Provider
    provider = LLMProvider(
        name="Ollama (本地)",
        provider_type=ProviderType.OLLAMA,
        base_url=settings.ollama_base_url,
        api_key_encrypted=None,
        model_name=settings.summary_model,
        is_active=True,
        is_default=True,
    )

    db.add(provider)
    db.commit()
    db.refresh(provider)

    print(f"创建默认 Ollama Provider: {provider.id}")
    return provider


def migrate_model_config_to_key(model: str) -> str:
    """将模型名称迁移为 model_key 格式"""
    if not model:
        return "local:"

    if model.startswith("local:") or model.startswith("cloud:"):
        return model

    return f"local:{model}"


def migrate_settings_config():
    """迁移 settings 中的模型配置建议"""
    settings = get_settings()

    models_to_migrate = {
        "planner_model": settings.planner_model,
        "synthesizer_model": settings.synthesizer_model,
        "embed_model": settings.embed_model,
        "summary_model": settings.summary_model,
        "category_model": settings.category_model,
        "friendly_name_model": settings.friendly_name_model,
        "vl_extract_model": settings.vl_extract_model,
    }

    print("\n=== 模型配置迁移建议 ===")
    print("以下配置项建议迁移为新的 model_key 格式:\n")

    for config_name, model in models_to_migrate.items():
        new_key = migrate_model_config_to_key(model)
        print(f"  {config_name}:")
        print(f"    当前: {model}")
        print(f"    建议: {new_key}")
        print()

    print("注意: 实际配置更新需要通过环境变量或配置文件手动完成。")
    print("旧的纯模型名称格式仍然兼容，会自动作为 local:xxx 处理。\n")


def add_cloud_provider_presets(db: Session, dry_run: bool = False):
    """添加云端 Provider 预设模板"""
    presets = [
        {
            "name": "OpenAI",
            "provider_type": ProviderType.OPENAI,
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini",
        },
        {
            "name": "Kimi (Moonshot)",
            "provider_type": ProviderType.KIMI,
            "base_url": "https://api.moonshot.cn/v1",
            "model_name": "moonshot-v1-8k",
        },
        {
            "name": "智谱 GLM",
            "provider_type": ProviderType.GLM,
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "model_name": "glm-4",
        },
    ]

    print("=== 云端 Provider 预设模板 ===\n")

    for preset in presets:
        if dry_run:
            print(f"  {preset['name']}: [DRY-RUN] 将创建（如果不存在）")
            continue

        # 检查是否已存在
        existing = (
            db.query(LLMProvider)
            .filter(LLMProvider.provider_type == preset["provider_type"])
            .first()
        )

        if existing:
            print(f"  {preset['name']}: 已存在 (ID: {existing.id})")
            continue

        provider = LLMProvider(
            name=preset["name"],
            provider_type=preset["provider_type"],
            base_url=preset["base_url"],
            api_key_encrypted=None,
            model_name=preset["model_name"],
            is_active=False,
            is_default=False,
        )

        db.add(provider)
        print(f"  {preset['name']}: 已创建")

    if not dry_run:
        db.commit()

    print()


def run_migration(dry_run: bool = False):
    """运行迁移"""
    print("=" * 50)
    print("Family Vault LLM 配置迁移工具")
    print("=" * 50)

    if dry_run:
        print("\n[DRY-RUN 模式] 仅预览，不写入数据库\n")

    try:
        # 检查/创建表
        if not dry_run:
            print("创建 llm_providers 表（如果不存在）...")
            Base.metadata.create_all(bind=engine, tables=[LLMProvider.__table__])
            print("完成\n")
        else:
            print("[DRY-RUN] 将创建 llm_providers 表（如果不存在）\n")

        # 开始数据库会话
        db = SessionLocal()

        try:
            # 1. 创建默认 Ollama Provider
            print("=== 步骤 1: 创建默认 Ollama Provider ===\n")
            create_default_ollama_provider(db, dry_run)
            print()

            # 2. 添加云端 Provider 预设
            print("=== 步骤 2: 添加云端 Provider 预设模板 ===\n")
            add_cloud_provider_presets(db, dry_run)

            # 3. 迁移配置建议
            print("=== 步骤 3: 配置迁移建议 ===\n")
            migrate_settings_config()

            if dry_run:
                print("[DRY-RUN 完成] 以上为预览内容，实际执行请去掉 --dry-run 参数")
            else:
                print("迁移完成！")
                print("\n后续步骤:")
                print("  1. 在环境变量或配置文件中更新模型配置为 model_key 格式")
                print("  2. 通过 API 或数据库配置云端 Provider 的 API Key")
                print("  3. 启用需要的云端 Provider")

        finally:
            db.close()

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


def main():
    parser = argparse.ArgumentParser(description="Family Vault LLM 配置迁移工具")
    parser.add_argument(
        "--dry-run", action="store_true", help="仅预览，不实际写入数据库"
    )

    args = parser.parse_args()

    return run_migration(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
