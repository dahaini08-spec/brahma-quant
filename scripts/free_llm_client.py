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


def vip_entry_reason(
    sym: str, price: float, regime: str,
    fvg_dir: str, fvg_magnet: float,
    oi_signal: str, sm_signal: str,
    hurst: float, kappa: float,
    entry_lo: float, entry_hi: float,
    bias: str, liq_up: float, liq_dn: float,
) -> str:
    """
    A: VIP入场理由一句话（LLM生成）
    返回: 20字内的精准入场逻辑句，失败返回空字符串
    """
    direction = '做多' if bias == 'LONG' else '做空'
    entry_side = '下方回调' if entry_lo < price else '上方反弹'
    prompt = (
        f"梵天系统 {sym}/USDT ${price:,.0f} {regime}体制\n"
        f"方向:{direction} 入场区:${entry_lo:,.0f}~${entry_hi:,.0f}({entry_side})\n"
        f"FVG:{fvg_dir}方向 磁铁${fvg_magnet:,.0f} OI:{oi_signal} 聪明钱:{sm_signal}\n"
        f"Hurst:{hurst:.2f} kappa:{kappa:.3f} 上方清算:${liq_up:,.0f} 下方清算:${liq_dn:,.0f}\n"
        f"必须用中文回答。输出一句话入场逻辑（15字内，直接说结构原因，禁止用英文）："
    )
    result = chat(prompt, max_tokens=40, timeout=12)
    if not result:
        return ''
    # 取第一句，截断
    first = result.split('\n')[0].strip().rstrip('。').strip()
    return first[:25]


def signal_conflict_resolve(
    sym: str, price: float, regime: str,
    oi_signal: str, oi_desc: str,
    sm_signal: str, big_long: float, retail_long: float,
    fvg_dir: str,
) -> str:
    """
    B: 信号矛盾自动LLM裁决
    当OI与大户方向矛盾时，LLM分析哪方更可信
    返回: 一句话裁决，失败返回空字符串
    """
    prompt = (
        f"{sym} ${price:,.0f} {regime}体制 信号矛盾分析：\n"
        f"OI信号:{oi_signal}（{oi_desc[:30]}）\n"
        f"大户多仓:{big_long:.0f}% 散户多仓:{retail_long:.0f}% 分歧:{abs(big_long-retail_long):.0f}%\n"
        f"FVG方向:{fvg_dir}\n"
        f"OI和大户方向矛盾，请裁决：哪方信号更可信，倾向做多还是做空？\n"
        f"输出格式（JSON）: {{\"winner\":\"OI或大户\",\"bias\":\"做多或做空\",\"reason\":\"原因一句话15字内\"}}"
    )
    raw = chat(prompt, max_tokens=80, timeout=12)
    if not raw:
        return ''
    try:
        import json as _j
        start = raw.find('{'); end = raw.rfind('}') + 1
        if start >= 0 and end > start:
            d = _j.loads(raw[start:end])
            winner = d.get('winner', '?')
            bias   = d.get('bias', '?')
            reason = d.get('reason', '')
            return f"{winner}信号更可信 → {bias}（{reason}）"
    except Exception:
        pass
    # fallback: 直接返回原始文本首句
    return raw.split('\n')[0].strip()[:50]


def council_three_way(
    sym: str, price: float, regime: str, score: float,
    fvg_dir: str, fvg_magnet: float,
    oi_signal: str, sm_signal: str, big_long: float,
    hurst: float, kappa: float, harv: float,
    entry_lo: float, entry_hi: float,
    liq_up: float, liq_dn: float,
    macro_bias: str, fear_greed: int,
) -> dict:
    """
    C: AI议会三方独立LLM投票
    宏观裁判 / 结构裁判 / 量化裁判 各自独立调用
    投票结果 → 综合置信度 HIGH/MED/LOW
    """
    import concurrent.futures as _cf

    base = f"{sym}/USDT ${price:,.0f} {regime}体制(score={score:.0f})"

    prompts = {
        '宏观': (
            f"{base}\n"
            f"宏观信号: 恐贪={fear_greed} 宏观偏向={macro_bias} FVG磁铁={fvg_dir}(${fvg_magnet:,.0f})\n"
            f"作为宏观裁判，仅从体制+宏观角度裁决，必须用中文。\n"
            f"JSON: {{\"vote\":\"做多或做空或中性\",\"reason\":\"10字内\",\"conf\":\"HIGH或MED或LOW\"}}"
        ),
        '结构': (
            f"{base}\n"
            f"结构信号: OI={oi_signal} 大户多{big_long:.0f}% FVG={fvg_dir} 上方清算=${liq_up:,.0f} 下方=${liq_dn:,.0f}\n"
            f"入场区: ${entry_lo:,.0f}~${entry_hi:,.0f}({'上方' if entry_lo > price else '下方'})\n"
            f"作为SMC结构裁判，仅从入场区+清算+OI角度裁决，必须用中文。\n"
            f"JSON: {{\"vote\":\"做多或做空或中性\",\"reason\":\"10字内\",\"conf\":\"HIGH或MED或LOW\"}}"
        ),
        '量化': (
            f"{base}\n"
            f"量化信号: Hurst={hurst:.3f} kappa={kappa:.3f} HAR-RV={harv:.4f} 聪明钱={sm_signal}\n"
            f"作为量化裁判，仅从Hurst趋势+期权偏向+波动率角度裁决，必须用中文。\n"
            f"JSON: {{\"vote\":\"做多或做空或中性\",\"reason\":\"10字内\",\"conf\":\"HIGH或MED或LOW\"}}"
        ),
    }

    results = {}
    def _call(role, prompt):
        raw = chat(prompt, max_tokens=60, timeout=15)
        if not raw:
            return role, {}
        try:
            import json as _j
            s = raw.find('{'); e = raw.rfind('}') + 1
            if s >= 0 and e > s:
                d = _j.loads(raw[s:e])
                if 'vote' in d:
                    return role, d
        except Exception:
            pass
        return role, {}

    # 三方并行调用
    with _cf.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_call, role, prompt): role for role, prompt in prompts.items()}
        for fut in _cf.as_completed(futures, timeout=20):
            try:
                role, d = fut.result()
                if d:
                    results[role] = d
            except Exception:
                pass

    if not results:
        return {}

    # 投票统计
    votes = [d.get('vote', '中性') for d in results.values()]
    long_v  = sum(1 for v in votes if '多' in v)
    short_v = sum(1 for v in votes if '空' in v)
    total   = len(votes)

    if long_v > short_v:
        final_bias = '偏多'
        final_action = 'ENTER' if long_v == total else 'WAIT'
    elif short_v > long_v:
        final_bias = '偏空'
        final_action = 'ENTER' if short_v == total else 'WAIT'
    else:
        final_bias = '中性'
        final_action = 'WAIT'

    # 置信度：全票=HIGH，2:1=MED，全中性=LOW
    if long_v == total or short_v == total:
        conf = 'HIGH'
    elif long_v > 0 or short_v > 0:
        conf = 'MED'
    else:
        conf = 'LOW'

    # 汇总理由
    reasons = [f"{role}:{d.get('reason','')}" for role, d in results.items() if d.get('reason')]
    reason_str = ' | '.join(reasons[:3])

    return {
        'bias':       final_bias,
        'reason':     reason_str[:60],
        'action':     final_action,
        'confidence': conf,
        'votes':      {role: d.get('vote', '?') for role, d in results.items()},
        'source':     'LLM3',
    }
