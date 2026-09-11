#!/usr/bin/env python3
"""
nl_strategy_bridge.py — 自然语言 -> 策略 -> 回测 桥接
设计院 2026-09-11 苏摩111封印

Minara Harness启发：苏摩说一句中文 -> 出回测结果
链路：
  1. free_llm_client 解析中文 -> 结构化策略参数
  2. 调用 dharma_training_engine.walkforward_backtest(symbol)
  3. 输出回测结果（WR、净值、回撤、夏普）

用法：
  python3 scripts/nl_strategy_bridge.py "做多BTC，RSI<30入场，ATR止损"
  python3 scripts/nl_strategy_bridge.py "做空ETH，布林带突破入场"
"""

import sys, os, json, time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'brahma_brain'))

# ── NL -> 策略参数解析 ─────────────────────────────────────────

STRATEGY_TEMPLATE = {
    'symbol': 'BTCUSDT',
    'direction': 'LONG',
    'entry_logic': 'rsi_oversold',
    'entry_param': 30,
    'sl_logic': 'atr',
    'sl_mult': 1.5,
    'tp_mult': 2.0,
    'regime_filter': True,
    'timeframe': '15m',
    'confirm_tf': '1h',
}

NL_SYSTEM = """你是一个量化策略参数解析器。
用户会用中文描述一个交易策略，你需要解析成结构化JSON参数。
输出格式（严格JSON，不要多余文字）：
{
  "symbol": "BTCUSDT" or "ETHUSDT",
  "direction": "LONG" or "SHORT",
  "entry_logic": "rsi_oversold" | "rsi_overbought" | "bb_breakout" | "squeeze" | "macd_cross" | "trend_follow",
  "entry_param": number,
  "sl_logic": "atr" | "percent" | "structure",
  "sl_mult": number,
  "tp_mult": number,
  "regime_filter": true,
  "timeframe": "15m",
  "confirm_tf": "1h"
}
规则：
- "做多" -> LONG, "做空" -> SHORT
- "BTC" -> BTCUSDT, "ETH" -> ETHUSDT
- RSI<X -> rsi_oversold, param=X
- RSI>X -> rsi_overbought, param=X
- 布林带突破 -> bb_breakout
- 方仓/压缩 -> squeeze
- MACD金叉/死叉 -> macd_cross
- 趋势跟踪 -> trend_follow
- ATR止损 -> sl_logic=atr, sl_mult=倍数
- 百分比止损 -> sl_logic=percent
- 默认sl_mult=1.5, tp_mult=2.0
"""

def parse_nl_strategy(user_text: str) -> dict:
    """用free_llm_client解析中文策略描述"""
    try:
        from free_llm_client import chat
        prompt = f'解析以下策略描述为JSON（只输出JSON，不要其他文字）：\n{user_text}'
        resp = chat(prompt, system=NL_SYSTEM, max_tokens=300, task='council', timeout=15)
        if not resp:
            return _fallback_parse(user_text)
        # 提取JSON
        resp = resp.strip()
        if resp.startswith('```'):
            resp = resp.split('```')[1]
            if resp.startswith('json'):
                resp = resp[4:]
        resp = resp.strip('`').strip()
        params = json.loads(resp)
        # 合并默认值
        result = dict(STRATEGY_TEMPLATE)
        result.update(params)
        return result
    except Exception as e:
        return _fallback_parse(user_text)

def _fallback_parse(text: str) -> dict:
    """LLM失败时的规则解析兜底"""
    result = dict(STRATEGY_TEMPLATE)
    t = text.lower()
    # 标的
    if 'eth' in t or '以太' in t:
        result['symbol'] = 'ETHUSDT'
    elif 'btc' in t or '比特币' in t:
        result['symbol'] = 'BTCUSDT'
    # 方向
    if '做空' in t or '空' in t or 'short' in t:
        result['direction'] = 'SHORT'
    elif '做多' in t or '多' in t or 'long' in t:
        result['direction'] = 'LONG'
    # 入场逻辑
    if 'rsi' in t:
        result['entry_logic'] = 'rsi_oversold' if '做多' in t or 'long' in t else 'rsi_overbought'
        import re
        m = re.search(r'rsi[<不到过]*([\d.]+)', t)
        if m:
            result['entry_param'] = float(m.group(1))
        else:
            result['entry_param'] = 30 if result['direction'] == 'LONG' else 70
    elif '布林' in t or 'bb' in t or '布林带' in t:
        result['entry_logic'] = 'bb_breakout'
    elif '方仓' in t or '压缩' in t or 'squeeze' in t:
        result['entry_logic'] = 'squeeze'
    return result

