#!/usr/bin/env python3
"""
reasoning_client.py — 梵天 LLM 推理客户端
设计院 2026-08-05 v2 | 多模型支持 2026-08-15 苏摩111封印

优先级:
  1. OpenClaw infer model run (内置, 无需外部key) ✅
  2. OpenRouter (完整key可用时)
  3. 规则降级 (保守JSON，不影响主流程)

模型别名（model参数）:
  'advanced'  → bedrock-claude-4-6-sonnet（默认，风控视角）
  'standard'  → Qwen3.5-397B（宏观视角，更广泛推理）
  None/缺省   → advanced

用法:
  from reasoning_client import call_reasoning
  result = call_reasoning(prompt="...", max_tokens=200, timeout=12)
  result = call_reasoning(prompt="...", model='standard')  # 指定模型
"""
import subprocess
import json
import re
import os
from pathlib import Path

BASE = Path(__file__).parent.parent


# 模型别名映射（openclaw infer model run --model 参数）
_MODEL_ALIASES = {
    'advanced':  'litellm/bedrock-claude-4-6-sonnet',
    'standard':  'litellm/Qwen3.5-397B-A17B-SGLang',
    None:        'litellm/bedrock-claude-4-6-sonnet',  # 默认
}


def call_reasoning(prompt: str, max_tokens: int = 200,
                   temperature: float = 0.1, timeout: int = 15,
                   model: str | None = None) -> str | None:
    """
    调用 LLM 推理。返回字符串响应，失败返回 None。
    model: 'advanced'(bedrock-claude) | 'standard'(Qwen) | None(默认advanced)
    """
    model_id = _MODEL_ALIASES.get(model, _MODEL_ALIASES[None])

    # ── 方案1: OpenClaw infer model run (内置多模型) ──────────────────────
    try:
        result = subprocess.run(
            ['openclaw', 'infer', 'model', 'run',
             '--prompt', prompt[:2000],   # 限制 prompt 长度
             '--model', model_id,
             '--json'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            # 解析 {"outputs": [{"text": "..."}]} 结构
            try:
                data = json.loads(result.stdout)
                # openclaw infer 返回格式: {ok, outputs: [{text}]} 或 {turns: [...]}
                outputs = data.get('outputs', data.get('turns', []))
                if outputs:
                    text = outputs[-1].get('text', '') if isinstance(outputs[-1], dict) else str(outputs[-1])
                    text = re.sub(r'```(?:json)?\s*', '', text).strip()
                    text = re.sub(r'```\s*$', '', text).strip()
                    if text:
                        return text
                # 有些模型直接返回 {text: ...} 不嵌套
                if 'text' in data:
                    return data['text'].strip()
            except (json.JSONDecodeError, KeyError):
                return result.stdout.strip()
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass

    # ── 方案2: OpenRouter (key 完整时) ────────────────────────────────────
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

    # ── 方案3: 规则降级 ───────────────────────────────────────────────────
    return _rule_fallback(prompt)


def _rule_fallback(prompt: str) -> str:
    """无LLM时的规则降级，返回保守JSON"""
    p = prompt.lower()
    if '风控' in p or 'risk' in p:
        return json.dumps({
            "score_adj": 0, "risk_level": "MEDIUM",
            "top_risk": "LLM降级:规则模式",
            "liq_insight": "清算数据已注入但推理降级",
            "veto": False
        })
    if '宏观' in p or 'macro' in p:
        return json.dumps({
            "score_adj": 0, "macro_trend": "NEUTRAL",
            "key_event": "LLM降级", "market_bias": "中性"
        })
    return json.dumps({"ok": True, "note": "rule_fallback", "score_adj": 0})


# ── reasoning_gate：信号门控 + 方仓上下文注射 ──────────────────────────
def reasoning_gate(result: dict, inject_context: bool = True) -> dict:
    """
    AI议会信号门控。
    接收 brahma_core 分析结果，调用 LLM 给出 PASS/WARN/BLOCK 裁决。
    inject_context=True 时自动注入方仓历史记忆（梵天思维）。

    返回:
        verdict: 'PASS' | 'WARN' | 'BLOCK'
        confidence: 0.0~1.0
        reason: str
        elapsed: float (秒)
    """
    import time
    t0 = time.time()

    symbol     = result.get('symbol', '')
    regime     = result.get('regime', result.get('market_state', 'CHOP_MID'))
    signal_dir = result.get('signal_dir', result.get('direction', 'LONG'))
    score      = float(result.get('score_final', result.get('score', 100)))

    # ── 注入梵天方仓记忆 ───────────────────────────────────────────
    memory_ctx = ''
    if inject_context and symbol:
        try:
            import sys as _sys
            _parent = str(Path(__file__).parent)
            if _parent not in _sys.path:
                _sys.path.insert(0, _parent)
            from brahma_context_injector import inject_brahma_context
            bd = result.get('confluence', {}).get('breakdown', {})
            ms = {
                'bb_width': bd.get('bb_width', 0.01),
                'rsi_1h':   bd.get('RSI_1H', 50),
                'rsi_4h':   bd.get('RSI_4H', 50),
                'fg':       bd.get('fg', 50),
            }
            memory_ctx = inject_brahma_context(
                symbol, regime, signal_dir, ms,
                include_cases=False, max_chars=800
            )
        except Exception:
            pass

    # ── 构建LLM提示词 ─────────────────────────────────────────────
    score_str = f'{score:.0f}'
    bd_str = ''
    bd = result.get('confluence', {}).get('breakdown', {})
    if bd:
        top_items = sorted(bd.items(), key=lambda x: abs(x[1]) if isinstance(x[1], (int,float)) else 0, reverse=True)[:5]
        bd_str = ' | '.join(f'{k}={v}' for k,v in top_items if isinstance(v, (int,float)))

    prompt = f"""{memory_ctx}

你是梵天风控专家（RiskAgent）。基于以上专有数据，评估此信号：
标的={symbol} 体制={regime} 方向={signal_dir} 评分={score_str}
评分分项（TOP5）: {bd_str}

请直接输出JSON（无其他文字）：
{{"verdict":"PASS|WARN|BLOCK","confidence":0.0~1.0,"reason":"<20字核心风险>"}}

PASS=正常 WARN=降分8 BLOCK=拒绝执行"""

    # ── 调用LLM ──────────────────────────────────────────────────
    raw = call_reasoning(prompt, max_tokens=100, timeout=12, model='advanced')

    elapsed = round(time.time() - t0, 2)

    # ── 解析结果 ─────────────────────────────────────────────────
    verdict = 'PASS'
    confidence = 0.6
    reason = '规则降级'

    if raw:
        try:
            # 提取JSON片段
            m = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if m:
                data = json.loads(m.group())
                verdict    = data.get('verdict', 'PASS').upper()
                confidence = float(data.get('confidence', 0.6))
                reason     = str(data.get('reason', ''))[:50]
                if verdict not in ('PASS', 'WARN', 'BLOCK'):
                    verdict = 'PASS'
        except Exception:
            # 关键词降级
            raw_l = raw.lower()
            if 'block' in raw_l:
                verdict, reason = 'BLOCK', 'LLM关键词:BLOCK'
            elif 'warn' in raw_l:
                verdict, reason = 'WARN', 'LLM关键词:WARN'

    return {
        'verdict':    verdict,
        'confidence': confidence,
        'reason':     reason,
        'elapsed':    elapsed,
    }
