#!/usr/bin/env python3
"""
dharma/sim_brahma_replay.py — 达摩院真实离线回放引擎 v1.0
设计院 × 达摩院 2026-06-18

【核心原则】
  使用真实 brahma_core.analyze() 调用（非简化代理）
  覆盖完整8年K线数据（BTC/ETH 57k+ 根）
  逐根滑动窗口，精确模拟实盘信号生成过程
  输出: dharma/results/sim_signals_YYYYMMDD.jsonl

【与旧 offline_brahma_replay.py 的区别】
  旧版: 简化代理函数 _build_offline_ms() 模拟market_state
  新版: 完整调用 brahma_core.analyze() 含19维+关键位12字段

用法：
  python3 dharma/sim_brahma_replay.py --sym BTCUSDT --fast   # 快速（近2年）
  python3 dharma/sim_brahma_replay.py --sym ETHUSDT          # 完整8年
  python3 dharma/sim_brahma_replay.py --all                  # BTC+ETH全量
  python3 dharma/sim_brahma_replay.py --report               # 只出统计报告
"""

import sys, os, json, time, argparse
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

RESULTS = BASE / 'dharma' / 'results'
RESULTS.mkdir(exist_ok=True)

# ── 配置 ─────────────────────────────────────────────────────────
MIN_SCORE       = 100     # 写入的最低评分门槛（低于此分不记录）
CONFIRM_SCORE   = 138     # 达摩院有效信号门槛（与实盘一致）
WARMUP_BARS     = 200     # 预热窗口（前200根不生成信号，等指标稳定）
SIGNAL_COOLDOWN = 4       # 同标的同方向信号冷却（K线根数，防止重叠）

CHOP_REGIMES = {'CHOP_MID', 'CHOP_HIGH', 'CHOP_LOW'}

SYMBOLS = {
    'BTCUSDT': BASE / 'data/backtest/fixed/btcusdt_1h_fixed.parquet',
    'ETHUSDT': BASE / 'data/backtest/fixed/ethusdt_1h_fixed.parquet',
}

# 各品种 TIER 参数（与实盘 champion_params 对齐）
CHAMPION = {
    'BTCUSDT': {'threshold': 100, 'sl': 2.527, 'tp': 1.964, 'hold': 17},
    'ETHUSDT': {'threshold': 100, 'sl': 2.800, 'tp': 1.800, 'hold': 18},
}


def log(msg): print(f'[SimReplay {datetime.now(timezone.utc).strftime("%H:%M:%S")}] {msg}')


def load_parquet(path: Path):
    """加载 parquet，返回 list of dict (OHLCV)"""
    import pandas as pd
    df = pd.read_parquet(path)
    df = df.sort_index()
    records = []
    for ts, row in df.iterrows():
        records.append({
            'ts':     ts.timestamp() if hasattr(ts, 'timestamp') else float(ts),
            'open':   float(row.get('open',  row.get('o', 0))),
            'high':   float(row.get('high',  row.get('h', 0))),
            'low':    float(row.get('low',   row.get('l', 0))),
            'close':  float(row.get('close', row.get('c', 0))),
            'volume': float(row.get('volume',row.get('v', 0))),
        })
    return records


def regime_bias(regime: str) -> str:
    """体制 → 主力方向"""
    if regime in ('BEAR_TREND', 'BEAR_EARLY'):     return 'SHORT'
    if regime in ('BULL_TREND', 'BULL_EARLY'):     return 'LONG'
    if regime == 'BULL_CORRECTION':                 return 'SHORT'
    if regime == 'BEAR_RECOVERY':                   return 'LONG'
    return None  # CHOP: 跳过


