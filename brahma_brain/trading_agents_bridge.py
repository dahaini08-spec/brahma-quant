"""
brahma_brain/trading_agents_bridge.py — TradingAgents × 梵天 桥接层 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 达摩院 × 苏摩 联合设计 · 2026-06-17

使命：
  将 TradingAgents 多智能体研究层的输出，作为 s24 维度注入梵天评分系统
  核心定位：研究增强层（异步），不进入核心交易循环

设计原则（设计院三原则）：
  1. 只在 BULL_EARLY 体制激活（其他体制系数=0）
  2. 权重上限 10%（最高 +10/-10 分）
  3. fail-safe 必须在位（任何异常返回0）

苏摩约束：
  - 完全异步，结果缓存 6小时
  - 不设高频 cron（仅体制切换 + 每日 9AM UTC 触发）
  - LLM API 未配置时自动降级到 Lite 模式（纯技术指标）

达摩院认证路径：
  M0：Paper 模式（当前）→ 测试输出质量，不注入评分
  M1：离线回放验证（n≥100，WR边际≥+1%）→ 注入评分
  M2：live 实盘（n≥50，WR≥60%）→ 全功率激活

运行模式：
  MODE=paper    → 只输出研究报告，不注入 brahma_core
  MODE=shadow   → 注入评分但不影响门控（影子模式）
  MODE=live     → 完整注入（需达摩院 M1 认证后才允许）
"""

import sys
import os
import time
import json
import logging
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("trading_agents_bridge")

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'external' / 'TradingAgents'))

# ── 体制系数（只在 BULL 体制有意义）────────────────────────────
TA_REGIME_COEFF = {
    'BULL_EARLY':      1.0,   # 核心激活体制（做多短板修复）
    'BULL_TREND':      0.5,   # 辅助确认
    'BULL_CORRECTION': 0.0,   # 由解锁器处理，不叠加
    'BEAR_EARLY':      0.0,   # 空头体制，TA信号无价值
    'BEAR_TREND':      0.0,
    'BEAR_RECOVERY':   0.0,
    'CHOP_MID':        0.0,
    'CHOP_HIGH':       0.0,
    'CHOP_LOW':        0.0,
}

# ── 运行模式 ─────────────────────────────────────────────────────
MODE = os.environ.get('TA_BRIDGE_MODE', 'paper')   # paper / shadow / live
CACHE_TTL = 6 * 3600  # 6小时

# ── 缓存 ─────────────────────────────────────────────────────────
_CACHE: Dict[str, Tuple[float, Dict]] = {}   # {symbol: (ts, result)}


# ════════════════════════════════════════════════════════════════
# Lite 模式（无 LLM API 时的降级方案）
# 用纯技术指标近似 TradingAgents 的输出
# ════════════════════════════════════════════════════════════════
def _lite_analysis(symbol: str, klines_15m: list, regime: str) -> Dict[str, Any]:
    """
    无 LLM 的技术指标版本
    用动量 + RSI + 成交量结构近似 TradingAgents 输出
    这是 M0 Paper 阶段的主力运行模式
    """
    try:
        import numpy as np
        arr = np.array(klines_15m[-200:], dtype=float)
        close = arr[:, 3]
        vol   = arr[:, 4]

        # 近期动量得分（0~1）
        ret_5  = (close[-1] - close[-5])  / close[-5]
        ret_20 = (close[-1] - close[-20]) / close[-20]
        ret_60 = (close[-1] - close[-60]) / close[-60] if len(close) >= 60 else ret_20
        momentum = (ret_5 * 0.5 + ret_20 * 0.3 + ret_60 * 0.2)

        # RSI
        delta = np.diff(close)
        gain  = np.where(delta > 0, delta, 0)
        loss  = np.where(delta < 0, -delta, 0)
        ag    = gain[-14:].mean()
        al    = loss[-14:].mean() + 1e-10
        rsi   = 100 - 100 / (1 + ag / al)

        # 成交量确认
        vol_ratio = vol[-5:].mean() / (vol[-20:].mean() + 1e-10)

        # 综合置信度
        confidence = 0.5
        if 'BULL' in regime:
            confidence += momentum * 3   # 上涨动量加分
            if rsi > 55:  confidence += 0.1
            if vol_ratio > 1.2: confidence += 0.05

        confidence = max(0.1, min(0.95, confidence))

        # 方向判断
        if momentum > 0.005:
            direction_bias = 'LONG'
        elif momentum < -0.005:
            direction_bias = 'SHORT'
        else:
            direction_bias = 'NEUTRAL'

        narrative_score = min(10.0, max(0.0, 5 + momentum * 200))

        return {
            'direction_bias':  direction_bias,
            'confidence':      round(confidence, 3),
            'narrative_score': round(narrative_score, 2),
            'source':          'ta_lite',
            'rsi':             round(rsi, 1),
            'momentum_5':      round(ret_5 * 100, 2),
            'vol_ratio':       round(vol_ratio, 2),
            'regime':          regime,
        }
    except Exception as e:
        return {'direction_bias': 'NEUTRAL', 'confidence': 0.5,
                'narrative_score': 5.0, 'source': 'ta_lite_error',
                'error': str(e)[:40]}


