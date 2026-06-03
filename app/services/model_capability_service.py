"""
模型能力管理服务

提供模型能力评估、验证和推荐功能。
"""

from typing import Tuple, Dict, Optional, List, Any
from app.constants.model_capabilities import (
    ANALYSIS_DEPTH_REQUIREMENTS,
    DEFAULT_MODEL_CAPABILITIES,
    CAPABILITY_DESCRIPTIONS,
    ModelRole,
    ModelFeature
)
from app.core.unified_config import unified_config
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.pg_models import SystemConfig as SystemConfigModel, LLMProvider, ModelCatalog
import logging
import re

logger = logging.getLogger(__name__)


class ModelCapabilityService:
    """模型能力管理服务"""

    def _parse_aggregator_model_name(self, model_name: str) -> Tuple[Optional[str], str]:
        """解析聚合渠道的模型名称"""
        if "/" in model_name:
            parts = model_name.split("/", 1)
            if len(parts) == 2:
                provider_hint = parts[0].lower()
                original_model = parts[1]

                provider_map = {
                    "openai": "openai",
                    "anthropic": "anthropic",
                    "google": "google",
                    "deepseek": "deepseek",
                    "alibaba": "qwen",
                    "qwen": "qwen",
                    "zhipu": "glm",
                    "glm": "glm",
                    "baidu": "baidu",
                    "moonshot": "moonshot"
                }
                provider = provider_map.get(provider_hint)
                return provider, original_model
        return None, model_name

    def _get_model_capability_with_mapping(self, model_name: str) -> Tuple[int, Optional[str]]:
        """获取模型能力等级（支持聚合渠道映射）"""
        if model_name in DEFAULT_MODEL_CAPABILITIES:
            return DEFAULT_MODEL_CAPABILITIES[model_name]["capability_level"], None

        provider, original_model = self._parse_aggregator_model_name(model_name)
        if original_model and original_model != model_name:
            if original_model in DEFAULT_MODEL_CAPABILITIES:
                logger.info(f"聚合渠道模型映射: {model_name} -> {original_model}")
                return DEFAULT_MODEL_CAPABILITIES[original_model]["capability_level"], original_model

        return 2, None

    def get_model_capability(self, model_name: str) -> int:
        """获取模型的能力等级（支持聚合渠道模型映射）"""
        # 1. 优先从数据库配置读取
        try:
            llm_configs = unified_config.get_llm_configs()
            for config in llm_configs:
                if config.model_name == model_name:
                    return getattr(config, 'capability_level', 2)
        except Exception as e:
            logger.warning(f"从配置读取模型能力失败: {e}")

        # 2. 从默认映射表读取
        capability, mapped_model = self._get_model_capability_with_mapping(model_name)
        if mapped_model:
            logger.info(f"使用映射模型 {mapped_model} 的能力等级: {capability}")
        return capability

    async def get_model_config(self, model_name: str) -> Dict[str, Any]:
        """
        获取模型的完整配置信息（支持聚合渠道模型映射）
        """
        # 1. 优先从 PostgreSQL 数据库配置读取
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(SystemConfigModel).where(
                        SystemConfigModel.is_active == True
                    ).order_by(SystemConfigModel.version.desc()).limit(1)
                )
                doc = result.scalar_one_or_none()

                if doc and doc.llm_configs:
                    llm_configs = doc.llm_configs
                    if isinstance(llm_configs, list):
                        for config_dict in llm_configs:
                            if isinstance(config_dict, dict) and config_dict.get("model_name") == model_name:
                                features_str = config_dict.get('features', [])
                                features_enum = []
                                for feature_str in features_str:
                                    try:
                                        features_enum.append(ModelFeature(feature_str))
                                    except ValueError:
                                        logger.warning(f"未知的特性值: {feature_str}")

                                roles_str = config_dict.get('suitable_roles', ["both"])
                                roles_enum = []
                                for role_str in roles_str:
                                    try:
                                        roles_enum.append(ModelRole(role_str))
                                    except ValueError:
                                        logger.warning(f"未知的角色值: {role_str}")

                                if not roles_enum:
                                    roles_enum = [ModelRole.BOTH]

                                logger.info(f"[PG配置] {model_name}: features={features_enum}, roles={roles_enum}")

                                return {
                                    "model_name": config_dict.get("model_name"),
                                    "capability_level": config_dict.get('capability_level', 2),
                                    "suitable_roles": roles_enum,
                                    "features": features_enum,
                                    "recommended_depths": config_dict.get('recommended_depths', ["快速", "基础", "标准"]),
                                    "performance_metrics": config_dict.get('performance_metrics', None)
                                }

                # 尝试从 llm_providers 和 model_catalog 查询
                result = await session.execute(
                    select(ModelCatalog).where(ModelCatalog.model_name == model_name, ModelCatalog.is_active == True).limit(1)
                )
                mc = result.scalar_one_or_none()
                if mc:
                    return {
                        "model_name": mc.model_name,
                        "capability_level": mc.capability_level or 2,
                        "suitable_roles": mc.suitable_roles or [ModelRole.BOTH],
                        "features": mc.features or [ModelFeature.TOOL_CALLING],
                        "recommended_depths": ["快速", "基础", "标准"],
                        "performance_metrics": mc.config or {},
                    }

        except Exception as e:
            logger.warning(f"从 PostgreSQL 读取模型信息失败: {e}", exc_info=True)

        # 2. 从默认映射表读取（直接匹配）
        if model_name in DEFAULT_MODEL_CAPABILITIES:
            return DEFAULT_MODEL_CAPABILITIES[model_name]

        # 3. 尝试聚合渠道模型映射
        provider, original_model = self._parse_aggregator_model_name(model_name)
        if original_model and original_model != model_name:
            if original_model in DEFAULT_MODEL_CAPABILITIES:
                logger.info(f"聚合渠道模型映射: {model_name} -> {original_model}")
                config = DEFAULT_MODEL_CAPABILITIES[original_model].copy()
                config["model_name"] = model_name
                config["_mapped_from"] = original_model
                return config

        # 4. 返回默认配置
        logger.warning(f"未找到模型 {model_name} 的配置，使用默认配置")
        return {
            "model_name": model_name,
            "capability_level": 2,
            "suitable_roles": [ModelRole.BOTH],
            "features": [ModelFeature.TOOL_CALLING],
            "recommended_depths": ["快速", "基础", "标准"],
            "performance_metrics": {"speed": 3, "cost": 3, "quality": 3}
        }

    def validate_model_pair(
        self,
        quick_model: str,
        deep_model: str,
        research_depth: str
    ) -> Dict[str, Any]:
        """验证模型对是否适合当前分析深度"""
        logger.info(f"开始验证模型对: quick={quick_model}, deep={deep_model}, depth={research_depth}")

        requirements = ANALYSIS_DEPTH_REQUIREMENTS.get(research_depth, ANALYSIS_DEPTH_REQUIREMENTS["标准"])
        logger.info(f"分析深度要求: {requirements}")

        quick_config = self.get_model_config(quick_model)
        deep_config = self.get_model_config(deep_model)

        result = {
            "valid": True,
            "warnings": [],
            "recommendations": []
        }

        # 检查快速模型
        quick_level = quick_config["capability_level"]
        if quick_level < requirements["quick_model_min"]:
            warning = f"快速模型 {quick_model} (能力等级{quick_level}) 低于 {research_depth} 分析的建议等级({requirements['quick_model_min']})"
            result["warnings"].append(warning)
            logger.warning(warning)

        quick_roles = quick_config.get("suitable_roles", [])
        if ModelRole.QUICK_ANALYSIS not in quick_roles and ModelRole.BOTH not in quick_roles:
            warning = f"模型 {quick_model} 不是为快速分析优化的，可能影响数据收集效率"
            result["warnings"].append(warning)

        quick_features = quick_config.get("features", [])
        if ModelFeature.TOOL_CALLING not in quick_features:
            result["valid"] = False
            warning = f"快速模型 {quick_model} 不支持工具调用，无法完成数据收集任务"
            result["warnings"].append(warning)

        # 检查深度模型
        deep_level = deep_config["capability_level"]
        if deep_level < requirements["deep_model_min"]:
            result["valid"] = False
            warning = f"深度模型 {deep_model} (能力等级{deep_level}) 不满足 {research_depth} 分析的最低要求(等级{requirements['deep_model_min']})"
            result["warnings"].append(warning)
            result["recommendations"].append(
                self._recommend_model("deep", requirements["deep_model_min"])
            )

        deep_roles = deep_config.get("suitable_roles", [])
        if ModelRole.DEEP_ANALYSIS not in deep_roles and ModelRole.BOTH not in deep_roles:
            warning = f"模型 {deep_model} 不是为深度推理优化的，可能影响分析质量"
            result["warnings"].append(warning)

        for feature in requirements["required_features"]:
            if feature == ModelFeature.REASONING:
                deep_features = deep_config.get("features", [])
                if feature not in deep_features:
                    warning = f"{research_depth} 分析建议使用具有强推理能力的深度模型"
                    result["warnings"].append(warning)

        logger.info(f"验证结果: valid={result['valid']}, warnings={len(result['warnings'])}条")
        return result

    def recommend_models_for_depth(
        self,
        research_depth: str
    ) -> Tuple[str, str]:
        """根据分析深度推荐合适的模型对"""
        requirements = ANALYSIS_DEPTH_REQUIREMENTS.get(research_depth, ANALYSIS_DEPTH_REQUIREMENTS["标准"])

        try:
            llm_configs = unified_config.get_llm_configs()
            enabled_models = [c for c in llm_configs if c.enabled]
        except Exception as e:
            logger.error(f"获取模型配置失败: {e}")
            return self._get_default_models()

        if not enabled_models:
            logger.warning("没有启用的模型，使用默认配置")
            return self._get_default_models()

        quick_candidates = []
        for m in enabled_models:
            roles = getattr(m, 'suitable_roles', [ModelRole.BOTH])
            level = getattr(m, 'capability_level', 2)
            features = getattr(m, 'features', [])

            if (ModelRole.QUICK_ANALYSIS in roles or ModelRole.BOTH in roles) and \
               level >= requirements["quick_model_min"] and \
               ModelFeature.TOOL_CALLING in features:
                quick_candidates.append(m)

        deep_candidates = []
        for m in enabled_models:
            roles = getattr(m, 'suitable_roles', [ModelRole.BOTH])
            level = getattr(m, 'capability_level', 2)

            if (ModelRole.DEEP_ANALYSIS in roles or ModelRole.BOTH in roles) and \
               level >= requirements["deep_model_min"]:
                deep_candidates.append(m)

        quick_candidates.sort(
            key=lambda x: (
                getattr(x, 'capability_level', 2),
                -getattr(x, 'performance_metrics', {}).get("cost", 3) if getattr(x, 'performance_metrics', None) else 0
            ),
            reverse=True
        )

        deep_candidates.sort(
            key=lambda x: (
                getattr(x, 'capability_level', 2),
                getattr(x, 'performance_metrics', {}).get("quality", 3) if getattr(x, 'performance_metrics', None) else 0
            ),
            reverse=True
        )

        quick_model = quick_candidates[0].model_name if quick_candidates else None
        deep_model = deep_candidates[0].model_name if deep_candidates else None

        if not quick_model or not deep_model:
            return self._get_default_models()

        logger.info(
            f"为 {research_depth} 分析推荐模型: "
            f"quick={quick_model} (角色:快速分析), "
            f"deep={deep_model} (角色:深度推理)"
        )

        return quick_model, deep_model

    def _get_default_models(self) -> Tuple[str, str]:
        """获取默认模型对"""
        try:
            quick_model = unified_config.get_quick_analysis_model()
            deep_model = unified_config.get_deep_analysis_model()
            logger.info(f"使用系统默认模型: quick={quick_model}, deep={deep_model}")
            return quick_model, deep_model
        except Exception as e:
            logger.error(f"获取默认模型失败: {e}")
            return "qwen-turbo", "qwen-plus"

    def _recommend_model(self, model_type: str, min_level: int) -> str:
        """推荐满足要求的模型"""
        try:
            llm_configs = unified_config.get_llm_configs()
            for config in llm_configs:
                if config.enabled and getattr(config, 'capability_level', 2) >= min_level:
                    display_name = config.model_display_name or config.model_name
                    return f"建议使用: {display_name}"
        except Exception as e:
            logger.warning(f"推荐模型失败: {e}")
        return "建议升级模型配置"


# 单例
_model_capability_service = None


def get_model_capability_service() -> ModelCapabilityService:
    """获取模型能力服务单例"""
    global _model_capability_service
    if _model_capability_service is None:
        _model_capability_service = ModelCapabilityService()
    return _model_capability_service