def replay_symbol(symbol: str, fast: bool = False) -> list:
    """
    对单个标的执行完整离线回放。
    返回: list of signal dict（仅 score >= MIN_SCORE 的有效信号）
    """
    path = SYMBOLS.get(symbol)
    if not path or not path.exists():
        log(f'❌ {symbol} parquet不存在: {path}')
        return []

    log(f'📂 加载 {symbol} K线...')
    bars = load_parquet(path)

    # fast模式：只取近2年（约17520根1H K线）
    if fast:
        bars = bars[-17520:]
        log(f'⚡ FAST模式: 最近2年 {len(bars)} 根')
    else:
        log(f'📊 全量模式: {len(bars)} 根 ({bars[0].get("ts",""):.0f} → {bars[-1].get("ts",""):.0f})')

    signals = []
    last_signal_bar = {}  # {方向: bar_idx}，冷却控制
    skipped_chop = 0
    skipped_score = 0
    errors = 0

    from brahma_core import analyze as brahma_analyze
    import warnings
    warnings.filterwarnings('ignore')

    # ── 方案A：激活 OFFLINE_MODE，冻结所有实时API调用 ──────────
    # OI/FR/LSR/Ticker/Basis 等辅助数据使用中性默认值
    # get_klines 仍通过 _inject_offline_klines() 注入本地历史数据
    import brahma_brain.data_cache as _dc
    _dc.OFFLINE_MODE = True
    _dc.OFFLINE_CTX = {
        'fr':          0.0001,
        'oi':          {'oi': 100000, 'ts': 0, 'oi_change_pct': 0.0, 'oi_momentum': 'NEUTRAL'},
        'oi_change':   0.0,
        'lsr':         50.0,
        'top_lsr':     50.0,
        'liquidation': 0.0,
        'gex':         0.0,
        'kronos_p_up': 0.5,
        'whale_flow':  0.0,
        'iv':          0.4,
        'ticker':      {},
        'basis':       {'basis_pct': 0.0, 'mark_price': 0.0, 'index_price': 0.0, 'spread': 0.0},
    }
    _dc.enable_offline_network_block()  # 拦截所有绕过 data_cache 的 Binance API 直接请求

    t_start = time.time()
    scan_count = 0

    for i in range(WARMUP_BARS, len(bars)):
        bar = bars[i]
        cur_price = bar['close']

        # 每隔4根扫描一次（1H×4 = 4H节奏，与实盘扫描节奏一致）
        if (i - WARMUP_BARS) % 4 != 0:
            continue

        scan_count += 1

        try:
            # ── 方案A: 注入历史K线到缓存，让 brahma_analyze 读到正确的历史数据 ──
            # 取当前 bar 往前200根构造仿真实1H K线格式并写入缓存
            _window = bars[max(0, i-199):i+1]  # 最多200根
            _fake_klines = [
                [
                    int(b['ts'] * 1000),  # open_time
                    str(b['open']),
                    str(b['high']),
                    str(b['low']),
                    str(b['close']),
                    str(b['volume']),
                    int(b['ts'] * 1000) + 3599999,  # close_time
                    '0', 0, '0', '0', '0'
                ] for b in _window
            ]
            # 注入多个时间框的K线缓存（brahma会请求1H/4H/15m/1d，覆盖所有常用limit）
            for _lmt in [200, 250, 300, 500, 0]:  # 0=无limit后缀的key
                _key_sfx = _dc._cache_key(symbol, '1h', _lmt) if _lmt else _dc._cache_key(symbol, '1h')
                _dc._cache_set(_key_sfx, _fake_klines, 7200)
            # 4H: 每4根合并一根
            _4h_bars = _fake_klines[::4]
            for _lmt in [200, 250, 300, 60, 0]:
                _key_sfx = _dc._cache_key(symbol, '4h', _lmt) if _lmt else _dc._cache_key(symbol, '4h')
                _dc._cache_set(_key_sfx, _4h_bars, 7200)
            # 15m: 复用同一件K线（尺度略粗，离线近似）
            for _lmt in [200, 250, 300, 0]:
                _key_sfx = _dc._cache_key(symbol, '15m', _lmt) if _lmt else _dc._cache_key(symbol, '15m')
                _dc._cache_set(_key_sfx, _fake_klines, 7200)
            # 1d: 取最近60根（每24根合并一根近似日线）
            _1d_bars = _fake_klines[::24] if len(_fake_klines) >= 24 else _fake_klines
            for _lmt in [60, 200, 0]:
                _key_sfx = _dc._cache_key(symbol, '1d', _lmt) if _lmt else _dc._cache_key(symbol, '1d')
                _dc._cache_set(_key_sfx, _1d_bars, 7200)
            # ticker注入当前收盘价
            _dc._cache_set(_dc._cache_key(symbol, 'ticker'), {
                'lastPrice': str(cur_price), 'bidPrice': str(cur_price),
                'askPrice': str(cur_price), 'volume': str(bars[i]['volume']),
            }, 7200)

            # ── 调用真实 brahma_core.analyze() ──────────────────
            r = brahma_analyze(symbol, signal_dir=None, deep=False)
        except Exception as e:
            errors += 1
            if errors <= 3:
                log(f'⚠ analyze异常 bar={i}: {e}')
            continue

        regime = r.get('regime', '')
        bias   = regime_bias(regime)

        if not bias or regime in CHOP_REGIMES:
            skipped_chop += 1
            continue

        # 冷却检查
        last_bar = last_signal_bar.get(bias, -999)
        if (i - last_bar) < SIGNAL_COOLDOWN:
            continue

        # 重新用正确方向评分
        try:
            r = brahma_analyze(symbol, signal_dir=bias, deep=True)
        except Exception:
            continue

        cf     = r.get('confluence', {}) or {}
        params = r.get('params', {}) or {}
        score  = float(cf.get('total', 0) or 0)

        if score < MIN_SCORE:
            skipped_score += 1
            continue

        # 构建标准化信号
        sig = {
            'signal_id':    f'sim_{symbol}_{i:06d}',
            'bar_idx':      i,
            'bar_ts':       bar['ts'],
            'bar_ts_iso':   datetime.fromtimestamp(bar['ts'], tz=timezone.utc).isoformat(),
            'symbol':       symbol,
            'direction':    bias,
            'regime':       regime,
            'regime_cn':    r.get('regime_cn', ''),
            'price':        cur_price,
            'score':        score,
            'grade':        cf.get('grade', ''),
            'action':       cf.get('action', ''),
            'valid':        score >= CONFIRM_SCORE,
            # 关键位
            'entry_lo':     params.get('entry_lo', cur_price),
            'entry_hi':     params.get('entry_hi', cur_price),
            'stop_loss':    params.get('stop_loss', 0),
            'tp1':          params.get('tp1', 0),
            'tp2':          params.get('tp2', 0),
            'sl_pct':       params.get('sl_pct', 0),
            'rr1':          params.get('rr1', 0),
            'primary_tf':   cf.get('primary_tf', '1H'),
            'entry_source': params.get('entry_source', ''),
            'ob_dist_pct':  params.get('ob_dist_pct', 0),
            'mtf_mode':     cf.get('v2_mode', ''),
            'mtf_4h_align': (cf.get('v2_breakdown') or {}).get('v2_mtf_4h_align', ''),
            # 内部状态（未结算）
            'result':       None,
            'exit_price':   None,
            'pnl_pct':      None,
        }
        signals.append(sig)
        last_signal_bar[bias] = i

        if len(signals) % 50 == 0:
            elapsed = time.time() - t_start
            rate = scan_count / elapsed
            log(f'  {symbol}: {len(signals)} 信号 | 扫描{scan_count}轮 | {rate:.0f}轮/s')

    elapsed = time.time() - t_start
    log(f'✅ {symbol}: {len(signals)} 信号生成完成 '
        f'| 扫描{scan_count}轮 | 跳过CHOP:{skipped_chop} 低分:{skipped_score} '
        f'| 耗时{elapsed:.0f}s')

    return signals


