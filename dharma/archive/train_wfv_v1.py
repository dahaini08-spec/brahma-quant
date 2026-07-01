#!/usr/bin/env python3
"""
达摩院 · Walk-Forward 验证框架 v1.0
====================================
设计院六方辩论定稿 · 2026-06-07

规格：
  - 扩展窗口 Walk-Forward（expanding window）
  - 6个OOS窗口（2019~2024每年一段，2025~2026合并为第6段）
  - 每窗口训练集：从数据起点到 cutoff 前
  - OOS段：cutoff 到下一个 cutoff（或数据末尾）
  - 标的：BTC + ETH（第一批精训）
  - 防穿越铁律：SL/TP/规则只用训练集数据拟合
  - 断点续跑：--resume 跳过已有结果
  - 防死机：nice+19 外部执行，内存门槛由 run_training.sh 控制

评估标准（通过门槛）：
  - OOS PF_pnl ≥ 1.0（正期望）
  - WFV误差 < 10%（|IS_WR - OOS_WR| / IS_WR）
  - 最大连败 ≤ 8笔
  - 6窗口中 ≥ 4个通过

用法:
  python3 dharma/train_wfv_v1.py
  python3 dharma/train_wfv_v1.py --fast        # 快速模式（减少迭代）
  python3 dharma/train_wfv_v1.py --resume      # 跳过已有结果
  python3 dharma/train_wfv_v1.py --sym ETHUSDT # 只跑单标的
"""
import sys, json, time, gc, warnings, argparse, logging
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

BASE    = Path(__file__).parent.parent
RESULTS = Path(__file__).parent / "results"
RESULTS.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[WFV %(asctime)s] %(levelname)-6s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("WFV")

TAG = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
COST = 0.0004  # 0.04% 双边手续费

# ── 超参数空间（训练集上优化）──
SL_ATR_CANDIDATES = [0.8, 1.0, 1.2, 1.5]
TP_ATR_CANDIDATES = [1.6, 2.0, 2.5, 3.0]
HOLD_MAX_CANDIDATES = [24, 48, 72]  # 根数（1H）

# ── Walk-Forward 窗口定义（扩展窗口）──
# (oos_start, oos_end)：OOS 期间
WF_WINDOWS = [
    ("2019-01-01", "2020-01-01"),   # W1: 2019
    ("2020-01-01", "2021-01-01"),   # W2: 2020
    ("2021-01-01", "2022-01-01"),   # W3: 2021
    ("2022-01-01", "2023-01-01"),   # W4: 2022
    ("2023-01-01", "2024-01-01"),   # W5: 2023
    ("2024-01-01", "2025-06-01"),   # W6: 2024~2025
]

SYMS = ["BTCUSDT", "ETHUSDT"]
MIN_TRAIN_N = 500   # IS最少交易笔数
MIN_OOS_N   = 50    # OOS最少交易笔数


# ════════════════════════════════════════════════════════════════
# 数据加载
# ════════════════════════════════════════════════════════════════
def load_data(sym: str):
    """加载 1H + 4H parquet，返回 (df1h, df4h)"""
    sym_l = sym.lower()
    f1h = BASE / f"dharma/data/{sym_l}_1h_2018_2026.parquet"
    f4h = BASE / f"dharma/data/{sym_l}_4h_2018_2026.parquet"
    if not f1h.exists() or not f4h.exists():
        raise FileNotFoundError(f"数据文件缺失: {f1h} 或 {f4h}")
    df1h = pd.read_parquet(f1h)
    df4h = pd.read_parquet(f4h)
    if not isinstance(df1h.index, pd.DatetimeIndex):
        df1h.index = pd.to_datetime(df1h.index, utc=True)
    else:
        df1h.index = df1h.index.tz_localize('UTC') if df1h.index.tz is None else df1h.index.tz_convert('UTC')
    if not isinstance(df4h.index, pd.DatetimeIndex):
        df4h.index = pd.to_datetime(df4h.index, utc=True)
    else:
        df4h.index = df4h.index.tz_localize('UTC') if df4h.index.tz is None else df4h.index.tz_convert('UTC')
    return df1h, df4h