# ════════════════════════════════════════════════════════════════
# Full 模式（有 LLM API 时）
# 调用 TradingAgents 真实多智能体分析
# ════════════════════════════════════════════════════════════════
def _full_analysis(symbol: str, regime: str) -> Optional[Dict[str, Any]]:
    """
    完整 TradingAgents 多智能体分析
    需要 OPENAI_API_KEY 或 ANTHROPIC_API_KEY
    """
    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.default_config import DEFAULT_CONFIG
        import copy

        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg['max_debate_rounds'] = 1          # 限制轮次，降低延迟
        cfg['online_tools'] = False           # 关闭实时工具（避免延迟）
        cfg['llm_provider'] = 'openai'

        ta = TradingAgentsGraph(debug=False, config=cfg)

        # 执行分析（取最近交易日）
        from datetime import date
        trade_date = date.today().strftime('%Y-%m-%d')

        # 仅运行 sentiment + market analyst（最轻量的组合）
        state, _ = ta.propagate(symbol.replace('USDT', ''), trade_date)

        # 提取决策
        decision = state.get('final_trade_decision', '')
        bullish = sum(1 for w in ['buy', 'long', 'bullish', 'upside'] if w in decision.lower())
        bearish = sum(1 for w in ['sell', 'short', 'bearish', 'downside'] if w in decision.lower())
        total = bullish + bearish + 1

        confidence = 0.5 + (bullish - bearish) / total * 0.4
        direction = 'LONG' if bullish > bearish else ('SHORT' if bearish > bullish else 'NEUTRAL')

        return {
            'direction_bias':  direction,
            'confidence':      round(max(0.1, min(0.95, confidence)), 3),
            'narrative_score': round(bullish / total * 10, 2),
            'source':          'ta_full',
            'regime':          regime,
            'raw_decision':    decision[:200],
        }
    except Exception as e:
        logger.warning(f'[TA-Full] {symbol} 分析失败: {e}')
        return None


# ════════════════════════════════════════════════════════════════
# 主接口
# ════════════════════════════════════════════════════════════════
def get_s24_score(
    symbol:   str,
    direction: str,
    klines_15m: list,
    regime:   str = '',
) -> Tuple[int, Dict[str, Any]]:
    """
    TradingAgents s24 维度主接口

    Returns:
        (score, meta)
        score: -10 ~ +10（含体制系数）
        meta:  详细分析结果
    """
    null_meta = {
        'direction_bias': 'NEUTRAL', 'confidence': 0.5,
        'narrative_score': 5.0, 'source': 'ta_skip', 'score': 0
    }

    try:
        # 体制门控（只在BULL体制有价值）
        coeff = TA_REGIME_COEFF.get(regime, 0.0)
        if coeff == 0.0:
            return 0, {**null_meta, 'source': f'ta_regime_gate:{regime}'}

        # 缓存检查
        now = time.time()
        if symbol in _CACHE:
            ts, cached = _CACHE[symbol]
            if now - ts < CACHE_TTL:
                score = _meta_to_score(cached, direction, coeff)
                return score, {**cached, 'score': score, 'source': cached['source'] + '_cache'}

        # 尝试完整模式（有 API key 时）
        result = None
        has_api_key = bool(os.environ.get('OPENAI_API_KEY') or
                           os.environ.get('ANTHROPIC_API_KEY'))

        if has_api_key and MODE in ('shadow', 'live'):
            result = _full_analysis(symbol, regime)

        # 降级到 Lite 模式
        if result is None:
            if len(klines_15m) < 60:
                return 0, {**null_meta, 'source': 'ta_insufficient_data'}
            result = _lite_analysis(symbol, klines_15m, regime)

        _CACHE[symbol] = (now, result)
        score = _meta_to_score(result, direction, coeff)
        result['score'] = score

        # Paper 模式：记录但不影响评分
        if MODE == 'paper':
            _log_paper_result(symbol, direction, regime, result)
            return 0, {**result, 'source': result['source'] + '_paper_mode'}

        return score, result

    except Exception as e:
        logger.warning(f'[TA-Bridge] {symbol} 异常: {e}')
        return 0, {**null_meta, 'source': f'ta_error:{str(e)[:30]}'}