def settle_signals(signals: list, bars: list) -> list:
    """
    精确出场结算 v1.0
    费率：0.04%（Taker 双边）
    滑点：0.03%（保守估计）
    移保本：浮盈≥0.8% 时止损移至入场价+0.1%缓冲
    持仓上限：champion_params.hold 根K线
    """
    FEE_RATE    = 0.0004  # 单边费率
    SLIPPAGE    = 0.0003  # 单边滑点
    MOVE_BE_AT  = 0.008   # 浮盈≥0.8% 移保本
    BE_BUFFER   = 0.001   # 保本缓冲

    settled = []
    for sig in signals:
        bar_idx   = sig['bar_idx']
        direction = sig['direction']
        symbol    = sig['symbol']
        entry_mid = (sig['entry_lo'] + sig['entry_hi']) / 2
        sl        = sig['stop_loss']
        tp1       = sig['tp1']
        tp2       = sig['tp2']
        hold_max  = CHAMPION.get(symbol, {}).get('hold', 18)

        # 入场成本（含费率+滑点）
        cost = FEE_RATE + SLIPPAGE
        entry_actual = entry_mid * (1 + cost if direction == 'LONG' else 1 - cost)

        result     = 'TIMEOUT'
        exit_price = bars[min(bar_idx + hold_max, len(bars)-1)]['close']
        pnl_pct    = 0.0
        be_moved   = False
        sl_dynamic = sl

        # 逐根回放
        for j in range(bar_idx + 1, min(bar_idx + hold_max + 1, len(bars))):
            b = bars[j]
            hi, lo, cl = b['high'], b['low'], b['close']

            if direction == 'SHORT':
                # 止损：向上突破
                if hi >= sl_dynamic:
                    exit_price = sl_dynamic * (1 + SLIPPAGE)
                    result = 'LOSS'
                    break
                # TP1
                if lo <= tp1:
                    exit_price = tp1 * (1 - SLIPPAGE)
                    # 移保本
                    if not be_moved:
                        sl_dynamic = entry_actual * (1 - BE_BUFFER)
                        be_moved = True
                    # TP2 继续持有
                    if tp2 and lo <= tp2:
                        exit_price = tp2 * (1 - SLIPPAGE)
                        result = 'WIN_T2'
                        break
                    result = 'WIN_T1'
                    break
                # 移保本检查（浮盈≥0.8%）
                if not be_moved:
                    float_pnl = (entry_actual - cl) / entry_actual
                    if float_pnl >= MOVE_BE_AT:
                        sl_dynamic = entry_actual * (1 - BE_BUFFER)
                        be_moved = True

            else:  # LONG
                if lo <= sl_dynamic:
                    exit_price = sl_dynamic * (1 - SLIPPAGE)
                    result = 'LOSS'
                    break
                if hi >= tp1:
                    exit_price = tp1 * (1 + SLIPPAGE)
                    if not be_moved:
                        sl_dynamic = entry_actual * (1 + BE_BUFFER)
                        be_moved = True
                    if tp2 and hi >= tp2:
                        exit_price = tp2 * (1 + SLIPPAGE)
                        result = 'WIN_T2'
                        break
                    result = 'WIN_T1'
                    break
                if not be_moved:
                    float_pnl = (cl - entry_actual) / entry_actual
                    if float_pnl >= MOVE_BE_AT:
                        sl_dynamic = entry_actual * (1 + BE_BUFFER)
                        be_moved = True

        # 计算实际PnL（双边费率）
        if direction == 'SHORT':
            pnl_pct = (entry_actual - exit_price) / entry_actual - 2 * FEE_RATE
        else:
            pnl_pct = (exit_price - entry_actual) / entry_actual - 2 * FEE_RATE

        sig_out = dict(sig)
        sig_out.update({
            'result':      result,
            'exit_price':  round(exit_price, 4),
            'pnl_pct':     round(pnl_pct * 100, 4),
            'be_moved':    be_moved,
            'entry_actual': round(entry_actual, 4),
        })
        settled.append(sig_out)

    return settled


