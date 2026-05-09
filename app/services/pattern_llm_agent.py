from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import requests

from app.models.pattern_screening import PatternResultDetail, PatternType
from app.services.config_service import ConfigService

logger = logging.getLogger("webapi")


_JSON_SCHEMA_HINT = {
    "type": "object",
    "required": [
        "analysis",
        "trend_expectation",
        "buy_price_range",
        "position_suggestion",
        "stop_loss",
        "risk_points",
        "invalid_conditions",
        "pattern_score",
        "recommendation_score",
        "brief_reason",
        "pattern_breakdown",
    ],
}


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """
    尝试从模型输出中提取 JSON（允许输出前后有少量文本）。
    """
    if not text:
        return None
    text = text.strip()

    # 优先直接 parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # 尝试提取首个 {...} 块
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


class PatternLLMAgent:
    """
    技术形态选股 LLM 复核器（首期：走 OpenAI 兼容 /chat/completions）
    - 若无法获取有效 LLM 配置，调用方应降级为规则引擎输出
    """

    def __init__(self):
        self.config_service = ConfigService()

    async def _pick_llm(self) -> Optional[Tuple[str, str, str]]:
        """
        返回 (api_base, api_key, model_name)
        """
        config = await self.config_service.get_system_config()
        llms = list(getattr(config, "llm_configs", []) or [])
        if not llms:
            return None

        # 优先 default_llm
        default = getattr(config, "default_llm", None)
        picked = None
        if default:
            for c in llms:
                if getattr(c, "model_name", None) == default:
                    picked = c
                    break
        if picked is None:
            # 退化：取第一个 enabled
            picked = next((c for c in llms if getattr(c, "enabled", True)), llms[0])

        api_base = getattr(picked, "api_base", None) or ""
        api_key = getattr(picked, "api_key", None) or ""
        model_name = getattr(picked, "model_name", None) or ""
        if not api_base or not model_name:
            return None
        if not api_key:
            # api_key 可能由厂家配置注入，但若为空则无法调用
            return None

        # 归一化 base url：若末尾无 /vN，则加 /v1
        api_base_normalized = api_base.rstrip("/")
        if not re.search(r"/v\d+$", api_base_normalized):
            api_base_normalized = api_base_normalized + "/v1"

        return api_base_normalized, api_key, model_name

    async def review(
        self,
        detail: PatternResultDetail,
        evidence: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        picked = await self._pick_llm()
        if not picked:
            return None

        api_base, api_key, model = picked
        url = f"{api_base}/chat/completions"

        pattern_cn = "老鸭头" if detail.pattern_type == PatternType.LAOYATOU else "N字形态"

        system_msg = (
            "你是技术形态选股智能体。你只能使用输入数据，不得编造行情。"
            "你必须输出严格 JSON，不要输出多余文本。"
            "禁止输出隐藏推理链，只输出可审计的依据摘要、风险点与失效条件。"
        )

        user_msg = {
            "task": "review_pattern_candidate",
            "schema_hint": _JSON_SCHEMA_HINT,
            "input": {
                "code": detail.code,
                "name": detail.name,
                "pattern_type": detail.pattern_type,
                "pattern_name": pattern_cn,
                "rule_engine": {
                    "pattern_score": detail.pattern_score,
                    "recommendation_score": detail.recommendation_score,
                    "pattern_breakdown": detail.pattern_breakdown,
                    "evidence": evidence,
                },
            },
            "output_requirements": {
                "pattern_score": "0-100 int",
                "recommendation_score": "0-100 int",
                "analysis": "string",
                "trend_expectation": "string",
                "buy_price_range": "[low, high] numbers",
                "position_suggestion": "string",
                "stop_loss": "number",
                "risk_points": "string[] (>=1)",
                "invalid_conditions": "string[] (>=1)",
                "brief_reason": "string",
                "pattern_breakdown": "object with stage->string",
            },
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": json.dumps(user_msg, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "max_tokens": 1200,
        }

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            return _extract_json(content)
        except Exception as e:
            logger.warning(f"[pattern_llm_agent] review failed: {e}")
            return None


def get_pattern_llm_agent() -> PatternLLMAgent:
    # 轻量对象，直接每次创建也行；这里提供函数方便未来注入缓存
    return PatternLLMAgent()

