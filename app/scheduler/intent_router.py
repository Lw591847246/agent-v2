# app/scheduler/intent_router.py
"""双层 LLM 意图路由：L0 fast-path → L1 领域 → L2 意图 → 难例升级 → 降级兜底。"""
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.core.logger import logger
from app.scheduler.cache import intent_cache
from app.scheduler.fast_path import try_fast_path
from app.scheduler.intents import Intent, INTENT_CATEGORIES
from app.scheduler.prompts import domain_prompt, intent_prompts


MODEL_KEY_L1 = "intent"
MODEL_KEY_L2 = "intent"
MODEL_KEY_L2_UPGRADE = "intent_large"

L1_CONFIDENCE_DROP = 0.5
L2_CONFIDENCE_DROP = 0.5
L2_UPGRADE_CONFIDENCE = 0.6
CACHE_L1_THRESHOLD = 0.85
CACHE_L2_THRESHOLD = 0.85

VALID_DOMAINS = {"general", "content", "image", "video"}


@dataclass
class RoutingResult:
    intent: Intent
    confidence: float
    params: Dict = field(default_factory=dict)
    source: str = "llm"
    reasoning: str = ""
    needs_agent: bool = False
    domain: str = ""
    upgraded: bool = False


class IntentRouter:

    def route(
        self,
        text: str,
        has_attachment: bool = False,
        attachment_types: Optional[List[str]] = None,
        recent_intents: Optional[List[str]] = None,
    ) -> RoutingResult:
        text = (text or "").strip()
        attachment_types = attachment_types or []

        # ========== 0. fast-path ==========
        fast = try_fast_path(text, has_attachment, attachment_types)
        if fast:
            logger.info(f"【Router.fast】{fast['intent'].value} src={fast['source']} | {text[:40]}")
            return RoutingResult(
                intent=fast["intent"], confidence=fast["confidence"], source=fast["source"],
            )

        # ========== 1. L1 缓存 ==========
        ck_l1 = intent_cache.key_l1(text, has_attachment, attachment_types, MODEL_KEY_L1)
        cached_l1 = intent_cache.get(ck_l1)

        if cached_l1:
            domain = cached_l1["domain"]
            logger.info(f"【Router.cache.L1】domain={domain} | {text[:40]}")
        else:
            try:
                l1_data = self._call_l1(text, has_attachment, attachment_types)
            except Exception as e:
                logger.error(f"【Router.L1】异常，降级 CHAT: {e}")
                return RoutingResult(intent=Intent.CHAT, confidence=0.0, source="l1_error")

            domain = (l1_data.get("domain") or "").strip()
            l1_conf = float(l1_data.get("confidence", 0.0))
            l1_reason = l1_data.get("reasoning", "")

            logger.info(f"【Router.L1】domain={domain} conf={l1_conf:.2f} reason={l1_reason}")

            if domain not in VALID_DOMAINS:
                logger.warning(f"【Router.L1】非法 domain={domain}，降级 CHAT")
                return RoutingResult(
                    intent=Intent.CHAT, confidence=0.0,
                    source="l1_invalid", reasoning=l1_reason,
                )

            if l1_conf < L1_CONFIDENCE_DROP:
                logger.info(f"【Router.L1】domain={domain} conf={l1_conf:.2f} < {L1_CONFIDENCE_DROP} 降级 CHAT")
                return RoutingResult(
                    intent=Intent.CHAT, confidence=l1_conf,
                    source="l1_low_conf", domain=domain, reasoning=l1_reason,
                )

            if l1_conf >= CACHE_L1_THRESHOLD:
                intent_cache.set(ck_l1, {"domain": domain, "confidence": l1_conf})

        # ========== 4. L2 缓存 ==========
        ck_l2 = intent_cache.key_l2(domain, text, has_attachment, attachment_types, MODEL_KEY_L2)
        cached_l2 = intent_cache.get(ck_l2)
        if cached_l2:
            logger.info(f"【Router.cache.L2】domain={domain} intent={cached_l2.get('intent')}")
            return self._build_result_from_l2(domain, cached_l2)

        # ========== 5. L2 ==========
        try:
            l2_data = self._call_l2(text, domain, has_attachment, attachment_types, model_key=MODEL_KEY_L2)
        except Exception as e:
            logger.error(f"【Router.L2】异常，降级 CHAT: {e}")
            return RoutingResult(intent=Intent.CHAT, confidence=0.0, source="l2_error", domain=domain)

        # ========== 6. L2 校验 ==========
        validated = self._validate_l2(l2_data, domain)
        if validated is None:
            logger.info(f"【Router.L2】domain={domain} raw_intent={l2_data.get('intent')} OUT_OF_DOMAIN/非法 → CHAT")
            return RoutingResult(intent=Intent.CHAT, confidence=0.0, source="l2_out_of_domain", domain=domain)

        l2_intent, l2_conf, l2_needs_agent, l2_reason, l2_params = validated
        logger.info(
            f"【Router.L2】domain={domain} intent={l2_intent.value} "
            f"conf={l2_conf:.2f} needs_agent={l2_needs_agent} params={list(l2_params.keys())}"
        )

        # ========== 7. 升级 ==========
        upgraded = False
        if l2_conf < L2_CONFIDENCE_DROP:
            logger.info(f"【Router.L2.upgrade】intent={l2_intent.value} conf={l2_conf:.2f} → {MODEL_KEY_L2_UPGRADE}")
            try:
                l2_up = self._call_l2(text, domain, has_attachment, attachment_types, model_key=MODEL_KEY_L2_UPGRADE)
                up_val = self._validate_l2(l2_up, domain)
                if up_val:
                    up_intent, up_conf, up_needs, up_reason, up_params = up_val
                    if up_conf >= L2_UPGRADE_CONFIDENCE:
                        l2_intent, l2_conf = up_intent, up_conf
                        l2_needs_agent, l2_reason, l2_params = up_needs, up_reason, up_params
                        upgraded = True
                    else:
                        l2_conf = up_conf
            except Exception as e:
                logger.warning(f"【Router.L2.upgrade】异常: {e}")

        # ========== 8. 兜底 ==========
        if l2_conf < L2_CONFIDENCE_DROP:
            logger.info(f"【Router.L2】最终 conf={l2_conf:.2f} < {L2_CONFIDENCE_DROP} → CHAT")
            return RoutingResult(
                intent=Intent.CHAT, confidence=l2_conf,
                source="l2_final_low_conf", domain=domain, upgraded=upgraded,
            )

        # ========== 9. 缓存 ==========
        if l2_conf >= CACHE_L2_THRESHOLD and not upgraded:
            intent_cache.set(ck_l2, {
                "intent": l2_intent.value, "confidence": l2_conf,
                "needs_agent": l2_needs_agent, "reasoning": l2_reason, "params": l2_params,
            })

        return RoutingResult(
            intent=l2_intent, confidence=l2_conf, params=l2_params,
            source="l2_llm_upgraded" if upgraded else "l2_llm",
            reasoning=l2_reason, needs_agent=l2_needs_agent,
            domain=domain, upgraded=upgraded,
        )

    # ==================== LLM 调用 ====================

    def _call_l1(self, text, has_att, att_types) -> dict:
        from app.infrastructure.clients.model_manager import model_manager
        user_msg = domain_prompt.DOMAIN_USER_TEMPLATE.format(
            text=text,
            has_attachment="是" if has_att else "否",
            attachment_types="、".join(att_types) if att_types else "无",
        )
        raw = model_manager.chat(
            task_type=MODEL_KEY_L1,
            messages=[
                {"role": "system", "content": domain_prompt.DOMAIN_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            format="json",
        )
        return self._parse_json(raw)

    def _call_l2(self, text, domain, has_att, att_types, model_key: str = MODEL_KEY_L2) -> dict:
        from app.infrastructure.clients.model_manager import model_manager
        prompt = intent_prompts.L2_PROMPTS.get(domain, intent_prompts.L2_PROMPTS["general"])
        user_msg = intent_prompts.L2_USER_TEMPLATE.format(
            text=text,
            has_attachment="是" if has_att else "否",
            attachment_types="、".join(att_types) if att_types else "无",
        )
        raw = model_manager.chat(
            task_type=model_key,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_msg},
            ],
            format="json",
        )
        return self._parse_json(raw)

    # ==================== 校验 ====================

    def _validate_l2(self, data: dict, domain: str):
        intent_str = (data.get("intent") or "").strip()
        if intent_str == "OUT_OF_DOMAIN" or not intent_str:
            return None

        try:
            intent = Intent(intent_str)
        except ValueError:
            logger.warning(f"【Router.L2】非法 intent={intent_str}")
            return None

        if intent not in INTENT_CATEGORIES.get(domain, []):
            logger.warning(f"【Router.L2】intent={intent.value} 不属于 domain={domain} → OUT_OF_DOMAIN")
            return None

        conf = float(data.get("confidence", 0.0))
        needs_agent = bool(data.get("needs_agent", False))
        reasoning = data.get("reasoning", "")
        params = data.get("params", {}) or {}
        if not isinstance(params, dict):
            params = {}

        return intent, conf, needs_agent, reasoning, params

    @staticmethod
    def _build_result_from_l2(domain: str, cached: dict) -> RoutingResult:
        try:
            intent = Intent(cached["intent"])
        except (ValueError, KeyError):
            return RoutingResult(intent=Intent.CHAT, confidence=0.0, source="cache_invalid", domain=domain)
        return RoutingResult(
            intent=intent,
            confidence=float(cached.get("confidence", 0.0)),
            params=cached.get("params", {}) or {},
            source="cache",
            reasoning=cached.get("reasoning", ""),
            needs_agent=bool(cached.get("needs_agent", False)),
            domain=domain,
        )

    @staticmethod
    def _parse_json(raw: str) -> dict:
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            l, r = raw.find("{"), raw.rfind("}")
            if l >= 0 and r > l:
                try:
                    return json.loads(raw[l:r + 1])
                except Exception:
                    pass
            return {}


intent_router = IntentRouter()