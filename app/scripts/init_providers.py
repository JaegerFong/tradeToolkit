#!/usr/bin/env python3
"""
初始化大模型厂家数据脚本
"""
import asyncio
import sys
import os
from datetime import datetime

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.core.database import init_db, async_session_factory
from app.core.pg_models import LLMProvider
from sqlalchemy import delete, select, func
from tradingagents.llm_clients.provider_keys import canonical_aliases

async def init_providers():
    """初始化大模型厂家数据"""
    print("开始初始化大模型厂家数据...")

    # 初始化数据库连接
    await init_db()

    # 预设厂家数据
    providers_data = [
        {
            "name": "openai",
            "display_name": "OpenAI",
            "api_base": "https://api.openai.com/v1",
            "is_active": True,
            "config": {
                "description": "OpenAI是人工智能领域的领先公司，提供GPT系列模型",
                "website": "https://openai.com",
                "api_doc_url": "https://platform.openai.com/docs",
                "supported_features": ["chat", "completion", "embedding", "image", "vision", "function_calling", "streaming"]
            }
        },
        {
            "name": "anthropic",
            "display_name": "Anthropic",
            "api_base": "https://api.anthropic.com",
            "is_active": True,
            "config": {
                "description": "Anthropic专注于AI安全研究，提供Claude系列模型",
                "website": "https://anthropic.com",
                "api_doc_url": "https://docs.anthropic.com",
                "supported_features": ["chat", "completion", "function_calling", "streaming"]
            }
        },
        {
            "name": "google",
            "display_name": "Google AI",
            "api_base": "https://generativelanguage.googleapis.com/v1beta",
            "is_active": True,
            "config": {
                "description": "Google的人工智能平台，提供Gemini系列模型",
                "website": "https://ai.google.dev",
                "api_doc_url": "https://ai.google.dev/docs",
                "supported_features": ["chat", "completion", "embedding", "vision", "function_calling", "streaming"]
            }
        },
        {
            "name": "glm",
            "display_name": "智谱AI",
            "api_base": "https://open.bigmodel.cn/api/paas/v4",
            "is_active": True,
            "config": {
                "description": "智谱AI提供GLM系列中文大模型",
                "website": "https://zhipuai.cn",
                "api_doc_url": "https://open.bigmodel.cn/doc",
                "aliases": canonical_aliases("glm"),
                "supported_features": ["chat", "completion", "embedding", "function_calling", "streaming"]
            }
        },
        {
            "name": "deepseek",
            "display_name": "DeepSeek",
            "api_base": "https://api.deepseek.com",
            "is_active": True,
            "config": {
                "description": "DeepSeek提供高性能的AI推理服务",
                "website": "https://www.deepseek.com",
                "api_doc_url": "https://platform.deepseek.com/api-docs",
                "supported_features": ["chat", "completion", "function_calling", "streaming"]
            }
        },
        {
            "name": "qwen",
            "display_name": "阿里云百炼",
            "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "is_active": True,
            "config": {
                "description": "阿里云百炼大模型服务平台，提供通义千问等模型",
                "website": "https://bailian.console.aliyun.com",
                "api_doc_url": "https://help.aliyun.com/zh/dashscope/",
                "aliases": canonical_aliases("qwen"),
                "supported_features": ["chat", "completion", "embedding", "function_calling", "streaming"]
            }
        },
        {
            "name": "siliconflow",
            "display_name": "硅基流动",
            "api_base": "https://api.siliconflow.cn/v1",
            "is_active": True,
            "config": {
                "description": "硅基流动提供高性价比的AI推理服务，支持多种开源模型",
                "website": "https://siliconflow.cn",
                "api_doc_url": "https://docs.siliconflow.cn",
                "supported_features": ["chat", "completion", "embedding", "function_calling", "streaming"]
            }
        },
        {
            "name": "302ai",
            "display_name": "302.AI",
            "api_base": "https://api.302.ai/v1",
            "is_active": True,
            "config": {
                "description": "302.AI是企业级AI聚合平台，提供多种主流大模型的统一接口",
                "website": "https://302.ai",
                "api_doc_url": "https://doc.302.ai",
                "supported_features": ["chat", "completion", "embedding", "image", "vision", "function_calling", "streaming"]
            }
        },
        {
            "name": "aihubmix",
            "display_name": "AIHubMix",
            "api_base": "https://aihubmix.com/v1",
            "is_active": True,
            "config": {
                "description": "AIHubMix 深度适配 OpenAI、Claude、Gemini、DeepSeek、智谱、千问 等全球顶级模型",
                "website": "https://aihubmix.com/?aff=2rIi",
                "api_doc_url": "https://docs.aihubmix.com/cn/quick-start",
                "supported_features": ["chat", "completion", "embedding", "vision", "function_calling", "streaming"]
            }
        }
    ]

    async with async_session_factory() as session:
        # 清除现有数据
        await session.execute(delete(LLMProvider))
        await session.commit()
        print("清除现有厂家数据")

        # 插入新数据
        for provider_data in providers_data:
            provider = LLMProvider(
                name=provider_data["name"],
                display_name=provider_data["display_name"],
                api_base=provider_data["api_base"],
                is_active=provider_data["is_active"],
                config=provider_data.get("config", {}),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(provider)

        await session.commit()
        print(f"成功初始化 {len(providers_data)} 个厂家数据")

if __name__ == "__main__":
    asyncio.run(init_providers())