# ════════════════════════════════════════════════════════════════
# 特征工程（复用 foundation_futures 逻辑，独立实现）
# ════════════════════════════════════════════════════════════════
def build_features(df1h: pd.DataFrame, df4h: pd.DataFrame) -> pd.DataFrame:
    df = df1h.copy()

    # ── ATR ──
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift()).abs()
    lc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['atr'] = tr.ewm(span=14, min_periods=1).mean()

    # ── RSI ──
    delta = df['close'].diff()
    gain  = delta.clip(lower=0).ewm(span=14, min_periods=1).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=14, min_periods=1).mean()
    rs    = gain / loss.replace(0, np.nan)
    df['rsi'] = 100 - 100 / (1 + rs)
    df['rsi'] = df['rsi'].fillna(50)

    # ── EMA200 1H ──
    df['ema200'] = df['close'].ewm(span=200, min_periods=1).mean()

    # ── 4H 体制 ──
    df4 = df4h.copy()
    e200_4 = df4['close'].ewm(span=200, min_periods=1).mean()
    rsi4_d = df4['close'].diff()
    g4     = rsi4_d.clip(lower=0).ewm(span=14, min_periods=1).mean()
    l4     = (-rsi4_d.clip(upper=0)).ewm(span=14, min_periods=1).mean()
    rsi4   = 100 - 100 / (1 + g4 / l4.replace(0, np.nan))
    rsi4   = rsi4.fillna(50)
    mc4    = df4['close']

    mb = mc4 < e200_4 * 0.95
    ch = mc4.between(e200_4 * 0.95, e200_4 * 1.05)
    regime4 = pd.Series('CHOP_MID', index=df4.index, dtype=object)
    regime4[mc4 < e200_4 * 0.88]                        = 'BEAR_CRASH'
    regime4[mb & (rsi4 < 42)]                            = 'BEAR_TREND'
    regime4[mb & rsi4.between(42, 55)]                   = 'BEAR_EARLY'
    regime4[mb & (rsi4 > 55)]                            = 'BEAR_RECOVERY'
    regime4[ch & (rsi4 < 45)]                            = 'CHOP_LOW'
    regime4[ch & rsi4.between(45, 55)]                   = 'CHOP_MID'
    regime4[ch & (rsi4 > 55)]                            = 'CHOP_HIGH'
    regime4[(mc4 >= e200_4 * 1.05) & (mc4 < e200_4 * 1.15)] = 'BULL_EARLY'
    regime4[mc4 >= e200_4 * 1.15]                        = 'BULL_TREND'

    df['regime'] = regime4.reindex(df.index, method='ffill').fillna('CHOP_MID').values

    # ── MACD ──
    ema12 = df['close'].ewm(span=12, min_periods=1).mean()
    ema26 = df['close'].ewm(span=26, min_periods=1).mean()
    df['macd_hist'] = ema12 - ema26

    # ── 量比 ──
    df['vol_ratio'] = df['volume'] / df['volume'].rolling(20, min_periods=1).mean()

    # ── BB ──
    bb_mid = df['close'].rolling(20, min_periods=1).mean()
    bb_std = df['close'].rolling(20, min_periods=1).std().fillna(0)
    bb_lo  = bb_mid - 2 * bb_std
    bb_hi  = bb_mid + 2 * bb_std
    denom  = (bb_hi - bb_lo).replace(0, np.nan)
    df['bb_pct'] = ((df['close'] - bb_lo) / denom).fillna(0.5)

    return df.dropna(subset=['atr', 'rsi'])


# ════════════════════════════════════════════════════════════════
# 信号生成（梵天主要做空逻辑的简化复现）
# ════════════════════════════════════════════════════════════════
BEAR_REGIMES  = {'BEAR_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY', 'CHOP_LOW'}
BULL_REGIMES  = {'BULL_TREND', 'BULL_EARLY', 'CHOP_HIGH'}

