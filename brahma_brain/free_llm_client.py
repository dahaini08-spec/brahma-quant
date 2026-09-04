#!/usr/bin/env python3
"""
free_llm_client.py — OpenRouter免费LLM接入层
设计院封印 2026-09-04 苏摩111

接入位置：brahma_brain/llm_council.py → from free_llm_client import council_three_way, council_llm
使用模型：minimax/minimax-m3:free（1M上下文，中文优秀）
fallback链：minimax → qwen → llama → rule_engine
"""
import json, sys, time
import urllib.request
from pathlib import Path

# API配置（从TOOLS.md读取，不依赖.env）
OPENROUTER_API_KEY = 'OPENROUTER_KEY_REDACTED'
OPENROUTER_URL     = 'https://openrouter.ai/api/v1/chat/completions'

# 免费模型优先级链
FREE_MODELS = [
    'minimax/minimax-m3:free',
    'qwen/qwen3-235b-a22b:free',
    'meta-llama/llama-3.3-70b-instruct:free',
    'deepseek/deepseek-r1-0528:free',
]

TIMEOUT_S = 12  # 单次LLM请求超时


def _call_openrouter(prompt: str, model: str = None, max_tokens: int = 120) -> str:
    """调用OpenRouter API，返回文本响应"""
    models = [model] + [m for m in FREE_MODELS if m != model] if model else FREE_MODELS
    for m in models:
        try:
            payload = {
                'model': m,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': max_tokens,
                'temperature': 0.1,
            }
            req = urllib.request.Request(
                OPENROUTER_URL,
                data=json.dumps(payload).encode(),
                headers={
                    'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                    'Content-Type': 'application/json',
                    'HTTP-Referer': 'https://brahma-quant.ai',
                    'X-Title': 'BrahmaQuantAI',
                },
                method='POST'
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT_S).read())
            text = resp['choices'][0]['message']['content'].strip()
            if text:
                return text
        except Exception:
            continue
    return ''


def _parse_action(text: str) -> dict:
    """
    从LLM响应中提取结构化结论
    期望格式：ACTION:LONG|SHORT|WAIT  CONF:HIGH|MED|LOW  REASON:xxx
    """
    text_up = text.upper()
    action = None
    for a in ('LONG', 'SHORT', 'WAIT'):
        if a in text_up:
            action = a
            break
    conf = 'MED'
    for c in ('HIGH', 'LOW', 'MED'):
        if c in text_up:
            conf = c
            break
    # 提取reason（取第一句中文或英文）
    reason = text[:80].replace('\n', ' ')
    return {'action': action, 'confidence': conf, 'reason': f'[LL] {reason}'}


def council_llm(regime: str, bias: str, fvg_dir: str = 'NONE',
                oi_signal: str = 'MIXED', sm_signal: str = 'NEUTRAL',
                hurst: float = 0.5, kappa: float = 0.0, score: float = 0,
                entry_lo: float = 0, entry_hi: float = 0, price: float = 0,
                liq_up: float = 0, liq_dn: float = 0, sym: str = 'BTC') -> dict:
    """单次LLM裁决"""
    prompt = f"""你是一名顶级合约交易员，给出唯一操作建议。

标的: {sym}/USDT  当前价: ${price:,.0f}
体制: {regime}  方向偏好: {bias}  梵天score: {score:.0f}
FVG磁铁: {fvg_dir}  OI信号: {oi_signal}  聪明钱: {sm_signal}
Hurst: {hurst:.2f}  κ(期权): {kappa:.3f}
入场区: ${entry_lo:,.0f}~${entry_hi:,.0f}
上方清算: ${liq_up:,.0f}  下方清算: ${liq_dn:,.0f}

请用一行回答，格式严格如下：
ACTION:LONG 或 ACTION:SHORT 或 ACTION:WAIT  CONF:HIGH或MED或LOW  REASON:一句话理由(20字内)"""

    text = _call_openrouter(prompt)
    if not text:
        return {}
    result = _parse_action(text)
    if not result.get('action'):
        return {}
    return {
        'action':     result['action'],
        'bias':       result['action'],
        'confidence': result['confidence'],
        'reason':     result['reason'],
        'model':      FREE_MODELS[0],
    }


