#!/usr/bin/env python3
"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 止损推理增强，叙事层
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
sl_reasoning_enhancer.py — P1b 止损位推理优化层
设计院封印 · 2026-06-26

职责：
  在ATR数学公式止损基础上，用推理模型综合微观结构给出最优止损位
  输入：ATR止损 + Swing高点 + GEX上沿 + BB上轨 + 流动性池 + Kronos
  输出：推理最优止损位 + 是否收紧/放宽建议

接入位置：
  brahma_core.py dynamic_sl计算后，params['stop_loss']覆盖（可选）
  TTL=15min缓存

苏摩约束：
  - 纯辅助，不强制覆盖，任何异常保留原ATR止损
  - 推理结果仅在置信度>0.75时才覆盖
"""
import json, time, os
from pathlib import Path

_CACHE_FILE = Path(__file__).parent.parent / 'data' / 'sl_reasoning_cache.json'
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


def enhance_stop_loss(symbol: str, direction: str,
                      atr_sl: float, entry: float, price: float,
                      swing_extreme: float = 0.0,
                      gex_boundary: float = 0.0,
                      bb_boundary: float = 0.0,
                      liq_pool: float = 0.0,
                      kronos_pup: float = 0.5,
                      regime: str = '') -> dict:
    """
    用推理模型优化止损位。

    返回:
    {
        'recommended_sl':  float,    # 推理最优止损
        'original_sl':     float,    # ATR原始止损
        'action':          str,      # KEEP / TIGHTEN / WIDEN
        'confidence':      float,
        'reasoning':       str,
        'source':          str       # 'reasoning_model' or 'atr_original'
    }
    """
    # 基础返回（保留原ATR止损）
    _base = {
        'recommended_sl': atr_sl,
        'original_sl':    atr_sl,
        'action':         'KEEP',
        'confidence':     0.5,
        'reasoning':      'atr_default',
        'source':         'atr_original',
    }

    if atr_sl <= 0 or entry <= 0:
        return _base

    # 缓存key（以入场价取整作为key，避免微小变化触发重复调用）
    _entry_round = round(entry, -1)  # 取整到10
    _cache_key = f"{symbol}_{direction}_{int(_entry_round)}"
    cached = _load_cache(_cache_key)
    if cached:
        return cached

    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        from reasoning_client import call_reasoning

        _sl_pct = abs(atr_sl - entry) / entry * 100

        # 构建关键价位信息
        _levels = [f"ATR止损={atr_sl:.2f}({_sl_pct:.2f}%)"]
        if swing_extreme > 0:
            _levels.append(f"Swing极值={swing_extreme:.2f}")
        if gex_boundary > 0:
            _levels.append(f"GEX边界={gex_boundary:.2f}")
        if bb_boundary > 0:
            _levels.append(f"BB轨道={bb_boundary:.2f}")
        if liq_pool > 0:
            _levels.append(f"流动性池={liq_pool:.2f}")

        prompt = f"""作为顶级合约风控师，优化{symbol} {direction}信号的止损位。

当前价: {price:.2f}  入场价: {entry:.2f}
{chr(10).join(_levels)}
Kronos上行概率: {kronos_pup:.2f}  体制: {regime}

规则：
- SHORT止损必须高于入场价，LONG止损必须低于入场价
- 止损应放在关键结构破坏位（Swing/GEX/BB）附近，不是随机价位
- Kronos>0.7时可适当收紧止损（反转概率高，早出为妙）

JSON回复：{{"recommended_sl":{atr_sl:.0f},"action":"KEEP/TIGHTEN/WIDEN","confidence":0.0-1.0,"reason":"一句话"}}"""

        r = call_reasoning(prompt, max_tokens=120, temperature=0.1)

        if r.get('error') or not r.get('content'):
            raise ValueError(r.get('error', 'empty'))

        content = r['content']
        start = content.find('{')
        end   = content.rfind('}') + 1
        if start >= 0 and end > start:
            parsed = json.loads(content[start:end])
            rec_sl = float(parsed.get('recommended_sl', atr_sl))
            conf   = float(parsed.get('confidence', 0.5))
            action = parsed.get('action', 'KEEP')

            # 安全护栏：SHORT时止损必须高于入场，LONG时必须低于入场
            if direction == 'SHORT' and rec_sl <= entry:
                rec_sl = atr_sl
                action = 'KEEP'
            elif direction == 'LONG' and rec_sl >= entry:
                rec_sl = atr_sl
                action = 'KEEP'

            # 只有高置信度才覆盖
            final_sl = rec_sl if conf >= 0.75 else atr_sl

            result = {
                'recommended_sl': final_sl,
                'original_sl':    atr_sl,
                'action':         action,
                'confidence':     conf,
                'reasoning':      parsed.get('reason', ''),
                'source':         'reasoning_model' if conf >= 0.75 else 'atr_low_confidence',
            }
            _save_cache(_cache_key, result)
            return result

    except Exception:
        pass

    return _base