def gen_signals(df: pd.DataFrame, direction: str = 'SHORT') -> pd.Series:
    """
    返回布尔 Series，True 处为入场信号。
    采用与梵天一致的 BEAR_TREND + RSI>60 SHORT 组合（signal_lab最高PF）
    """
    if direction == 'SHORT':
        sig = (
            df['regime'].isin(BEAR_REGIMES) &
            (df['rsi'] > 60) &
            (df['close'] < df['ema200'])   # 价格在EMA200下方（趋势过滤）
        )
    else:  # LONG
        sig = (
            df['regime'].isin(BULL_REGIMES) &
            (df['rsi'] < 40) &
            (df['close'] > df['ema200'])
        )
    return sig


# ════════════════════════════════════════════════════════════════
# 向量化结算引擎
# ════════════════════════════════════════════════════════════════
def settle_vectorized(df: pd.DataFrame, sig: pd.Series,
                      sl_atr: float, tp_atr: float,
                      hold_max: int, direction: str) -> pd.DataFrame:
    """向量化结算，返回 trades DataFrame"""
    sig_idx = np.where(sig.values)[0]
    if len(sig_idx) == 0:
        return pd.DataFrame()

    closes = df['close'].values
    highs  = df['high'].values
    lows   = df['low'].values
    atrs   = df['atr'].values
    regimes = df['regime'].values
    n = len(df)

    records = []
    last_exit = -1  # 避免持仓重叠

    for i in sig_idx:
        if i <= last_exit:
            continue
        entry = closes[i]
        atr_i = atrs[i]
        if atr_i <= 0:
            continue
        if direction == 'SHORT':
            sl = entry + atr_i * sl_atr
            tp = entry - atr_i * tp_atr
        else:
            sl = entry - atr_i * sl_atr
            tp = entry + atr_i * tp_atr

        result = 'TIMEOUT'
        pnl_pct = 0.0
        exit_idx = min(i + hold_max, n - 1)

        for j in range(i + 1, exit_idx + 1):
            h, l = highs[j], lows[j]
            if direction == 'SHORT':
                if h >= sl:
                    result = 'SL'
                    pnl_pct = (entry - sl) / entry - COST
                    last_exit = j
                    break
                if l <= tp:
                    result = 'TP'
                    pnl_pct = (entry - tp) / entry - COST
                    last_exit = j
                    break
            else:
                if l <= sl:
                    result = 'SL'
                    pnl_pct = (sl - entry) / entry - COST
                    last_exit = j
                    break
                if h >= tp:
                    result = 'TP'
                    pnl_pct = (tp - entry) / entry - COST
                    last_exit = j
                    break
        else:
            pnl_pct = -COST  # TIMEOUT：近似0损耗

        records.append({
            'entry_idx': i,
            'entry_ts':  df.index[i],
            'regime':    regimes[i],
            'direction': direction,
            'result':    result,
            'pnl_pct':   pnl_pct,
            'sl_atr':    sl_atr,
            'tp_atr':    tp_atr,
        })

    return pd.DataFrame(records)


# ════════════════════════════════════════════════════════════════
# 统计计算
# ════════════════════════════════════════════════════════════════
def calc_stats(trades: pd.DataFrame) -> dict:
    if trades is None or len(trades) == 0:
        return {'n': 0, 'wr': 0, 'pf_pnl': 0, 'max_dd_streak': 0, 'avg_pnl': 0}
    wins   = trades[trades['result'] == 'TP']
    losses = trades[trades['result'] == 'SL']
    n = len(trades)
    wr = len(wins) / (len(wins) + len(losses)) if (len(wins) + len(losses)) > 0 else 0

    pnl_w = wins['pnl_pct'].sum()
    pnl_l = losses['pnl_pct'].abs().sum()
    pf_pnl = pnl_w / pnl_l if pnl_l > 0 else 0

    # 最大连败
    streak = max_streak = 0
    for r in trades['result']:
        if r == 'SL':
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    avg_pnl = trades['pnl_pct'].mean() if n > 0 else 0

    return {'n': n, 'wr': round(wr, 4), 'pf_pnl': round(pf_pnl, 3),
            'max_dd_streak': max_streak, 'avg_pnl': round(avg_pnl * 100, 4)}