def _meta_to_score(meta: Dict, direction: str, coeff: float) -> int:
    """将 TA 分析结果转换为 s24 分数"""
    confidence = meta.get('confidence', 0.5)
    ta_dir = meta.get('direction_bias', 'NEUTRAL')
    narrative = meta.get('narrative_score', 5.0) / 10.0

    if ta_dir == direction:
        raw = (confidence - 0.5) * 2 * 10 * narrative
    elif ta_dir == 'NEUTRAL':
        raw = 0.0
    else:
        raw = -(confidence - 0.5) * 2 * 10 * narrative * 0.5  # 反向惩罚减半

    final = int(raw * coeff)
    return max(-10, min(10, final))


# ════════════════════════════════════════════════════════════════
# Paper 模式日志（M0 阶段质量评估）
# ════════════════════════════════════════════════════════════════
_PAPER_LOG = BASE / 'data' / 'ta_paper_log.jsonl'

def _log_paper_result(symbol: str, direction: str, regime: str, result: Dict):
    """记录 Paper 模式分析结果，用于 M0 质量评估"""
    entry = {
        'ts':        datetime.now(timezone.utc).isoformat(),
        'symbol':    symbol,
        'direction': direction,
        'regime':    regime,
        **{k: v for k, v in result.items() if k != 'raw_decision'},
    }
    try:
        with open(_PAPER_LOG, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
# M0 Paper 质量评估报告
# ════════════════════════════════════════════════════════════════
def generate_m0_report() -> Dict:
    """
    生成 M0 Paper 阶段质量评估报告
    用于达摩院 M0 → M1 升级决策
    """
    if not _PAPER_LOG.exists():
        return {'status': 'no_data', 'n': 0}

    records = []
    with open(_PAPER_LOG) as f:
        for line in f:
            try:
                records.append(json.loads(line.strip()))
            except Exception:
                pass

    if not records:
        return {'status': 'no_data', 'n': 0}

    n = len(records)
    bull_correct  = sum(1 for r in records if r.get('direction_bias') == 'LONG'  and r.get('regime') == 'BULL_EARLY')
    bear_correct  = sum(1 for r in records if r.get('direction_bias') == 'SHORT' and r.get('regime', '').startswith('BEAR'))
    neutral       = sum(1 for r in records if r.get('direction_bias') == 'NEUTRAL')

    regime_dist = {}
    for r in records:
        k = r.get('regime', '?')
        regime_dist[k] = regime_dist.get(k, 0) + 1

    sources = {}
    for r in records:
        s = r.get('source', '?').replace('_paper_mode', '').replace('_cache', '')
        sources[s] = sources.get(s, 0) + 1

    avg_conf = sum(r.get('confidence', 0.5) for r in records) / n

    return {
        'status':       'ok',
        'n':            n,
        'avg_confidence': round(avg_conf, 3),
        'bull_early_n': regime_dist.get('BULL_EARLY', 0),
        'direction_dist': {'LONG': bull_correct, 'SHORT': bear_correct, 'NEUTRAL': neutral},
        'regime_dist':  regime_dist,
        'source_dist':  sources,
        'note':         f'M0 Paper模式，n={n}，达到n≥30后可评估方向准确率',
        'mode':         MODE,
    }


# ════════════════════════════════════════════════════════════════
# 测试入口
# ════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    import urllib.request

    print('=== TradingAgents Bridge v1.0 · Paper模式测试 ===\n')
    print(f'运行模式: {MODE}')
    print(f'缓存TTL: {CACHE_TTL//3600}h')
    print()

    # 获取 BTC 15m K线
    url = 'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=250'
    raw = json.loads(urllib.request.urlopen(url, timeout=10).read())
    klines = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in raw]
    print(f'K线数量: {len(klines)}\n')

    test_cases = [
        ('BTCUSDT', 'LONG',  'BULL_EARLY'),
        ('BTCUSDT', 'SHORT', 'BULL_EARLY'),
        ('BTCUSDT', 'LONG',  'BULL_TREND'),
        ('BTCUSDT', 'SHORT', 'BEAR_TREND'),
        ('BTCUSDT', 'SHORT', 'CHOP_MID'),
    ]

    for sym, direction, regime in test_cases:
        t0 = time.time()
        score, meta = get_s24_score(sym, direction, klines, regime)
        ms = (time.time() - t0) * 1000
        print(f'  {direction:<6} {regime:<22} s24={score:+3d}  '
              f'bias={meta.get("direction_bias","?"):<8} '
              f'conf={meta.get("confidence",0):.2f}  '
              f'src={meta.get("source","?")}  [{ms:.0f}ms]')

    # M0 报告
    print()
    report = generate_m0_report()
    print('M0 Paper 报告:')
    print(json.dumps(report, indent=2, ensure_ascii=False))