# ── 回测执行 ───────────────────────────────────────────────────

def run_strategy_backtest(params: dict) -> dict:
    """根据策略参数运行回测"""
    try:
        from dharma_training_engine import walkforward_backtest, build_wr_matrix
    except ImportError:
        return {'error': 'dharma_training_engine不可用'}

    symbol = params['symbol']
    if not symbol.endswith('USDT'):
        symbol += 'USDT'

    # 检查parquet是否存在，不存在则从jsonl.gz加载
    from dharma_training_engine import load_symbol as _dt_load, DATA as _DATA
    _pq_15m = _DATA / symbol.lower() / f'{symbol.lower()}_15m.parquet'
    _pq_1h  = _DATA / symbol.lower() / f'{symbol.lower()}_1h.parquet'
    if not _pq_15m.exists() or not _pq_1h.exists():
        # 从jsonl.gz加载并转DataFrame
        import gzip, json as _json
        import pandas as pd
        _gz_15m = _DATA / f'{symbol}_15m.jsonl.gz'
        _gz_1h  = _DATA / f'{symbol}_1h.jsonl.gz'
        if not _gz_15m.exists() or not _gz_1h.exists():
            return {'error': f'{symbol}: 缺少15m或1h历史数据(jsonl.gz/parquet)'}
        # Monkey-patch load_symbol以支持jsonl.gz
        import dharma_training_engine as _dt
        _orig_load = _dt.load_symbol
        def _patched_load(sym, tf):
            # 先试parquet
            df = _orig_load(sym, tf)
            if df is not None:
                return df
            # fallback to jsonl.gz
            gz = _DATA / f'{sym.upper()}_{tf}.jsonl.gz'
            if not gz.exists():
                return None
            rows = []
            with gzip.open(str(gz), 'rt') as f:
                for line in f:
                    if line.strip():
                        rows.append(_json.loads(line.strip()))
            if not rows:
                return None
            df = pd.DataFrame(rows)
            # 映射缩写列名为全名
            col_map = {'c':'close', 'h':'high', 'l':'low', 'v':'volume', 'o':'open', 'ts':'ts'}
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            for col in ('close','high','low','volume','open'):
                if col in df.columns:
                    df[col] = df[col].astype(float)
            return df
        _dt.load_symbol = _patched_load

    print(f'Running walkforward backtest for {symbol}...', flush=True)
    t0 = time.time()
    result = walkforward_backtest(symbol, verbose=True)
    elapsed = time.time() - t0

    if 'error' in result:
        return result

    trades = result.get('trades', [])
    n = result.get('n', 0)

    if n == 0:
        return {'symbol': symbol, 'trades': 0, 'error': 'No trades generated'}

    # 按方向过滤
    direction = params['direction']
    filtered = [t for t in trades if t.get('direction') == direction]
    if not filtered:
        return {'symbol': symbol, 'trades': n, 'filtered': 0, 'error': f'No {direction} trades'}

    # 统计
    wins = sum(1 for t in filtered if t.get('win'))
    losses = sum(1 for t in filtered if not t.get('win'))
    wr = wins / len(filtered) if filtered else 0

    pnl_list = [t.get('pnl_pct', 0) for t in filtered]
    cum_pnl = sum(pnl_list)
    avg_pnl = cum_pnl / len(pnl_list) if pnl_list else 0

    # 净值曲线
    equity = [100.0]
    for p in pnl_list:
        equity.append(equity[-1] * (1 + p / 100))

    max_dd = 0
    peak = equity[0]
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # 夏普近似
    import math
    if len(pnl_list) > 1:
        mean_p = sum(pnl_list) / len(pnl_list)
        std_p = (sum((p - mean_p)**2 for p in pnl_list) / (len(pnl_list) - 1)) ** 0.5
        sharpe = (mean_p / std_p * math.sqrt(252 * 24)) if std_p > 0 else 0
    else:
        sharpe = 0

    # 按体制分桶
    by_regime = {}
    for t in filtered:
        r = t.get('regime_1h', 'UNKNOWN')
        if r not in by_regime:
            by_regime[r] = {'wins': 0, 'total': 0, 'pnl_sum': 0}
        by_regime[r]['total'] += 1
        if t.get('win'):
            by_regime[r]['wins'] += 1
        by_regime[r]['pnl_sum'] += t.get('pnl_pct', 0)

    return {
        'symbol': symbol,
        'direction': direction,
        'strategy': params['entry_logic'],
        'trades_total': n,
        'trades_filtered': len(filtered),
        'wr': round(wr * 100, 1),
        'wins': wins,
        'losses': losses,
        'cum_pnl_pct': round(cum_pnl, 2),
        'avg_pnl_pct': round(avg_pnl, 3),
        'max_drawdown_pct': round(max_dd, 2),
        'sharpe_approx': round(sharpe, 2),
        'final_equity': round(equity[-1], 2),
        'by_regime': {
            r: {
                'n': v['total'],
                'wr': round(v['wins'] / v['total'] * 100, 1) if v['total'] else 0,
                'pnl_sum': round(v['pnl_sum'], 2),
            }
            for r, v in by_regime.items()
        },
        'elapsed_s': round(elapsed, 1),
    }