def print_stats(settled: list, title: str = ''):
    """打印统计报告"""
    if not settled:
        print('无数据')
        return

    total   = len(settled)
    wins_t1 = [s for s in settled if s['result'] == 'WIN_T1']
    wins_t2 = [s for s in settled if s['result'] == 'WIN_T2']
    losses  = [s for s in settled if s['result'] == 'LOSS']
    timeouts= [s for s in settled if s['result'] == 'TIMEOUT']
    wins    = wins_t1 + wins_t2

    wr = len(wins) / (len(wins) + len(losses)) if (wins or losses) else 0
    avg_win  = sum(s['pnl_pct'] for s in wins)  / len(wins)  if wins   else 0
    avg_loss = sum(s['pnl_pct'] for s in losses) / len(losses) if losses else 0
    pf = abs(avg_win * len(wins)) / abs(avg_loss * len(losses)) if (losses and avg_loss < 0) else 0
    ev = sum(s['pnl_pct'] for s in settled) / total

    valid = [s for s in settled if s.get('valid')]
    valid_wr = len([s for s in valid if 'WIN' in s['result']]) / max(1, len([s for s in valid if s['result'] in ('WIN_T1','WIN_T2','LOSS')])) if valid else 0

    print(f'\n{"═"*55}')
    print(f'  {title or "达摩院回放统计"}')
    print(f'{"═"*55}')
    print(f'  总信号: {total}  |  有效(≥138分): {len(valid)}')
    print(f'  WIN_T1: {len(wins_t1)}  WIN_T2: {len(wins_t2)}  LOSS: {len(losses)}  TIMEOUT: {len(timeouts)}')
    print(f'  胜率(WR):       {wr*100:.1f}%')
    print(f'  有效信号WR:     {valid_wr*100:.1f}%')
    print(f'  avg_win:        +{avg_win:.3f}%')
    print(f'  avg_loss:       {avg_loss:.3f}%')
    print(f'  利润因子(PF):   {pf:.3f}')
    print(f'  期望值(EV):     {ev:.4f}%/信号')
    print()

    # 评分段分析
    score_bands = [(100,120), (120,138), (138,155), (155,200)]
    print('  评分段 WR:')
    for lo, hi in score_bands:
        band = [s for s in settled if lo <= s['score'] < hi]
        if band:
            bw = [s for s in band if 'WIN' in s['result']]
            bl = [s for s in band if s['result'] == 'LOSS']
            bwr = len(bw)/(len(bw)+len(bl)) if (bw or bl) else 0
            print(f'    [{lo:3d}-{hi:3d}) n={len(band):4d}  WR={bwr*100:.1f}%')

    # 体制×方向
    print()
    print('  体制×方向 WR (n≥10):')
    from collections import defaultdict
    matrix = defaultdict(list)
    for s in settled:
        key = f"{s['regime']}×{s['direction']}"
        matrix[key].append(s)
    for key, sigs in sorted(matrix.items()):
        if len(sigs) >= 10:
            w = [s for s in sigs if 'WIN' in s['result']]
            l = [s for s in sigs if s['result'] == 'LOSS']
            wr_ = len(w)/(len(w)+len(l)) if (w or l) else 0
            ev_ = sum(s['pnl_pct'] for s in sigs)/len(sigs)
            flag = '✅' if wr_ >= 0.60 else ('⚠️' if wr_ >= 0.50 else '❌')
            print(f'    {flag} {key:35s} n={len(sigs):4d} WR={wr_*100:.1f}% EV={ev_:.3f}%')

    print(f'{"═"*55}\n')


