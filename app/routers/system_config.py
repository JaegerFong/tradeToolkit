from fastapi import APIRouter, Depends, HTTPException, status
from typing import Any, Dict
import re
import logging

from app.core.config import settings
from app.routers.auth_db import get_current_user

router = APIRouter()
logger = logging.getLogger("webapi")

SENSITIVE_KEYS = {
    "MONGODB_PASSWORD",
    "REDIS_PASSWORD",
    "JWT_SECRET",
    "CSRF_SECRET",
    "STOCK_DATA_API_KEY",
    "REFRESH_TOKEN_EXPIRE_DAYS",
}

MASK = "***"


def _mask_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in SENSITIVE_KEYS:
        return MASK
    # Mask URLs that may contain credentials
    if key in {"MONGO_URI", "REDIS_URL"} and isinstance(value, str):
        v = value
        v = re.sub(r"(mongodb://[^:/?#]+):([^@/]+)@", r"\1:***@", v)
        v = re.sub(r"(redis://:)[^@/]+@", r"\1***@", v)
        return v
    return value


def _build_summary() -> Dict[str, Any]:
    raw = settings.model_dump()
    raw["MONGO_URI"] = settings.MONGO_URI
    raw["REDIS_URL"] = settings.REDIS_URL

    summary: Dict[str, Any] = {}
    for k, v in raw.items():
        summary[k] = _mask_value(k, v)
    return summary


