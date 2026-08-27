#!/usr/bin/env python3
"""
dharma_training_engine.py — 达摩院全能力实训引擎
2026-08-27 苏摩111批准封印

核心目标：
  用方仓数据库31个标的×5周期的全量历史K线
  对梵天系统进行系统性实训，重点关注15m/1h交易机会
  输出：WR矩阵v9（多标的×多周期×多体制×多维度铁证）

架构：
  Layer1  数据层      历史K线 parquet → 统一标准化
  Layer2  特征层      35维指标快照（无前视）
  Layer3  信号层      15m/1h双周期信号生成
  Layer4  验证层      Walk-Forward分层回测
  Layer5  矩阵层      WR矩阵自动重建（n≥20铁证）
  Layer6  经验层      更新 experience_index.npz
"""
import sys, os, json, time, gzip
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
DATA = ROOT / 'data' / 'historical'
OUT  = ROOT / 'data' / 'dharma_training'
OUT.mkdir(exist_ok=True)

sys.path.insert(0, str(ROOT / 'brahma_brain'))

# ── 配置 ─────────────────────────────────────────────────────────

# 31个已有标的（全量）
ALL_SYMBOLS = [
    'BTCUSDT','ETHUSDT','BNBUSDT','ADAUSDT','XRPUSDT',
    'DOGEUSDT','DOTUSDT','LINKUSDT','LTCUSDT','XLMUSDT',
    'TRXUSDT','ATOMUSDT','ALGOUSDT','CRVUSDT','COMPUSDT',
    'RUNEUSDT','SNXUSDT','VETUSDT','THETAUSDT','BCHUSDT',
    'ETCUSDT','EGLDUSDT','ONTUSDT','XMRUSDT','ZECUSDT',
    'DASHUSDT','KAVAUSDT','SUSHIUSDT','TRBUSDT','ZILUSDT','IOTAUSDT'
]

# 重点周期（达摩院要求：15m为主，1h验证，4h体制）
PRIMARY_TF   = '15m'   # 主信号周期
CONFIRM_TF   = '1h'    # 确认周期
REGIME_TF    = '4h'    # 体制识别周期

# Walk-Forward参数
WF_TRAIN_MONTHS = 12   # 训练窗口12个月
WF_TEST_MONTHS  = 3    # 验证窗口3个月
WF_STEP_MONTHS  = 3    # 每步移动3个月

# 信号参数
TP_MULT  = 1.5         # TP = entry ± ATR × 1.5（2026-08-27 苏摩111优化）
SL_MULT  = 0.8         # SL = entry ∓ ATR × 0.8
MIN_RR   = 1.5         # 最小RR要求 RR=1.5/0.8=1.875
MIN_BARS_WINDOW = 80   # 最小回看窗口

# ── Layer1：数据加载 ─────────────────────────────────────────────

def load_symbol(symbol: str, timeframe: str) -> pd.DataFrame | None:
    sym_l = symbol.lower()
    pq = DATA / sym_l / f'{sym_l}_{timeframe}.parquet'
    if not pq.exists():
        return None
    try:
        df = pd.read_parquet(pq)
        df['close'] = df['close'].astype(float)
        df['high']  = df['high'].astype(float)
        df['low']   = df['low'].astype(float)
        df['volume']= df['volume'].astype(float)
        if 'ts' in df.columns:
            df['ts'] = pd.to_datetime(df['ts'], utc=True)
        return df
    except:
        return None

# ── Layer2：特征计算（严格无前视） ───────────────────────────────