def main():
    parser = argparse.ArgumentParser(description='达摩院真实离线回放引擎 v1.0')
    parser.add_argument('--sym',    default='BTCUSDT', help='标的 (BTCUSDT/ETHUSDT)')
    parser.add_argument('--all',    action='store_true', help='BTC+ETH全量')
    parser.add_argument('--fast',   action='store_true', help='只回放近2年')
    parser.add_argument('--settle', action='store_true', help='自动执行结算')
    parser.add_argument('--report', action='store_true', help='只输出统计报告')
    parser.add_argument('--save',   action='store_true', default=True, help='保存结果JSON')
    args = parser.parse_args()

    TAG = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')

    if args.all:
        target_syms = list(SYMBOLS.keys())
    else:
        target_syms = [args.sym.upper()]

    all_signals  = []
    all_settled  = []

    for sym in target_syms:
        log(f'\n{"━"*50}')
        log(f'开始回放: {sym}')
        signals = replay_symbol(sym, fast=args.fast)
        all_signals.extend(signals)

        if args.settle or True:  # 默认自动结算
            log(f'开始结算 {sym} {len(signals)} 条信号...')
            bars = load_parquet(SYMBOLS[sym])
            if args.fast:
                bars = bars[-17520:]
            settled = settle_signals(signals, bars)
            all_settled.extend(settled)
            print_stats(settled, f'{sym} 回放统计')

    # 保存结果
    if args.save and all_settled:
        out_path = RESULTS / f'sim_replay_{TAG}.jsonl'
        with open(out_path, 'w') as f:
            for s in all_settled:
                f.write(json.dumps(s, ensure_ascii=False) + '\n')
        log(f'💾 结果已保存: {out_path}')

        # 同时保存汇总 JSON
        summary_path = RESULTS / f'sim_replay_summary_{TAG}.json'
        wins  = [s for s in all_settled if 'WIN' in str(s.get('result',''))]
        losses = [s for s in all_settled if s.get('result') == 'LOSS']
        wr = len(wins)/(len(wins)+len(losses)) if (wins or losses) else 0
        summary = {
            'tag': TAG,
            'symbols': target_syms,
            'fast_mode': args.fast,
            'total_signals': len(all_signals),
            'total_settled': len(all_settled),
            'win_rate': round(wr, 4),
            'wins': len(wins),
            'losses': len(losses),
            'ev_pct': round(sum(s.get('pnl_pct',0) for s in all_settled)/max(1,len(all_settled)), 4),
            'generated_at': TAG,
        }
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        log(f'📊 汇总: {summary_path}')

    if len(target_syms) > 1 and all_settled:
        print_stats(all_settled, f'全标的汇总统计')


if __name__ == '__main__':
    main()
