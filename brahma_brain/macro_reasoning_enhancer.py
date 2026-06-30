#!/usr/bin/env python3
"""
macro_reasoning_enhancer.py — P1a 宏观事件推理动态评分增强
设计院封印 · 2026-06-26

职责：
  将原本固定的"宏观+事件=10分"替换为推理模型动态解读
  输出：动态评分 5~20分（无事件=5，FOMC=20，ETF大额流入=15）

接入位置：
  brahma_core.py s19 之后，breakdown['宏观+事件'] 覆盖写入
  TTL=30min缓存，避免高频API调用

苏摩约束：
  - 纯离线辅助，不产生cron
  - 任何异常直接返回原始s19值，不阻断主流程
"""
import os, json, time
from pathlib import Path

_CACHE_FILE = Path(__file__).parent.parent / 'data' / 'macro_reasoning_cache.json'
_CACHE_TTL  = 900   # 15min（苏摩B档升级）


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


def enhance_macro_score(symbol: str, direction: str, regime: str,
                        base_score: float, macro_report: dict) -> dict:
    """
    用推理模型动态解读宏观事件，返回增强后的评分。

    返回:
    {
        'enhanced_score': float,    # 动态评分（5~20）
        'delta': float,             # vs base_score的变化
        'impact': str,              # LOW/MED/HIGH/EXTREME
        'direction_match': bool,    # 宏观方向是否与信号一致
        'key_events': list,         # 当前活跃宏观事件
        'reasoning': str,           # 推理模型判断理由
        'source': str               # 'reasoning_model' or 'fallback'
    }
    """
    # 缓存key
    from datetime import datetime, timezone
    _hour = datetime.now(timezone.utc).strftime('%Y%m%d%H')
    _cache_key = f"{symbol}_{direction}_{regime}_{_hour}"

    cached = _load_cache(_cache_key)
    if cached:
        return cached

    # 提取宏观事件信息
    _fg_val = macro_report.get('macro_report', {}).get('fg_value', 50) or 50
    _fg_label = macro_report.get('macro_report', {}).get('fg_label', 'Neutral') or 'Neutral'
    _macro_score = macro_report.get('macro_score', 0)
    _news_score  = macro_report.get('news_score', 0)
    _blocked     = macro_report.get('blocked', False)
    _events      = macro_report.get('news_report', {}).get('active_events', []) or []

    # 检查推理模型是否可用
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        from reasoning_client import call_reasoning

        _events_str = ', '.join([e.get('name', str(e)) for e in _events[:3]]) if _events else '无重大事件'

        prompt = f"""作为加密市场宏观分析师，评估当前宏观环境对{symbol} {direction}信号的影响。

当前宏观数据：
- 恐惧贪婪指数: {_fg_val} ({_fg_label})
- 活跃宏观事件: {_events_str}
- 事件封锁状态: {'是' if _blocked else '否'}
- 宏观量化评分: {_macro_score:+.0f}
- 新闻事件扣分: {_news_score:+.0f}
- 体制: {regime}，方向: {direction}

请评估宏观环境对此信号的整体支撑程度，并给出动态评分（5~20分）。
JSON格式回复：
{{"impact":"LOW|MED|HIGH|EXTREME","direction_match":true/false,"score":整数5到20,"key_reason":"一句话"}}"""

        r = call_reasoning(prompt, max_tokens=150, temperature=0.1)

        if r.get('error') or not r.get('content'):
            raise ValueError(r.get('error', 'empty'))

        # 解析JSON
        content = r['content']
        start = content.find('{')
        end   = content.rfind('}') + 1
        if start >= 0 and end > start:
            parsed = json.loads(content[start:end])
            enhanced = float(parsed.get('score', base_score))
            # 限制范围
            enhanced = max(5.0, min(20.0, enhanced))
            result = {
                'enhanced_score':  enhanced,
                'delta':           round(enhanced - base_score, 1),
                'impact':          parsed.get('impact', 'MED'),
                'direction_match': bool(parsed.get('direction_match', True)),
                'key_events':      _events[:3],
                'reasoning':       parsed.get('key_reason', ''),
                'source':          'reasoning_model',
            }
            _save_cache(_cache_key, result)
            return result

    except Exception:
        pass

    # Fallback：基于规则的动态评分
    _fallback = base_score
    _impact = 'LOW'
    if _blocked:
        _fallback = 5.0
        _impact = 'EXTREME'
    elif _fg_val <= 20:  # 极度恐惧
        _fallback = 15.0 if direction == 'SHORT' else 8.0
        _impact = 'HIGH'
    elif _fg_val >= 80:  # 极度贪婪
        _fallback = 15.0 if direction == 'LONG' else 8.0
        _impact = 'HIGH'
    elif _events:
        _fallback = max(base_score, 12.0)
        _impact = 'MED'

    result = {
        'enhanced_score':  _fallback,
        'delta':           round(_fallback - base_score, 1),
        'impact':          _impact,
        'direction_match': True,
        'key_events':      _events[:3],
        'reasoning':       'fallback_rule_based',
        'source':          'fallback',
    }
    _save_cache(_cache_key, result)
    return result