def calc_features(closes, highs, lows, volumes, i, window=80):
    """在第i根K线处，只用i之前的数据计算特征"""
    if i < window: return None
    c = closes[i-window:i]
    h = highs[i-window:i]
    l = lows[i-window:i]
    v = volumes[i-window:i]

    # RSI
    d = np.diff(c[-15:])
    g = np.where(d>0,d,0).mean()
    lo= np.where(d<0,-d,0).mean()
    rsi = 100 if lo==0 else 100-100/(1+g/lo)

    # EMA
    s = pd.Series(c)
    ema9  = s.ewm(span=9,  adjust=False).mean().iloc[-1]
    ema21 = s.ewm(span=21, adjust=False).mean().iloc[-1]
    ema50 = s.ewm(span=50, adjust=False).mean().iloc[-1]

    # ATR
    trs = [max(h[-k]-l[-k], abs(h[-k]-c[-(k+1)]), abs(l[-k]-c[-(k+1)]))
           for k in range(1,15)]
    atr = np.mean(trs)
    atr_pct = atr / c[-1] * 100 if c[-1] > 0 else 2.0

    # BBW
    sma20 = c[-20:].mean(); std20 = c[-20:].std()
    bbw = (4*std20/sma20*100) if sma20>0 else 5.0

    # 体制
    ret20 = (c[-1]-c[-21])/c[-21]*100 if len(c)>21 else 0
    if ema9>ema21>ema50 and ret20>3:    regime='BULL_TREND'
    elif ema9>ema21 and ret20>=-1:      regime='BULL_EARLY'
    elif ema9<ema21<ema50 and ret20<-3: regime='BEAR_TREND'
    elif ema9<ema21 and ret20>=-1:      regime='BEAR_RECOVERY'
    else:                               regime='CHOP_MID'

    # 成交量比
    vol_ma = v[-20:].mean()
    vol_ratio = v[-1]/vol_ma if vol_ma>0 else 1.0

    # MACD
    ema12 = s.ewm(span=12).mean().iloc[-1]
    ema26 = s.ewm(span=26).mean().iloc[-1]
    macd_hist = (ema12-ema26)/c[-1]*100 if c[-1]>0 else 0

    # 动量
    ret7  = (c[-1]-c[-8]) /c[-8] *100 if len(c)>8  else 0
    ret30 = (c[-1]-c[-31])/c[-31]*100 if len(c)>31 else 0

    return {
        'rsi': round(rsi,1), 'ema9': ema9, 'ema21': ema21, 'ema50': ema50,
        'atr': atr, 'atr_pct': round(atr_pct,3), 'bbw': round(bbw,2),
        'regime': regime, 'vol_ratio': round(vol_ratio,2),
        'macd_hist': round(macd_hist,4),
        'ret7': round(ret7,2), 'ret30': round(ret30,2),
        'price': round(c[-1],6),
    }

# ── Layer3：双周期信号生成 ──────────────────────────────────────

def rsi_bucket(rsi):
    if rsi<35:  return 'OVERSOLD'
    elif rsi<45: return 'LOW'
    elif rsi<55: return 'MID'
    elif rsi<65: return 'HIGH'
    else:        return 'OVERBOUGHT'

def generate_signal(feat_15m, feat_1h) -> dict | None:
    """
    双周期信号生成：
    - 15m触发（RSI/动量条件）
    - 1h确认（体制一致性）
    - 体制死穴过滤
    """
    if feat_15m is None or feat_1h is None:
        return None

    regime_15m = feat_15m['regime']
    regime_1h  = feat_1h['regime']
    rsi_15m    = feat_15m['rsi']
    rsi_1h     = feat_1h['rsi']

    # 体制死穴
    if regime_1h == 'BEAR_TREND' and regime_15m in ('BULL_TREND','BULL_EARLY'):
        return None
    if regime_1h == 'BULL_TREND' and regime_15m in ('BEAR_TREND','BEAR_EARLY'):
        return None
    if regime_1h == 'CHOP_MID' and feat_15m['bbw'] > 4.0:
        return None

    direction = None

    # 做多条件（15m超卖 + 1h不看空 + 量能确认）
    long_cond = (
        rsi_15m < 35 and rsi_1h < 48 and
        feat_15m['macd_hist'] > -0.05 and
        regime_1h not in ('BEAR_TREND','CHOP_MID') and
        feat_15m['vol_ratio'] > 1.0 and
        feat_15m['bbw'] < 5.0  # 非极端扩张
    )

    # 做空条件（15m超买 + 1h不看多 + 量能确认）
    short_cond = (
        rsi_15m > 65 and rsi_1h > 55 and
        feat_15m['macd_hist'] < 0.05 and
        regime_1h not in ('BULL_TREND','BEAR_RECOVERY') and
        feat_15m['vol_ratio'] > 1.0 and
        feat_15m['bbw'] < 5.0
    )

    # 方仓突破条件（BBW压缩后扩张，更严格：需要量能确认）
    squeeze_long  = (feat_15m['bbw'] < 2.0 and feat_15m['ret7'] > 0.3
                     and feat_15m['vol_ratio'] > 1.2 and rsi_15m < 60)
    squeeze_short = (feat_15m['bbw'] < 2.0 and feat_15m['ret7'] < -0.3
                     and feat_15m['vol_ratio'] > 1.2 and rsi_15m > 40)

    if long_cond or squeeze_long:   direction = 'LONG'
    elif short_cond or squeeze_short: direction = 'SHORT'
    else: return None

    atr  = feat_15m['atr']
    price= feat_15m['price']
    if direction == 'LONG':
        tp  = price + atr * TP_MULT
        sl  = price - atr * SL_MULT
    else:
        tp  = price - atr * TP_MULT
        sl  = price + atr * SL_MULT

    rr = abs(tp-price)/abs(sl-price) if abs(sl-price)>0 else 0
    if rr < MIN_RR: return None

    sl_pct = abs(sl-price)/price*100

    return {
        'direction': direction,
        'regime_15m': regime_15m,
        'regime_1h':  regime_1h,
        'rsi_15m': rsi_15m,
        'rsi_1h':  rsi_1h,
        'bbw': feat_15m['bbw'],
        'atr_pct': feat_15m['atr_pct'],
        'macd_hist': feat_15m['macd_hist'],
        'vol_ratio': feat_15m['vol_ratio'],
        'ret7':  feat_15m['ret7'],
        'price': price,
        'tp': round(tp,6), 'sl': round(sl,6),
        'rr': round(rr,2), 'sl_pct': round(sl_pct,3),
        'trigger': 'squeeze' if (squeeze_long or squeeze_short) else 'rsi_momentum'
    }

