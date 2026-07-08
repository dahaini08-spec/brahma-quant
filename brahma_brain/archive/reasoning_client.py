"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 推理客户端，AI增强辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
reasoning_client.py — OpenRouter 推理模型接入层
设计院封印 · 2026-06-26

支持模型：
  - deepseek/deepseek-r1-0528（默认，性价比最优）
  - google/gemini-2.5-flash（快速）
  - qwen/qwen3-235b-a22b-thinking-2507（超低价）

用途：
  - 梵天信号深度验证（reasoning gate）
  - 止损逻辑推理增强
  - 体制分析反驳检验
"""
import os, json, time
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env
_ENV = Path(__file__).parent.parent / '.env'
load_dotenv(_ENV)

OPENROUTER_API_KEY  = os.getenv('OPENROUTER_API_KEY', '')
OPENROUTER_BASE_URL = os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')
REASONING_MODEL     = os.getenv('REASONING_MODEL', 'deepseek/deepseek-r1-0528')
REASONING_MODEL_FAST = os.getenv('REASONING_MODEL_FAST', 'google/gemini-2.5-flash')
REASONING_MODEL_CHEAP = os.getenv('REASONING_MODEL_CHEAP', 'qwen/qwen3-235b-a22b-thinking-2507')


def call_reasoning(prompt: str, model: str = None, max_tokens: int = 1024,
                   temperature: float = 0.1, timeout: int = 60,
                   max_retries: int = 2) -> dict:
    """
    调用OpenRouter推理模型（含429自动重试）。
    返回: {
        'content': str,          # 模型回复
        'reasoning': str,        # 思考链（若支持）
        'model': str,
        'usage': dict,
        'elapsed': float,
        'error': str | None
    }
    """
    if not OPENROUTER_API_KEY:
        return {'content': '', 'reasoning': '', 'model': model or REASONING_MODEL,
                'usage': {}, 'elapsed': 0, 'error': 'OPENROUTER_API_KEY未配置'}

    try:
        import requests
    except ImportError:
        return {'content': '', 'reasoning': '', 'model': '', 'usage': {}, 'elapsed': 0,
                'error': 'requests未安装'}

    _model = model or REASONING_MODEL
    t0 = time.time()

    for _attempt in range(max_retries + 1):
      try:
        resp = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                'Content-Type': 'application/json',
                'HTTP-Referer': 'https://brahma-trading-system.local',
                'X-Title': 'Brahma Trading System',
            },
            json={
                'model': _model,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': max_tokens,
                'temperature': temperature,
            },
            timeout=timeout
        )
        # 429限速：读取retry-after并等待
        if resp.status_code == 429:
            _retry_after = int(resp.headers.get('retry-after', 20))
            if _attempt < max_retries:
                time.sleep(min(_retry_after, 25))  # 最多等25s
                continue
            else:
                return {'content': '', 'reasoning': '', 'model': _model,
                        'usage': {}, 'elapsed': round(time.time()-t0, 2),
                        'error': f'429 rate_limit after {max_retries} retries'}
        resp.raise_for_status()
        data = resp.json()

        choice = data.get('choices', [{}])[0]
        msg = choice.get('message', {})
        content = msg.get('content', '')
        # DeepSeek-R1 / Qwen3 thinking 字段
        reasoning = msg.get('reasoning', '') or msg.get('reasoning_content', '')

        return {
            'content': content,
            'reasoning': reasoning,
            'model': data.get('model', _model),
            'usage': data.get('usage', {}),
            'elapsed': round(time.time() - t0, 2),
            'error': None
        }

      except Exception as e:
        if _attempt < max_retries:
            time.sleep(3)
            continue
        return {
            'content': '', 'reasoning': '',
            'model': _model, 'usage': {},
            'elapsed': round(time.time() - t0, 2),
            'error': str(e)
        }

    # 应该不会到这里
    return {'content': '', 'reasoning': '', 'model': _model, 'usage': {},
            'elapsed': round(time.time() - t0, 2), 'error': 'max_retries exceeded'}


def reasoning_gate(signal: dict, fast: bool = False) -> dict:
    """
    梵天信号推理门控：用推理模型对高分信号做二次验证。
    只对 score >= 150 且 valid=True 的信号调用，避免浪费。

    返回: {
        'verdict': 'PASS' | 'WARN' | 'BLOCK',
        'confidence': float,  # 0~1
        'reason': str,
        'reasoning_chain': str,
        'elapsed': float
    }
    """
    score = signal.get('score_final', 0) or signal.get('score', 0)
    regime = signal.get('regime', '')
    direction = signal.get('signal_dir', '') or signal.get('direction', '')
    price = signal.get('price', 0)
    entry_lo = signal.get('params', {}).get('entry_lo', 0)
    entry_hi = signal.get('params', {}).get('entry_hi', 0)
    sl = signal.get('params', {}).get('stop_loss', 0)
    tp1 = signal.get('params', {}).get('tp1', 0)
    rr = signal.get('params', {}).get('rr1', 0)
    kronos = signal.get('confluence', {}).get('breakdown', {}).get('s23_kronos', '')
    gex = signal.get('confluence', {}).get('breakdown', {}).get('s22_gex', '')
    symbol = signal.get('symbol', 'UNKNOWN')
    sentiment = signal.get('sentiment', {})

    prompt = f"""你是顶级加密合约交易风控审计师。请对以下梵天量化信号进行推理验证，判断是否应该执行。