def council_three_way(sym: str, price: float, regime: str, score: float,
                      fvg_dir: str = 'NONE', fvg_magnet: float = 0,
                      oi_signal: str = 'MIXED', sm_signal: str = 'NEUTRAL',
                      big_long: float = 50, hurst: float = 0.5, kappa: float = 0.0,
                      harv: float = 0, entry_lo: float = 0, entry_hi: float = 0,
                      liq_up: float = 0, liq_dn: float = 0,
                      macro_bias: str = 'NEUTRAL', fear_greed: int = 50) -> dict:
    """
    三方独立投票（量化工程师 + 梵天专家 + 合约交易员）
    三方2/3一致才出信号，否则WAIT
    """
    context = f"""{sym}/USDT ${price:,.0f} | 体制:{regime} score:{score:.0f}
FVG:{fvg_dir}(磁铁${fvg_magnet:,.0f}) OI:{oi_signal} 聪明钱:{sm_signal} 大户多:{big_long:.0f}%
Hurst:{hurst:.2f} κ:{kappa:.3f} HAR-RV:{harv:.4f}
入场:${entry_lo:,.0f}~${entry_hi:,.0f} 上方清算:${liq_up:,.0f} 下方清算:${liq_dn:,.0f}
宏观:{macro_bias} 恐贪:{fear_greed}"""

    roles = [
        ('顶级量化工程师', '从统计和EV角度'),
        ('梵天量化专家',   '从SMC结构和FVG磁铁角度'),
        ('40年合约交易员', '从市场微观结构和清算角度'),
    ]

    votes = []
    for role, angle in roles:
        prompt = f"""你是{role}，{angle}给出操作建议。

{context}

一行回答：ACTION:LONG 或 ACTION:SHORT 或 ACTION:WAIT  CONF:HIGH或MED或LOW  REASON:理由(15字)"""
        text = _call_openrouter(prompt, max_tokens=60)
        if text:
            v = _parse_action(text)
            if v.get('action'):
                votes.append(v)

    if len(votes) < 2:
        return {}

    # 统计投票
    from collections import Counter
    action_votes = Counter(v['action'] for v in votes)
    top_action, top_count = action_votes.most_common(1)[0]

    if top_count < 2:
        # 无共识 → WAIT
        return {
            'action': 'WAIT', 'bias': 'WAIT',
            'confidence': 'LOW',
            'reason': f'[LL] 三方无共识: {dict(action_votes)}',
            'votes': votes, 'model': FREE_MODELS[0],
        }

    # 合并理由
    reasons = [v['reason'] for v in votes if v['action'] == top_action]
    conf_votes = Counter(v['confidence'] for v in votes if v['action'] == top_action)
    top_conf = conf_votes.most_common(1)[0][0]
    combined_reason = f"[LL] {top_count}/3共识 | {reasons[0]}"

    return {
        'action':     top_action,
        'bias':       top_action,
        'confidence': top_conf,
        'reason':     combined_reason,
        'votes':      votes,
        'model':      FREE_MODELS[0],
        'vote_breakdown': dict(action_votes),
    }


def vip_entry_reason(sym: str, direction: str, regime: str, score: float,
                     fvg_dir: str = 'NONE', fvg_magnet: float = 0,
                     oi_signal: str = 'MIXED', sm_signal: str = 'NEUTRAL',
                     hurst: float = 0.5, kappa: float = 0.0,
                     entry_lo: float = 0, entry_hi: float = 0,
                     price: float = 0, liq_up: float = 0, liq_dn: float = 0) -> str:
    """
    P0-2: 生成VIP卡片“一句话核心逻辑”（20字内）
    接入位置：brahma_manual_analysis.py Step10 VIP卡片生成
    """
    prompt = f"""{sym}/USDT {direction} | 体制:{regime} score:{score:.0f}
FVG:{fvg_dir}(磁铁${fvg_magnet:,.0f}) OI:{oi_signal} 聊明錢:{sm_signal}
Hurst:{hurst:.2f} κ:{kappa:.3f}
入场区:${entry_lo:,.0f}~${entry_hi:,.0f} 上方清算:${liq_up:,.0f} 下方清算:${liq_dn:,.0f}
当前价:${price:,.0f}

用一句话(不超过20字)总结最核心的做{direction}逻辑，直接输出值不要加前缀:"""
    text = _call_openrouter(prompt, max_tokens=40)
    return text.strip() if text else ''


def signal_conflict_resolve(sym: str, direction: str, regime: str,
                            fvg_dir: str, oi_signal: str, sm_signal: str,
                            conflict_desc: str = '') -> str:
    """
    P0-2: 当信号有矛盾时，LLM裁决“相信哪个维度”
    接入位置：brahma_manual_analysis.py Step10 冲突检测后
    """
    prompt = f"""{sym}/USDT {direction} | 体制:{regime}
FVG:{fvg_dir} OI:{oi_signal} 聊明錢:{sm_signal}
矛盾描述: {conflict_desc if conflict_desc else 'FVG方向与OI信号不一致'}

用一句话裁决应相信哪个维度(不超过20字)，直接输出:"""
    text = _call_openrouter(prompt, max_tokens=40)
    return text.strip() if text else ''


if __name__ == '__main__':
    # 冒烟测试
    print('=== free_llm_client 冒烟测试 ===')
    t0 = time.time()
    result = council_three_way(
        sym='BTC', price=81000, regime='CHOP_MID', score=95,
        fvg_dir='BEAR', fvg_magnet=80200,
        oi_signal='SHORT_SQUEEZE', sm_signal='BULLISH',
        big_long=64.9, hurst=0.588, kappa=0.017,
        entry_lo=78300, entry_hi=78500,
        liq_up=82154, liq_dn=78248,
    )
    elapsed = time.time() - t0
    print(f'耗时: {elapsed:.1f}s')
    print(f'action:     {result.get("action")}')
    print(f'confidence: {result.get("confidence")}')
    print(f'reason:     {result.get("reason")}')
    print(f'vote_breakdown: {result.get("vote_breakdown")}')
    print(f'[LL]标注: {"✅" if "[LL]" in str(result.get("reason","")) else "❌"}')