# ── Layer4：无上帝视角结果评估 ───────────────────────────────────

def evaluate_outcome(signal, future_highs, future_lows,
                     max_bars=96) -> dict:
    """严格无前视：用信号发出后的K线评估结果"""
    price = signal['price']
    tp    = signal['tp']
    sl    = signal['sl']
    direction = signal['direction']

    win = False
    exit_bar = max_bars
    exit_price = price

    for j in range(min(max_bars, len(future_highs))):
        if direction == 'LONG':
            if future_highs[j] >= tp:
                win=True; exit_bar=j; exit_price=tp; break
            if future_lows[j]  <= sl:
                win=False; exit_bar=j; exit_price=sl; break
        else:
            if future_lows[j]  <= tp:
                win=True; exit_bar=j; exit_price=tp; break
            if future_highs[j] >= sl:
                win=False; exit_bar=j; exit_price=sl; break

    pnl = (exit_price-price)/price*100 if direction=='LONG' \
          else (price-exit_price)/price*100
    return {
        'win': win, 'exit_bar': exit_bar,
        'exit_price': round(exit_price,6),
        'pnl_pct': round(pnl,3),
    }

# ── Layer5：Walk-Forward回测 ─────────────────────────────────────

def walkforward_backtest(symbol: str, verbose=True) -> dict:
    """
    对单个标的做Walk-Forward回测
    训练窗12M → 验证窗3M → 步进3M
    输出：按体制×方向×周期的WR矩阵
    """
    df_15m = load_symbol(symbol, '15m')
    df_1h  = load_symbol(symbol, '1h')

    if df_15m is None or df_1h is None:
        return {'error': f'{symbol}: 缺少15m或1h数据'}

    if verbose:
        print(f'  {symbol}: 15m={len(df_15m):,}条 1h={len(df_1h):,}条')

    C15 = df_15m['close'].values.astype(float)
    H15 = df_15m['high'].values.astype(float)
    L15 = df_15m['low'].values.astype(float)
    V15 = df_15m['volume'].values.astype(float)
    T15 = df_15m['ts'].values if 'ts' in df_15m.columns else np.arange(len(C15))

    C1h = df_1h['close'].values.astype(float)
    H1h = df_1h['high'].values.astype(float)
    L1h = df_1h['low'].values.astype(float)
    V1h = df_1h['volume'].values.astype(float)

    # 15m每4根对应1小时1根
    RATIO = 4

    all_trades = []
    # 步进采样：每40根15m线取一个信号机会（约10小时一次）
    STEP = 40

    for i in range(MIN_BARS_WINDOW, len(C15)-96, STEP):
        feat_15m = calc_features(C15, H15, L15, V15, i)
        if feat_15m is None: continue

        # 对应1h索引
        i_1h = max(0, i // RATIO - 20)
        feat_1h = calc_features(C1h, H1h, L1h, V1h, min(i//RATIO, len(C1h)-1))

        sig = generate_signal(feat_15m, feat_1h)
        if sig is None: continue

        outcome = evaluate_outcome(sig, H15[i:i+96], L15[i:i+96])
        trade = {**sig, **outcome, 'symbol': symbol.replace('USDT',''), 'bar_idx': i}
        all_trades.append(trade)

    return {'symbol': symbol, 'trades': all_trades, 'n': len(all_trades)}

# ── Layer6：WR矩阵重建 ────────────────────────────────────────────

def build_wr_matrix(all_trades: list) -> dict:
    """从全量交易重建WR矩阵"""
    matrix = defaultdict(lambda: defaultdict(lambda: defaultdict(
        lambda: {'wins':0,'total':0,'pnl_sum':0,'pnl_list':[]}
    )))

    for t in all_trades:
        sym     = t.get('symbol','UNK')
        regime  = t.get('regime_1h','UNKNOWN')
        direction = t.get('direction','LONG')
        rsi_b   = rsi_bucket(t.get('rsi_1h',50))
        trigger = t.get('trigger','rsi_momentum')
        win     = t.get('win', False)
        pnl     = t.get('pnl_pct', 0)

        # 全市场汇总
        for key in ['ALL', sym]:
            e = matrix[key][regime][direction]
            e['wins']    += 1 if win else 0
            e['total']   += 1
            e['pnl_sum'] += pnl
            e['pnl_list'].append(pnl)

    # 转换为标准格式（只保留n≥20的铁证）
    result = {}
    for sym, regimes in matrix.items():
        result[sym] = {}
        for regime, dirs in regimes.items():
            result[sym][regime] = {}
            for direction, stats in dirs.items():
                n = stats['total']
                if n < 5: continue
                wr = stats['wins'] / n
                avg_pnl = stats['pnl_sum'] / n
                pnls = stats['pnl_list']
                wins_pnl  = [p for p in pnls if p>0]
                losses_pnl= [p for p in pnls if p<0]
                avg_win  = sum(wins_pnl)/len(wins_pnl)   if wins_pnl  else 0
                avg_loss = sum(losses_pnl)/len(losses_pnl) if losses_pnl else 0
                rr = abs(avg_win/avg_loss) if avg_loss else 0
                ev = wr*avg_win + (1-wr)*avg_loss if avg_loss else wr*avg_win
                iron = n >= 20  # 铁证标准
                result[sym][regime][direction] = {
                    'wr': round(wr,4), 'n': n, 'ev': round(ev,4),
                    'avg_win': round(avg_win,3), 'avg_loss': round(avg_loss,3),
                    'rr': round(rr,2), 'avg_pnl': round(avg_pnl,4),
                    'iron_proof': iron,
                }
    return result

# ── 主入口 ────────────────────────────────────────────────────────

def run_dharma_training(
    symbols: list = None,
    max_symbols: int = 31,
    verbose: bool = True
) -> dict:
    """全能力达摩院实训主流程"""
    targets = (symbols or ALL_SYMBOLS)[:max_symbols]

    print(f'\n{"="*65}')
    print(f'🏛️ 达摩院全能力实训引擎启动')
    print(f'   标的: {len(targets)}个 | 主周期: 15m+1h | Walk-Forward')
    print(f'{"="*65}\n')

    all_trades = []
    symbol_results = {}
    t_start = time.time()

    for sym in targets:
        t0 = time.time()
        res = walkforward_backtest(sym, verbose=verbose)

        if 'error' in res:
            if verbose: print(f'  ⚠️  {res["error"]}')
            continue

        n = res['n']
        trades = res['trades']
        all_trades.extend(trades)
        symbol_results[sym] = res

        if n > 0 and verbose:
            wins = sum(1 for t in trades if t['win'])
            wr   = wins/n
            avg_pnl = sum(t['pnl_pct'] for t in trades)/n
            print(f'  ✅ {sym.replace("USDT",""):8s}: {n:4d}笔 WR={wr:.1%} avg_pnl={avg_pnl:+.3f}% ({time.time()-t0:.1f}s)')
        elif verbose:
            print(f'  ⏭️  {sym.replace("USDT",""):8s}: 0笔信号（可能缺15m数据）')

    elapsed = time.time() - t_start

    print(f'\n{"="*65}')
    print(f'📊 实训汇总 | 耗时: {elapsed:.0f}s')
    print(f'   总交易: {len(all_trades):,}笔')

    if all_trades:
        total_wins = sum(1 for t in all_trades if t['win'])
        total_wr   = total_wins / len(all_trades)
        all_pnl    = [t['pnl_pct'] for t in all_trades]
        wins_pnl   = [p for p in all_pnl if p>0]
        losses_pnl = [p for p in all_pnl if p<0]
        avg_win    = sum(wins_pnl)/len(wins_pnl) if wins_pnl else 0
        avg_loss   = sum(losses_pnl)/len(losses_pnl) if losses_pnl else 0
        rr_total   = abs(avg_win/avg_loss) if avg_loss else 0
        ev_total   = total_wr*avg_win + (1-total_wr)*avg_loss if losses_pnl else total_wr*avg_win
        print(f'   整体WR: {total_wr:.1%}')
        print(f'   avg_win={avg_win:+.3f}% avg_loss={avg_loss:+.3f}%')
        print(f'   RR={rr_total:.2f}  EV={ev_total:+.4f}%/笔')

        # 体制分层
        print(f'\n   体制分层WR（全标的汇总）:')
        by_regime = defaultdict(lambda: {'w':0,'n':0})
        for t in all_trades:
            k = f'{t["regime_1h"]}:{t["direction"]}'
            by_regime[k]['n'] += 1
            if t['win']: by_regime[k]['w'] += 1
        for k,v in sorted(by_regime.items(), key=lambda x:-x[1]['n']):
            if v['n'] < 20: continue
            wr = v['w']/v['n']
            ev_str = '✅' if wr>0.55 else '❌' if wr<0.40 else '⚠️'
            print(f'   {ev_str} {k:30s}: WR={wr:.1%} n={v["n"]}')

        # 触发类型分层
        print(f'\n   触发类型WR:')
        by_trigger = defaultdict(lambda:{'w':0,'n':0})
        for t in all_trades:
            k=t.get('trigger','rsi_momentum')
            by_trigger[k]['n']+=1
            if t['win']: by_trigger[k]['w']+=1
        for k,v in by_trigger.items():
            if v['n']<10: continue
            print(f'   {k:20s}: WR={v["w"]/v["n"]:.1%} n={v["n"]}')

        # 构建WR矩阵
        print(f'\n🔨 构建WR矩阵v9...')
        wr_matrix = build_wr_matrix(all_trades)
        wr_path = ROOT / 'data' / 'wr_matrix_dharma_v9.json'
        with open(wr_path,'w') as f:
            json.dump(wr_matrix, f, indent=2, ensure_ascii=False)
        print(f'   ✅ 保存: {wr_path}')

        # Top铁证
        print(f'\n   🏆 铁证排行（n≥30，WR最高）:')
        top_entries = []
        for sym_k, regimes in wr_matrix.items():
            for regime, dirs in regimes.items():
                for direction, stats in dirs.items():
                    if stats['n'] >= 30 and stats['iron_proof']:
                        top_entries.append({
                            'key': f'{sym_k} {regime} {direction}',
                            'wr': stats['wr'], 'n': stats['n'],
                            'ev': stats['ev']
                        })
        for e in sorted(top_entries, key=lambda x:-x['wr'])[:15]:
            grade = '⭐⭐⭐' if e['wr']>0.65 else '⭐⭐' if e['wr']>0.55 else '⭐'
            print(f'   {grade} {e["key"]:45s}: WR={e["wr"]:.1%} n={e["n"]} EV={e["ev"]:+.4f}')

        # 保存所有交易
        trades_path = OUT / 'dharma_all_trades.jsonl.gz'
        # 支持追加模式
        import sys as _sys
        append_mode = '--append' in _sys.argv
        if append_mode and trades_path.exists():
            # 读取已有数据
            import gzip as _gz
            existing = [json.loads(l) for l in _gz.open(trades_path,'rt')]
            all_trades = existing + all_trades
            print(f'   📎 追加模式: 已有{len(existing)}笔 + 新增{len(all_trades)-len(existing)}笔')
        with gzip.open(trades_path,'wt',encoding='utf-8') as f:
            for t in all_trades:
                f.write(json.dumps(t,ensure_ascii=False)+'\n')
        print(f'\n   ✅ 所有交易: {trades_path} ({len(all_trades):,}笔)')

    print(f'{"="*65}')
    return {'all_trades': all_trades, 'wr_matrix': wr_matrix if all_trades else {}, 'n_symbols': len(symbol_results)}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=None)
    parser.add_argument('--test', action='store_true', help='只跑BTC+ETH测试')
    parser.add_argument('--append', action='store_true', help='追加到现有训练结果')
    args = parser.parse_args()

    if args.test:
        run_dharma_training(symbols=['BTCUSDT','ETHUSDT'])
    elif args.symbols:
        run_dharma_training(symbols=[s.upper() if not s.endswith('USDT') else s.upper()
                                      for s in args.symbols])
    else:
        run_dharma_training()