@router.get("/config/summary", tags=["system"], summary="配置概要（已屏蔽敏感项，需管理员）")
async def get_config_summary(current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """
    返回当前生效的设置概要。敏感字段将以 *** 掩码显示。
    访问控制：需管理员身份。
    """
    if not current_user.get("is_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return {"settings": _build_summary()}


@router.get("/config/validate", tags=["system"], summary="验证配置完整性")
async def validate_config():
    """
    验证系统配置的完整性和有效性。
    返回验证结果，包括缺少的配置项和无效的配置。

    验证内容：
    1. 环境变量配置（.env 文件）
    2. PostgreSQL 中存储的配置（大模型、数据源等）

    注意：此接口会先从 PostgreSQL 重载配置到环境变量，然后再验证。
    """
    from app.core.startup_validator import StartupValidator
    from app.core.config_bridge import bridge_config_to_env
    from app.services.config_service import config_service

    try:
        # 步骤1: 重载配置 - 从 PostgreSQL 读取配置并桥接到环境变量
        try:
            bridge_config_to_env()
            logger.info("配置已从数据库重载到环境变量")
        except Exception as e:
            logger.warning(f"配置重载失败: {e}，将验证 .env 文件中的配置")

        # 步骤2: 验证环境变量配置
        validator = StartupValidator()
        env_result = validator.validate()

        # 步骤3: 验证数据库中的配置
        mongodb_validation = {
            "llm_providers": [],
            "data_source_configs": [],
            "warnings": []
        }

        try:
            from app.utils.api_key_utils import (
                is_valid_api_key,
                get_env_api_key_for_provider
            )

            # 从 PostgreSQL 读取 LLM 厂家配置
            from app.core.database import async_session_factory
            from app.core.pg_models import LLMProvider
            from sqlalchemy import select

            async with async_session_factory() as session:
                result = await session.execute(select(LLMProvider))
                llm_providers = result.scalars().all()

            logger.info(f"获取到 {len(llm_providers)} 个大模型厂家")

            for provider in llm_providers:
                if not provider.is_active:
                    continue

                validation_item = {
                    "name": provider.name,
                    "display_name": provider.display_name,
                    "is_active": provider.is_active,
                    "has_api_key": False,
                    "status": "未配置",
                    "source": None,
                    "mongodb_configured": False,
                    "env_configured": False
                }

                db_key_valid = is_valid_api_key(provider.api_key)
                validation_item["mongodb_configured"] = db_key_valid

                env_key = get_env_api_key_for_provider(provider.name)
                env_key_valid = env_key is not None
                validation_item["env_configured"] = env_key_valid

                if db_key_valid:
                    validation_item["has_api_key"] = True
                    validation_item["status"] = "已配置"
                    validation_item["source"] = "database"
                elif env_key_valid:
                    validation_item["has_api_key"] = True
                    validation_item["status"] = "已配置（环境变量）"
                    validation_item["source"] = "environment"
                    mongodb_validation["warnings"].append(
                        f"大模型厂家 {provider.display_name} 使用环境变量配置，建议在数据库中配置以便统一管理"
                    )
                else:
                    validation_item["status"] = "未配置"
                    mongodb_validation["warnings"].append(
                        f"大模型厂家 {provider.display_name} 已启用但未配置有效的 API Key"
                    )

                mongodb_validation["llm_providers"].append(validation_item)

            # 验证数据源配置
            from app.utils.api_key_utils import (
                is_valid_api_key,
                get_env_api_key_for_datasource
            )

            system_config = await config_service.get_system_config()
            if system_config and system_config.data_source_configs:
                logger.info(f"获取到 {len(system_config.data_source_configs)} 个数据源配置")

                for ds_config in system_config.data_source_configs:
                    if not ds_config.enabled:
                        continue

                    validation_item = {
                        "name": ds_config.name,
                        "type": ds_config.type,
                        "enabled": ds_config.enabled,
                        "has_api_key": False,
                        "status": "未配置",
                        "source": None,
                        "mongodb_configured": False,
                        "env_configured": False
                    }

                    if ds_config.type in ["akshare", "yahoo"]:
                        validation_item["has_api_key"] = True
                        validation_item["status"] = "已配置（无需密钥）"
                        validation_item["source"] = "builtin"
                        validation_item["mongodb_configured"] = True
                        validation_item["env_configured"] = True
                    else:
                        db_key_valid = is_valid_api_key(ds_config.api_key)
                        validation_item["mongodb_configured"] = db_key_valid

                        ds_type = ds_config.type.value if hasattr(ds_config.type, 'value') else ds_config.type
                        env_key = get_env_api_key_for_datasource(ds_type)
                        env_key_valid = env_key is not None
                        validation_item["env_configured"] = env_key_valid

                        if db_key_valid:
                            validation_item["has_api_key"] = True
                            validation_item["status"] = "已配置"
                            validation_item["source"] = "database"
                        elif env_key_valid:
                            validation_item["has_api_key"] = True
                            validation_item["status"] = "已配置（环境变量）"
                            validation_item["source"] = "environment"
                            mongodb_validation["warnings"].append(
                                f"数据源 {ds_config.name} 使用环境变量配置，建议在数据库中配置以便统一管理"
                            )
                        else:
                            validation_item["status"] = "未配置"
                            mongodb_validation["warnings"].append(
                                f"数据源 {ds_config.name} 已启用但未配置有效的 API Key"
                            )

                    mongodb_validation["data_source_configs"].append(validation_item)

        except Exception as e:
            logger.error(f"验证数据库配置失败: {e}", exc_info=True)
            mongodb_validation["warnings"].append(f"数据库配置验证失败: {str(e)}")

        logger.info(f"数据库验证结果: {len(mongodb_validation['llm_providers'])} 个大模型厂家, {len(mongodb_validation['data_source_configs'])} 个数据源, {len(mongodb_validation['warnings'])} 个警告")

        overall_success = env_result.success

        return {
            "success": True,
            "data": {
                "env_validation": {
                    "success": env_result.success,
                    "missing_required": [
                        {"key": config.key, "description": config.description}
                        for config in env_result.missing_required
                    ],
                    "missing_recommended": [
                        {"key": config.key, "description": config.description}
                        for config in env_result.missing_recommended
                    ],
                    "invalid_configs": [
                        {"key": config.key, "error": config.description}
                        for config in env_result.invalid_configs
                    ],
                    "warnings": env_result.warnings
                },
                "mongodb_validation": mongodb_validation,
                "success": overall_success
            },
            "message": "配置验证完成"
        }
    except Exception as e:
        logger.error(f"配置验证失败: {e}", exc_info=True)
        return {
            "success": False,
            "data": None,
            "message": f"配置验证失败: {str(e)}"
        }
