#!/usr/bin/env python3
"""
trigger_reasoning_enhancer.py — P2 15M触发推理增强
设计院封印 · 2026-06-26

职责：
  在15M置信度已达标的情况下，用推理模型判断是否立即入场还是等待更优时机
  解决核心痛点：置信度80但Kronos反转+流动性池极近 → 推理说"等扫完再入"

接入位置：
  trigger_15m.py 输出后，brahma_core.py params组装阶段

苏摩约束：
  - 仅在置信度≥70且Kronos p_up>0.6时触发（避免频繁调用）
  - TTL=10min缓存
  - 任何异常返回原始trigger建议
"""
import json, time
from pathlib import Path

_CACHE_FILE = Path(__file__).parent.parent / 'data' / 'trigger_reasoning_cache.json'
_CACHE_TTL  = 600   # 10min（苏摩B档升级）


def _load_cache(key: str):
    try:
        if _CACHE_FILE.exists():
            c = json.loads(_CACHE_FILE.read_text())
            entry = c.get(key, {})
            if time.time() - entry.get('ts', 0) < _CACHE_TTL:
                return entry.get('result')
    except Exception:
        pass
    return None


def _save_cache(key: str, result: dict):
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        c = {}
        if _CACHE_FILE.exists():
            try:
                c = json.loads(_CACHE_FILE.read_text())
            except Exception:
                pass
        c[key] = {'ts': time.time(), 'result': result}
        _CACHE_FILE.write_text(json.dumps(c, ensure_ascii=False))
    except Exception:
        pass


def enhance_trigger_timing(symbol: str, direction: str,
                           trigger_confidence: int,
                           price: float, entry_lo: float, entry_hi: float,
                           wick_type: str = '',
                           kronos_pup: float = 0.5,
                           liq_above: float = 0.0,
                           liq_below: float = 0.0,
                           orderbook_imbalance: str = '',
                           regime: str = '') -> dict:
    """
    推理模型判断15M触发时机。

    返回:
    {
        'execute_now':    bool,
        'wait_for':       str,       # 等待条件描述
        'optimal_entry':  float,     # 推理最优入场价
        'confidence_adj': int,       # 对trigger_confidence的调整（-20~+10）
        'reasoning':      str,
        'source':         str
    }
    """
    _base = {
        'execute_now':    trigger_confidence >= 75,
        'wait_for':       '',
        'optimal_entry':  (entry_lo + entry_hi) / 2,
        'confidence_adj': 0,
        'reasoning':      'original_trigger',
        'source':         'original',
    }

    # 仅在矛盾信号出现时触发推理
    _should = (trigger_confidence >= 70 and kronos_pup > 0.60)
    if not _should:
        return _base

    # 缓存key
    _price_round = round(price, -1)
    _cache_key = f"{symbol}_{direction}_{int(_price_round)}_{trigger_confidence}"
    cached = _load_cache(_cache_key)
    if cached:
        return cached

    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        from reasoning_client import call_reasoning

        _liq_info = []
        if liq_above > 0:
            _dist_up = abs(liq_above - price) / price * 100
            _liq_info.append(f"上方流动性池={liq_above:.2f}(距{_dist_up:.2f}%)")
        if liq_below > 0:
            _dist_dn = abs(price - liq_below) / price * 100
            _liq_info.append(f"下方流动性池={liq_below:.2f}(距{_dist_dn:.2f}%)")

        prompt = f"""作为顶级合约交易员，判断{symbol} {direction}信号是否应该立即入场。

当前状态：
- 价格={price:.2f}，入场区={entry_lo:.2f}~{entry_hi:.2f}
- 15M触发置信度={trigger_confidence}/100
- Wick拒绝信号={wick_type}
- Kronos上行概率={kronos_pup:.2f}（{'反转看多！' if kronos_pup>0.7 else '中性'}）
- {chr(10).join(_liq_info) if _liq_info else '无明显流动性池'}
- 订单薄={orderbook_imbalance or '未知'}
- 体制={regime}

关键矛盾：{direction}信号 但Kronos上行概率={kronos_pup:.0%}

判断：立即入场 还是 等待更好时机？
JSON回复：{{"execute_now":true/false,"wait_for":"等待条件或空字符串","optimal_entry":{(entry_lo+entry_hi)/2:.0f},"confidence_adj":整数-20到10,"reason":"一句话"}}"""

        r = call_reasoning(prompt, max_tokens=150, temperature=0.1)

        if r.get('error') or not r.get('content'):
            raise ValueError(r.get('error', 'empty'))

        content = r['content']
        start = content.find('{')
        end   = content.rfind('}') + 1
        if start >= 0 and end > start:
            parsed = json.loads(content[start:end])
            result = {
                'execute_now':    bool(parsed.get('execute_now', True)),
                'wait_for':       str(parsed.get('wait_for', '')),
                'optimal_entry':  float(parsed.get('optimal_entry', (entry_lo+entry_hi)/2)),
                'confidence_adj': max(-20, min(10, int(parsed.get('confidence_adj', 0)))),
                'reasoning':      str(parsed.get('reason', '')),
                'source':         'reasoning_model',
            }
            _save_cache(_cache_key, result)
            return result

    except Exception:
        pass

    return _base
