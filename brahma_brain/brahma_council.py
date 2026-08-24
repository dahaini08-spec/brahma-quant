#!/usr/bin/env python3
"""
brahma_council.py — 统一AI议会域 v1.0
设计院 2026-08-24 重建 | 替换3个旧模块:
  llm_council.py        (188行) → 规则议会 (零延迟)
  llm_council_bridge.py (767行) → 三专家LLM议会 (claude+Qwen×2)
  reasoning_client.py   (125行) → call_reasoning

向后兼容: 所有函数签名不变
"""
from __future__ import annotations
import logging
from typing import Dict

logger = logging.getLogger('brahma_council')

# ══════════════════════════════════════════════════════════════════
# 1. reasoning_client — LLM调用底层
# ══════════════════════════════════════════════════════════════════

def call_reasoning(prompt: str, max_tokens: int = 300,
                   model: str = 'standard', timeout: int = 15) -> str | None:
    try:
        from reasoning_client import call_reasoning as _f
        return _f(prompt, max_tokens, model, timeout)
    except Exception as e:
        logger.debug(f'call_reasoning降级: {e}')
        return None

# ══════════════════════════════════════════════════════════════════
# 2. llm_council — 规则议会 (零延迟零成本)
# ══════════════════════════════════════════════════════════════════

def rule_council(signal: Dict) -> Dict:
    """纯规则议会，无LLM调用，<1ms"""
    try:
        from llm_council import review as _f
        return _f(signal)
    except Exception as e:
        logger.debug(f'rule_council降级: {e}')
        return {'score_adj': 0, 'source': 'fallback'}

# ══════════════════════════════════════════════════════════════════
# 3. llm_council_bridge — 三专家LLM议会
# ══════════════════════════════════════════════════════════════════

def review(signal: Dict) -> Dict:
    """
    三专家AI议会主入口 (MODE=live):
      RiskAgent  → claude  (风控+清算)
      MacroAgent → Qwen    (宏观+DXY)
      QuantAgent → Qwen    (WR矩阵+EV)
    并行执行，~10s
    """
    try:
        from llm_council_bridge import review as _f
        return _f(signal)
    except Exception as e:
        logger.warning(f'llm_council_bridge降级→rule_council: {e}')
        return rule_council(signal)

def get_council_mode() -> str:
    try:
        from llm_council_bridge import MODE
        return MODE
    except Exception:
        return 'unknown'

# ══════════════════════════════════════════════════════════════════
# 4. 统一入口（自动选择规则/LLM）
# ══════════════════════════════════════════════════════════════════

def council_review(signal: Dict, use_llm: bool = True) -> Dict:
    """
    统一议会入口:
    - use_llm=True (默认): 三专家LLM议会 (score≥140时推荐)
    - use_llm=False: 纯规则议会 (快速路径)
    """
    if use_llm:
        return review(signal)
    return rule_council(signal)
