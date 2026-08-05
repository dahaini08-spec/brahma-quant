#!/usr/bin/env python3
"""
reasoning_client.py — 梵天 LLM 推理客户端
设计院 2026-08-05

优先级:
  1. OpenRouter (完整key可用时) → mistral-7b-instruct:free
  2. OpenClaw bedrock-claude (openclaw ask CLI)
  3. 规则降级 (保守JSON，不影响主流程)
"""
import subprocess, json, os
from pathlib import Path

BASE = Path(__file__).parent.parent


def call_reasoning(prompt: str, max_tokens: int = 200,
                   temperature: float = 0.1, timeout: int = 12) -> str | None:
    """
    调用 LLM 推理。返回字符串响应，失败返回 None。
    """
    # 方案1: OpenRouter (key完整时)
    try:
        cfg_path = BASE / 'config' / 'omniroute.json'
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            api_key = cfg.get('api_key', '')
            if len(api_key) > 30:
                import requests as _req
                r = _req.post(
                    'https://openrouter.ai/api/v1/chat/completions',
                    headers={
                        'Authorization': f'Bearer {api_key}',
                        'Content-Type': 'application/json',
                        'HTTP-Referer': 'https://brahma.ai',
                    },
                    json={
                        'model': cfg.get('chat_model', 'mistralai/mistral-7b-instruct:free'),
                        'messages': [{'role': 'user', 'content': prompt}],
                        'max_tokens': max_tokens,
                        'temperature': temperature,
                    },
                    timeout=timeout
                )
                if r.status_code == 200:
                    return r.json()['choices'][0]['message']['content']
    except Exception:
        pass

    # 方案2: OpenClaw bedrock-claude via CLI
    try:
        result = subprocess.run(
            ['openclaw', 'ask', '--model', 'standard', '--timeout', str(timeout),
             '--message', prompt[:1500]],  # 限制prompt长度
            capture_output=True, text=True, timeout=timeout + 5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    # 方案3: 规则降级
    return _rule_fallback(prompt)


def _rule_fallback(prompt: str) -> str:
    """无LLM时的规则降级，返回保守JSON"""
    p = prompt.lower()
    if '风控' in p or 'risk' in p:
        return json.dumps({
            "score_adj": 0, "risk_level": "MEDIUM",
            "top_risk": "LLM不可用:规则降级",
            "liq_insight": "清算数据已注入但LLM未响应",
            "veto": False
        })
    if '宏观' in p or 'macro' in p:
        return json.dumps({
            "score_adj": 0, "macro_trend": "NEUTRAL",
            "key_event": "LLM降级", "market_bias": "中性"
        })
    return json.dumps({"ok": True, "note": "rule_fallback", "score_adj": 0})
