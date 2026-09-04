#!/usr/bin/env python3
"""
free_llm_client.py — OpenRouter免费LLM客户端
设计院三方封印 2026-09-04 苏摩111

模型优先级（全免费，无额度限制）：
  1. minimax/minimax-m3:free        — 1M ctx，中文强，主力
  2. nvidia/nemotron-3-ultra-550b-a55b:free — 1M ctx，推理强，备用
  3. thinkingmachines/inkling:free  — 1M ctx，思维链，备用

接入位置：
  brahma_brain/llm_council.py   (AI议会真实LLM裁决)
  scripts/regime_switch_monitor.py
  scripts/oi_watchlist_monitor.py
"""
import json, os, ssl, time, urllib.request
from pathlib import Path

# Key优先级：.env > 环境变量 > TOOLS.md缓存
def _load_key() -> str:
    # 1. .env文件
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('OPENROUTER_API_KEY='):
                return line.split('=', 1)[1].strip()
    # 2. 环境变量
    k = os.environ.get('OPENROUTER_API_KEY', '')
    if k:
        return k
    return ''

API_KEY  = _load_key()
BASE_URL = 'https://openrouter.ai/api/v1/chat/completions'
MODELS   = [
    'minimax/minimax-m3:free',
    'nvidia/nemotron-3-ultra-253b-v1:free',
    'nvidia/nemotron-3-ultra-550b-a55b:free',
    'thinkingmachines/inkling:free',
]

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def chat(prompt: str, system: str = '', max_tokens: int = 200, timeout: int = 20) -> str:
    """
    调用OpenRouter免费模型，自动轮换备用。
    返回模型回复文本，失败时返回空字符串。
    """
    if not API_KEY:
        return ''

    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})

    for model in MODELS:
        try:
            payload = json.dumps({
                'model':      model,
                'messages':   messages,
                'max_tokens': max_tokens,
                'temperature': 0.3,
            }).encode()
            req = urllib.request.Request(
                BASE_URL, data=payload,
                headers={
                    'Authorization':  f'Bearer {API_KEY}',
                    'Content-Type':   'application/json',
                    'HTTP-Referer':   'https://brahma-quant.ai',
                    'X-Title':        'BrahmaQuantAI',
                },
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=timeout, context=_ctx).read())
            content = resp['choices'][0]['message']['content'].strip()
            if content:
                return content
        except Exception as e:
            continue  # 轮换下一个模型

    return ''


def council_llm(
    regime: str,
    bias: str,
    fvg_dir: str,
    oi_signal: str,
    sm_signal: str,
    hurst: float,
    kappa: float,
    score: float,
    entry_lo: float,
    entry_hi: float,
    price: float,
    liq_up: float,
    liq_dn: float,
    sym: str = 'BTC',
) -> dict:
    """
    AI议会真实LLM裁决。
    返回与规则引擎相同的结构: {bias, reason, action, confidence}
    """
    if not API_KEY:
        return {}

    prompt = f"""你是梵天量化交易系统的AI裁判。根据以下实时信号，给出一句话裁决。

标的: {sym}/USDT
现价: ${price:,.0f}
体制: {regime} (score={score:.0f})
梵天bias: {bias}
FVG方向: {fvg_dir}（磁铁方向）
OI信号: {oi_signal}
聪明钱: {sm_signal}
Hurst: {hurst:.3f}（>0.65=趋势，<0.45=均值回归）
κ(kappa): {kappa:.3f}（负=期权偏多，正=期权偏空）
入场区: ${entry_lo:,.0f}~${entry_hi:,.0f}（{'现价上方' if entry_lo > price else '现价下方'}）
上方清算目标: ${liq_up:,.0f}
下方清算目标: ${liq_dn:,.0f}

请输出严格JSON格式（不要markdown代码块，直接输出JSON）：
{{"bias":"偏多或偏空或中性","reason":"核心逻辑一句话（20字内）","action":"ENTER或WAIT或AVOID","confidence":"HIGH或MED或LOW"}}"""

    raw = chat(prompt, max_tokens=120, timeout=15)
    if not raw:
        return {}

    # 解析JSON
    try:
        # 去掉可能的markdown包裹
        clean = raw.strip()
        if '```' in clean:
            clean = clean.split('```')[1] if '```json' not in clean else clean.split('```json')[1].split('```')[0]
        # 找第一个{...}
        start = clean.find('{')
        end   = clean.rfind('}') + 1
        if start >= 0 and end > start:
            d = json.loads(clean[start:end])
            # 验证字段
            if all(k in d for k in ('bias', 'reason', 'action', 'confidence')):
                return d
    except Exception:
        pass

    return {}


if __name__ == '__main__':
    print('测试OpenRouter连通性...')
    if not API_KEY:
        print('❌ 未找到OPENROUTER_API_KEY，请检查.env文件')
    else:
        # 连通性测试
        resp = chat('BTC现在CHOP_MID体制，大户65%多，FVG向下，一句话：做多还是做空？', max_tokens=60)
        print(f'✅ 连通: {resp[:100]}' if resp else '❌ 所有模型均失败')

        # AI议会测试
        result = council_llm(
            regime='CHOP_MID', bias='SHORT', fvg_dir='BEAR',
            oi_signal='MIXED', sm_signal='STRONG_BULL',
            hurst=0.683, kappa=-0.110, score=60,
            entry_lo=80958, entry_hi=81927, price=80716,
            liq_up=82825, liq_dn=79577, sym='BTC',
        )
        print(f'AI议会: {json.dumps(result, ensure_ascii=False)}')