【信号摘要】
标的: {symbol}
体制: {regime}
方向: {direction}
当前价: {price}
入场区: {entry_lo:.2f} ~ {entry_hi:.2f}
止损: {sl:.2f}
目标: {tp1:.2f}
R:R: {rr}
综合评分: {score}
Kronos: {kronos}
GEX: {gex}
资金费率: {sentiment.get('funding_rate', 'N/A')}
多空比(LSR): {sentiment.get('long_short_ratio', 'N/A')}%
OI变化: {sentiment.get('oi_change_pct', 'N/A')}%

【推理任务】
1. 该信号的多空结构是否自洽？
2. 最大风险点是什么？
3. 是否存在明显矛盾或陷阱？
4. 综合判断：PASS（可执行）/ WARN（谨慎）/ BLOCK（拒绝）

请用JSON格式回复：
{{"verdict": "PASS|WARN|BLOCK", "confidence": 0.0-1.0, "risk": "主要风险一句话", "reason": "判断理由2-3句"}}"""

    _model = REASONING_MODEL_FAST if fast else REASONING_MODEL
    result = call_reasoning(prompt, model=_model, max_tokens=800, temperature=0.1)

    if result.get('error'):
        return {'verdict': 'PASS', 'confidence': 0.5, 'reason': f'推理模型调用失败: {result["error"]}',
                'reasoning_chain': '', 'elapsed': result['elapsed']}

    # 解析JSON
    try:
        content = result['content'].strip()
        # 提取JSON块
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        elif '```' in content:
            content = content.split('```')[1].split('```')[0].strip()
        # 找第一个{...}
        start = content.find('{')
        end = content.rfind('}') + 1
        if start >= 0 and end > start:
            parsed = json.loads(content[start:end])
            return {
                'verdict': parsed.get('verdict', 'PASS'),
                'confidence': float(parsed.get('confidence', 0.7)),
                'reason': parsed.get('reason', '') + ' | 风险: ' + parsed.get('risk', ''),
                'reasoning_chain': result.get('reasoning', ''),
                'elapsed': result['elapsed']
            }
    except Exception:
        pass

    return {
        'verdict': 'PASS',
        'confidence': 0.6,
        'reason': result['content'][:200],
        'reasoning_chain': result.get('reasoning', ''),
        'elapsed': result['elapsed']
    }


if __name__ == '__main__':
    # 快速连通性测试
    print("🔧 测试OpenRouter推理模型连接...")
    r = call_reasoning(
        "请用一句话回答：BTC当前处于熊市趋势时，最优策略方向是？只回答方向。",
        model=REASONING_MODEL_FAST,
        max_tokens=50
    )
    if r.get('error'):
        print(f"❌ 连接失败: {r['error']}")
    else:
        print(f"✅ 连接成功！模型={r['model']} 耗时={r['elapsed']}s")
        print(f"   回复: {r['content']}")
        if r.get('reasoning'):
            print(f"   思考链: {r['reasoning'][:100]}...")