# ── 格式化输出 ─────────────────────────────────────────────────

def format_result(params: dict, result: dict) -> str:
    """格式化回测结果输出"""
    lines = [
        '=' * 50,
        'NL Strategy Backtest Result',
        '=' * 50,
        f'Strategy: {params["direction"]} {params["symbol"]} | {params["entry_logic"]}',
        f'Entry: {params.get("entry_param", "N/A")} | SL: {params["sl_logic"]} x{params["sl_mult"]} | TP: x{params["tp_mult"]}',
        '-' * 50,
    ]

    if 'error' in result:
        lines.append(f'ERROR: {result["error"]}')
        return '\n'.join(lines)

    lines += [
        f'Trades: {result["trades_filtered"]}/{result["trades_total"]}',
        f'WR: {result["wr"]}% ({result["wins"]}W / {result["losses"]}L)',
        f'Cum PnL: {result["cum_pnl_pct"]:+.2f}%',
        f'Avg PnL: {result["avg_pnl_pct"]:+.3f}%/trade',
        f'Max DD: {result["max_drawdown_pct"]:.2f}%',
        f'Sharpe (approx): {result["sharpe_approx"]}',
        f'Final Equity: {result["final_equity"]:.2f} (base=100)',
        f'Elapsed: {result["elapsed_s"]}s',
        '',
        'By Regime:',
    ]
    for r, v in sorted(result.get('by_regime', {}).items()):
        lines.append(f'  {r}: n={v["n"]} WR={v["wr"]}% PnL={v["pnl_sum"]:+.2f}%')

    lines += [
        '',
        '=' * 50,
        'WARNING: Backtest != Future. Overfitting risk exists.',
        '=' * 50,
    ]
    return '\n'.join(lines)


# ── 主入口 ─────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print('Usage: python3 nl_strategy_bridge.py "做多BTC，RSI<30入场，ATR止损"')
        sys.exit(1)

    user_text = ' '.join(sys.argv[1:])
    print(f'Parsing: {user_text}', flush=True)

    params = parse_nl_strategy(user_text)
    print(f'Parsed: {json.dumps(params, ensure_ascii=False)}', flush=True)

    result = run_strategy_backtest(params)
    output = format_result(params, result)
    print(output)


if __name__ == '__main__':
    main()