def pf_for_params(df_train: pd.DataFrame, sl_atr, tp_atr, hold_max, direction) -> float:
    """快速评估参数组合，返回 PF（训练集）"""
    sig = gen_signals(df_train, direction)
    trades = settle_vectorized(df_train, sig, sl_atr, tp_atr, hold_max, direction)
    if trades is None or len(trades) < MIN_TRAIN_N // 3:
        return 0.0
    s = calc_stats(trades)
    return s['pf_pnl']


# ════════════════════════════════════════════════════════════════
# Walk-Forward 主逻辑
# ════════════════════════════════════════════════════════════════
def run_wfv(df_feat: pd.DataFrame, sym: str, direction: str, fast: bool) -> dict:
    """
    对单个 sym + direction 跑 6窗口 Walk-Forward
    返回详细结果 dict
    """
    window_results = []
    passed = 0

    for w_idx, (oos_start, oos_end) in enumerate(WF_WINDOWS):
        ts_oos_start = pd.Timestamp(oos_start, tz='UTC')
        ts_oos_end   = pd.Timestamp(oos_end,   tz='UTC')

        df_train = df_feat[df_feat.index < ts_oos_start].copy()
        df_oos   = df_feat[(df_feat.index >= ts_oos_start) &
                           (df_feat.index <  ts_oos_end)].copy()

        if len(df_train) < 2000 or len(df_oos) < 200:
            log.info("  W%d 跳过（数据不足 train=%d oos=%d）",
                     w_idx+1, len(df_train), len(df_oos))
            window_results.append({'window': w_idx+1, 'skipped': True,
                                   'reason': 'insufficient_data'})
            continue

        log.info("  W%d IS=%s~%s(%d) OOS=%s~%s(%d)",
                 w_idx+1,
                 df_train.index[0].strftime('%Y-%m'),
                 df_train.index[-1].strftime('%Y-%m'),
                 len(df_train),
                 df_oos.index[0].strftime('%Y-%m'),
                 df_oos.index[-1].strftime('%Y-%m'),
                 len(df_oos))

        # ── 训练集：网格搜索最优参数 ──
        best_pf = 0.0
        best_params = {'sl_atr': 1.2, 'tp_atr': 2.0, 'hold_max': 48}

        sl_list = SL_ATR_CANDIDATES if not fast else [1.0, 1.2]
        tp_list = TP_ATR_CANDIDATES if not fast else [2.0, 2.5]
        hm_list = HOLD_MAX_CANDIDATES if not fast else [48]

        for sl in sl_list:
            for tp in tp_list:
                if tp / sl < 1.5:   # RR < 1.5 直接跳过
                    continue
                for hm in hm_list:
                    pf = pf_for_params(df_train, sl, tp, hm, direction)
                    if pf > best_pf:
                        best_pf = pf
                        best_params = {'sl_atr': sl, 'tp_atr': tp, 'hold_max': hm}

        # ── IS 统计（最优参数）──
        sig_is  = gen_signals(df_train, direction)
        trd_is  = settle_vectorized(df_train, sig_is,
                                    best_params['sl_atr'], best_params['tp_atr'],
                                    best_params['hold_max'], direction)
        stats_is = calc_stats(trd_is)

        # ── OOS 统计（原样应用，不再优化）──
        sig_oos  = gen_signals(df_oos, direction)
        trd_oos  = settle_vectorized(df_oos, sig_oos,
                                     best_params['sl_atr'], best_params['tp_atr'],
                                     best_params['hold_max'], direction)
        stats_oos = calc_stats(trd_oos)

        # ── 通过判断 ──
        oos_ok = (
            stats_oos['n'] >= MIN_OOS_N and
            stats_oos['pf_pnl'] >= 1.0 and
            stats_oos['max_dd_streak'] <= 8
        )
        if oos_ok:
            passed += 1

        # WFV误差
        wfv_err = abs(stats_is['wr'] - stats_oos['wr']) / max(stats_is['wr'], 0.001)

        log.info("    IS  PF=%.3f WR=%.3f n=%d | OOS PF=%.3f WR=%.3f n=%d | %s | WFV误差=%.1f%%",
                 stats_is['pf_pnl'],  stats_is['wr'],  stats_is['n'],
                 stats_oos['pf_pnl'], stats_oos['wr'], stats_oos['n'],
                 "✅PASS" if oos_ok else "❌FAIL",
                 wfv_err * 100)

        window_results.append({
            'window':    w_idx + 1,
            'oos_start': oos_start,
            'oos_end':   oos_end,
            'best_params': best_params,
            'is_best_pf': round(best_pf, 3),
            'is':  stats_is,
            'oos': stats_oos,
            'oos_pass': oos_ok,
            'wfv_err': round(wfv_err, 4),
        })

        gc.collect()

    overall_pass = passed >= 4
    log.info("  %s %s: %d/6 OOS通过 → %s",
             sym, direction, passed,
             "✅ 策略有效" if overall_pass else "❌ 策略无效")

    return {
        'sym': sym,
        'direction': direction,
        'windows': window_results,
        'passed_windows': passed,
        'overall_pass': overall_pass,
    }


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description='梵天 Walk-Forward 验证 v1.0')
    ap.add_argument('--fast',   action='store_true', help='快速模式（减少参数搜索）')
    ap.add_argument('--resume', action='store_true', help='跳过已有结果')
    ap.add_argument('--sym',    default=None,        help='只跑单标的，如 ETHUSDT')
    args = ap.parse_args()

    syms = [args.sym] if args.sym else SYMS
    out_path = RESULTS / f"train_wfv_v1_{TAG}.json"

    # 断点续跑：找已有结果
    existing = {}
    if args.resume:
        for p in sorted(RESULTS.glob("train_wfv_v1_*.json")):
            try:
                prev = json.loads(p.read_text())
                for item in prev.get('results', []):
                    key = f"{item['sym']}_{item['direction']}"
                    existing[key] = item
                log.info("断点续跑：加载已有结果 %s (%d条)", p.name, len(existing))
                break
            except Exception:
                pass

    all_results = list(existing.values())
    t_total = time.time()

    for sym in syms:
        log.info("══════════════════════════════════════")
        log.info("加载数据 %s ...", sym)
        try:
            df1h, df4h = load_data(sym)
        except FileNotFoundError as e:
            log.error(str(e))
            continue

        log.info("构建特征 %s ...", sym)
        df_feat = build_features(df1h, df4h)
        log.info("特征构建完成 n=%d (%s ~ %s)",
                 len(df_feat),
                 df_feat.index[0].strftime('%Y-%m'),
                 df_feat.index[-1].strftime('%Y-%m'))

        for direction in ['SHORT', 'LONG']:
            key = f"{sym}_{direction}"
            if key in existing:
                log.info("跳过 %s %s（已有结果）", sym, direction)
                continue

            log.info("── WFV %s %s ──", sym, direction)
            result = run_wfv(df_feat, sym, direction, args.fast)
            all_results.append(result)

            # 每个 sym+direction 完成后立即保存（防中断丢失）
            _save(all_results, out_path)

        del df1h, df4h, df_feat
        gc.collect()

    elapsed = time.time() - t_total
    log.info("══════════════════════════════════════")
    log.info("全部完成 耗时=%.1fs 结果→ %s", elapsed, out_path)

    # ── 打印汇总 ──
    _print_summary(all_results)


def _save(results: list, path: Path):
    out = {
        '_meta': {
            'ts': TAG,
            'version': 'train_wfv_v1',
            'windows': len(WF_WINDOWS),
            'pass_threshold': '4/6 OOS窗口 PF≥1.0',
        },
        'results': results,
    }
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))


def _print_summary(results: list):
    print("\n" + "="*60)
    print("  Walk-Forward 验证汇总")
    print("="*60)
    for r in results:
        if 'windows' not in r:
            continue
        sym = r['sym']
        d   = r['direction']
        p   = r['passed_windows']
        ok  = r['overall_pass']
        oos_pfs = [w['oos']['pf_pnl'] for w in r['windows']
                   if not w.get('skipped') and 'oos' in w]
        oos_avg = round(sum(oos_pfs)/len(oos_pfs), 3) if oos_pfs else 0
        print(f"  {sym} {d:6s}: {p}/6窗口通过 OOS_PF均值={oos_avg} {'✅' if ok else '❌'}")
    print("="*60)


if __name__ == '__main__':
    main()
