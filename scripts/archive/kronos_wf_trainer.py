#!/usr/bin/env python3
"""
kronos_wf_trainer.py — 达摩院 Walk-Forward 训练数据生成器 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 达摩院 · 2026-06-25 | 苏摩111批准

核心思路：
  在历史K线上以步进窗口运行梵天体制判断 + Kronos特征提取
  向前验证价格结果(WIN/LOSS)
  生成与当前梵天v25+完全兼容的训练样本

严格前视隔离：
  信号生成：只用截至信号时刻的K线（无前视）
  结果判断：只用信号时刻之后的K线（forward-looking）
  训练/验证分割：时间轴严格分割

苏摩合规：
  - nice+19 低优先级运行
  - 有持仓时仍可运行（只读K线，不下单）
  - 结果写入 data/kronos_wf_samples.jsonl（追加模式）
  - 不产生任何AI cron任务

用法：
  python3 scripts/kronos_wf_trainer.py --symbol BTCUSDT --fast
  python3 scripts/kronos_wf_trainer.py --symbol BTCUSDT ETHUSDT
  python3 scripts/kronos_wf_trainer.py --train  # 训练模型
"""

import os, sys, json, time, argparse, math
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta

# nice+19 降优先级
try:
    os.nice(19)
except:
    pass

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

DATA_DIR   = BASE / 'data'
WF_SAMPLES = DATA_DIR / 'kronos_wf_samples.jsonl'
WF_MODEL   = DATA_DIR / 'kronos_wf_model.json'
LOG_FILE   = BASE / 'logs' / 'kronos_wf_trainer.log'

LOG_FILE.parent.mkdir(exist_ok=True)

def log(msg):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


# ── 工具：分页获取历史K线 ────────────────────────────────────────
def fetch_klines_paginated(symbol: str, interval: str,
                            start_ts: int, end_ts: int) -> list:
    """
    分页获取历史K线，返回 [open,high,low,close,volume] 列表
    start_ts / end_ts: 毫秒时间戳
    """
    import urllib.request
    FAPI = 'https://fapi.binance.com'
    all_klines = []
    cur = start_ts
    while cur < end_ts:
        url = (f'{FAPI}/fapi/v1/klines?symbol={symbol}'
               f'&interval={interval}&startTime={cur}&limit=1500')
        try:
            raw = json.loads(urllib.request.urlopen(url, timeout=15).read())
        except Exception as e:
            log(f'  ⚠️ 请求失败: {e}, 重试...')
            time.sleep(3)
            continue
        if not raw:
            break
        for c in raw:
            all_klines.append([
                float(c[1]),  # open
                float(c[2]),  # high
                float(c[3]),  # low
                float(c[4]),  # close
                float(c[5]),  # volume
                int(c[0]),    # open_time ms
            ])
        last_ts = int(raw[-1][6])  # close_time
        cur = last_ts + 1
        if len(raw) < 1500:
            break
        time.sleep(0.2)  # 限速
    return all_klines


# ── 核心：在历史窗口计算体制 + Kronos特征 ───────────────────────
def compute_regime_simple(klines_4h: list, klines_1h: list) -> str:
    """
    轻量体制判断（不调用完整brahma_core，避免重复API请求）
    基于 EMA20/50 + 价格结构
    """
    if len(klines_4h) < 50:
        return 'UNKNOWN'

    arr = np.array(klines_4h[-100:], dtype=float)
    close = arr[:, 3]
    high  = arr[:, 1]
    low   = arr[:, 2]

    def ema(c, n):
        k = 2.0 / (n + 1)
        e = np.zeros(len(c))
        e[0] = c[0]
        for i in range(1, len(c)):
            e[i] = c[i] * k + e[i-1] * (1 - k)
        return e

    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    cur   = close[-1]

    # 斜率（10根）
    slope20 = (ema20[-1] - ema20[-10]) / (ema20[-10] + 1e-10)
    slope50 = (ema50[-1] - ema50[-10]) / (ema50[-10] + 1e-10)

    # 最近20根高低点
    recent_high = high[-20:].max()
    recent_low  = low[-20:].min()
    prev_high   = high[-40:-20].max() if len(high) >= 40 else high[-20:].max()
    prev_low    = low[-40:-20].min()  if len(low)  >= 40 else low[-20:].min()

    # 判断逻辑
    price_above_ema20 = cur > ema20[-1]
    price_above_ema50 = cur > ema50[-1]
    ema20_above_ema50 = ema20[-1] > ema50[-1]
    higher_highs = recent_high > prev_high
    lower_lows   = recent_low  < prev_low

    if slope20 < -0.005 and slope50 < -0.003:
        if not price_above_ema20 and not price_above_ema50:
            if lower_lows and not higher_highs:
                return 'BEAR_TREND'
            return 'BEAR_EARLY'
        if not price_above_ema50:
            return 'BEAR_EARLY'
        return 'BEAR_RECOVERY'

    if slope20 > 0.005 and slope50 > 0.003:
        if price_above_ema20 and price_above_ema50:
            if higher_highs and not lower_lows:
                return 'BULL_TREND'
            return 'BULL_EARLY'
        if price_above_ema20:
            return 'BULL_EARLY'
        return 'BULL_CORRECTION'

    # 震荡
    atr = np.mean(np.maximum(
        arr[-20:, 1] - arr[-20:, 2],
        np.abs(arr[-20:, 1] - arr[-21:-1, 3]),
    ) if len(arr) >= 21 else arr[-20:, 1] - arr[-20:, 2])
    atr_pct = atr / (cur + 1e-10)
    if atr_pct > 0.025:
        return 'CHOP_HIGH'
    if atr_pct > 0.012:
        return 'CHOP_MID'
    return 'CHOP_LOW'


def compute_kronos_features(klines_15m: list, regime: str) -> dict:
    """调用 Kronos-Lite v2.0 提取特征"""
    try:
        from kronos_lite import _compute_p_up
        p_up, debug = _compute_p_up(klines_15m, regime=regime, tf_hint='15m')
        return {
            'p_up':        round(p_up, 4),
            'p_momentum':  debug.get('p_momentum', 0.5),
            'p_ema':       debug.get('p_ema', 0.5),
            'p_rsi':       debug.get('p_rsi', 0.5),
            'p_candle':    debug.get('p_candle', 0.5),
            'p_volume':    debug.get('p_volume', 0.5),
            'p_bos':       debug.get('p_bos', 0.5),
            'rsi_cur':     debug.get('rsi_cur', 50.0),
        }
    except Exception as e:
        return {'p_up': 0.5, 'error': str(e)[:40]}


def simulate_result(klines_4h_future: list, entry_price: float,
                    direction: str, sl_pct: float = 0.025,
                    tp_pct: float = 0.04) -> str:
    """
    向前验证：给定入场价和方向，判断未来是否触及TP/SL
    klines_4h_future: 入场后的K线
    """
    if not klines_4h_future or entry_price <= 0:
        return 'UNKNOWN'

    sl = entry_price * (1 - sl_pct) if direction == 'LONG' else entry_price * (1 + sl_pct)
    tp = entry_price * (1 + tp_pct) if direction == 'LONG' else entry_price * (1 - tp_pct)

    for candle in klines_4h_future[:12]:   # 最多看12根4H = 48H
        high  = candle[1]
        low   = candle[2]
        if direction == 'LONG':
            if low  <= sl: return 'LOSS'
            if high >= tp: return 'WIN'
        else:
            if high >= sl: return 'LOSS'
            if low  <= tp: return 'WIN'
    return 'TIMEOUT'


# ── 主流程：Walk-Forward 信号生成 ────────────────────────────────
def run_walk_forward(symbol: str, lookback_days: int = 365,
                     step_4h: int = 2, fast: bool = False) -> list:
    """
    对单个标的运行Walk-Forward，生成训练样本

    symbol:        交易对
    lookback_days: 回溯天数
    step_4h:       步进（每隔多少根4H窗口生成一个信号）
    fast:          快速模式（步进×4，仅BTC主要信号）
    """
    log(f'=== {symbol} Walk-Forward 开始 (lookback={lookback_days}d) ===')

    if fast:
        step_4h = max(step_4h, 8)

    now_ms    = int(time.time() * 1000)
    start_ms  = now_ms - lookback_days * 24 * 3600 * 1000

    # 1. 获取完整历史K线
    log(f'  获取 {symbol} 4H K线...')
    kl_4h_all = fetch_klines_paginated(symbol, '4h', start_ms, now_ms)
    log(f'  获取 {symbol} 1H K线...')
    kl_1h_all = fetch_klines_paginated(symbol, '1h', start_ms, now_ms)
    log(f'  获取 {symbol} 15M K线...')
    # 15M取最近200天（更快）
    start_15m = now_ms - min(lookback_days, 180) * 24 * 3600 * 1000
    kl_15m_all = fetch_klines_paginated(symbol, '15m', start_15m, now_ms)

    log(f'  4H={len(kl_4h_all)}根  1H={len(kl_1h_all)}根  15M={len(kl_15m_all)}根')

    if len(kl_4h_all) < 100:
        log(f'  ⚠️ 4H数据不足，跳过')
        return []

    samples = []
    n_windows = len(kl_4h_all)
    n_steps   = (n_windows - 200) // step_4h   # 保留200根作为初始窗口

    log(f'  步进={step_4h}  预计窗口数={n_steps}')

    # 训练/验证时间分割（前80%训练，后20%验证）
    split_idx = int(n_windows * 0.8)

    generated = 0
    skipped   = 0

    for step in range(n_steps):
        idx_4h = 200 + step * step_4h   # 当前4H窗口末尾索引

        if idx_4h + 12 >= n_windows:    # 需要12根未来K线验证
            break

        # 当前时间戳
        cur_ts_ms = kl_4h_all[idx_4h][5]

        # 截至当前的K线（无前视）
        hist_4h  = kl_4h_all[:idx_4h + 1]
        future_4h = kl_4h_all[idx_4h + 1: idx_4h + 13]  # 未来12根4H

        # 对应1H K线（按时间戳对齐）
        hist_1h = [c for c in kl_1h_all if c[5] <= cur_ts_ms][-200:]

        # 对应15M K线
        hist_15m = [c for c in kl_15m_all if c[5] <= cur_ts_ms][-200:]

        if len(hist_1h) < 50 or len(hist_15m) < 60:
            skipped += 1
            continue

        # 当前价格
        cur_price = hist_4h[-1][3]  # close

        # 计算体制
        regime = compute_regime_simple(hist_4h, hist_1h)

        # 只保留有价值的体制（过滤CHOP和UNKNOWN）
        if regime in ('CHOP_HIGH', 'UNKNOWN'):
            skipped += 1
            continue

        # 计算Kronos特征
        kf = compute_kronos_features(hist_15m, regime)

        # 对LONG和SHORT各生成一个样本
        for direction in ['LONG', 'SHORT']:
            # 模拟入场价（以当前收盘价为基准）
            if direction == 'LONG':
                entry = cur_price * 0.998   # 略低于市价入场
            else:
                entry = cur_price * 1.002   # 略高于市价入场

            # 向前验证结果
            result = simulate_result(future_4h, entry, direction)
            if result == 'TIMEOUT':
                continue   # 跳过超时，只保留明确WIN/LOSS

            # 时间段标记
            split = 'train' if idx_4h < split_idx else 'valid'

            sample = {
                'symbol':      symbol,
                'direction':   direction,
                'regime':      regime,
                'result':      result,
                'split':       split,
                'cur_price':   round(cur_price, 4),
                'entry':       round(entry, 4),
                'ts_ms':       cur_ts_ms,
                'ts_str':      datetime.fromtimestamp(cur_ts_ms/1000,
                                tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
                # Kronos特征
                'p_up':        kf.get('p_up', 0.5),
                'p_momentum':  kf.get('p_momentum', 0.5),
                'p_ema':       kf.get('p_ema', 0.5),
                'p_rsi':       kf.get('p_rsi', 0.5),
                'p_candle':    kf.get('p_candle', 0.5),
                'p_volume':    kf.get('p_volume', 0.5),
                'p_bos':       kf.get('p_bos', 0.5),
                'rsi_cur':     kf.get('rsi_cur', 50.0),
                # 额外特征
                'n_4h_idx':    idx_4h,
                'data_source': 'walk_forward_v1',
            }
            samples.append(sample)
            generated += 1

        if generated % 50 == 0 and generated > 0:
            log(f'  进度: {step+1}/{n_steps}  生成={generated}  跳过={skipped}')

    log(f'  {symbol} 完成: 生成={generated}  跳过={skipped}')

    # 统计WR
    if samples:
        wins  = sum(1 for s in samples if s['result'] == 'WIN')
        total = len(samples)
        by_regime = {}
        for s in samples:
            k = f"{s['regime']}_{s['direction']}"
            by_regime.setdefault(k, {'win': 0, 'total': 0})
            by_regime[k]['total'] += 1
            if s['result'] == 'WIN':
                by_regime[k]['win'] += 1
        log(f'  总WR={wins/total*100:.1f}%  ({wins}/{total})')
        log('  体制WR:')
        for k, v in sorted(by_regime.items()):
            n = v['total']
            if n >= 5:
                wr = v['win'] / n * 100
                flag = '✅' if wr >= 55 else ('🔴' if wr < 45 else '🟡')
                log(f'    {flag} {k:<35} WR={wr:.1f}% n={n}')

    return samples


# ── 模型训练（在WF样本上训练LightGBM）───────────────────────────
def train_on_wf_samples(samples: list = None):
    """在Walk-Forward样本上训练模型"""
    log('\n=== 模型训练阶段 ===')

    if samples is None:
        if not WF_SAMPLES.exists():
            log('⚠️ 无WF样本文件，请先运行生成阶段')
            return
        samples = [json.loads(l) for l in WF_SAMPLES.read_text().strip().split('\n') if l.strip()]

    log(f'样本总数: {len(samples)}')

    # 分割训练/验证
    train_s = [s for s in samples if s.get('split') == 'train']
    valid_s = [s for s in samples if s.get('split') == 'valid']
    log(f'训练集: {len(train_s)}  验证集: {len(valid_s)}')

    if len(train_s) < 50:
        log('⚠️ 训练样本不足50条，跳过训练')
        return

    # 构建特征矩阵
    REGIME_ENC = {
        'BEAR_TREND': 0, 'BEAR_EARLY': 1, 'BEAR_RECOVERY': 2,
        'BULL_TREND': 3, 'BULL_EARLY': 4, 'BULL_CORRECTION': 5,
        'CHOP_MID': 6, 'CHOP_LOW': 7, 'CHOP_HIGH': 8,
    }

    def build_X(slist):
        X, y = [], []
        for s in slist:
            if s.get('result') not in ('WIN', 'LOSS'):
                continue
            regime_enc = REGIME_ENC.get(s.get('regime', ''), -1)
            if regime_enc < 0:
                continue
            dir_enc = 1 if s.get('direction') == 'LONG' else 0
            feat = [
                float(s.get('p_momentum', 0.5)),
                float(s.get('p_ema', 0.5)),
                float(s.get('p_rsi', 0.5)),
                float(s.get('p_candle', 0.5)),
                float(s.get('p_volume', 0.5)),
                float(s.get('p_bos', 0.5)),
                float(s.get('p_up', 0.5)),
                regime_enc / 8.0,
                dir_enc,
                float(s.get('rsi_cur', 50)) / 100.0,
            ]
            X.append(feat)
            y.append(1 if s['result'] == 'WIN' else 0)
        return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)

    X_tr, y_tr = build_X(train_s)
    X_va, y_va = build_X(valid_s)
    log(f'特征矩阵: train={X_tr.shape}  valid={X_va.shape}')
    log(f'训练集WR: {y_tr.mean()*100:.1f}%  验证集WR: {y_va.mean()*100:.1f}%')

    # 尝试LightGBM，回退SimpleGBM
    try:
        import lightgbm as lgb
        log('使用 LightGBM 原生库...')
        dtrain = lgb.Dataset(X_tr, label=y_tr)
        dvalid = lgb.Dataset(X_va, label=y_va)
        params = {
            'objective': 'binary', 'metric': 'binary_error',
            'learning_rate': 0.03, 'num_leaves': 15,
            'min_data_in_leaf': 10, 'verbose': -1,
            'feature_fraction': 0.8, 'bagging_fraction': 0.8,
        }
        model_lgb = lgb.train(
            params, dtrain, num_boost_round=300,
            valid_sets=[dvalid],
            callbacks=[lgb.early_stopping(30, verbose=False),
                       lgb.log_evaluation(50)],
        )
        va_preds = (model_lgb.predict(X_va) > 0.5).astype(int)
        oos_acc  = (va_preds == y_va).mean()
        log(f'LightGBM OOS准确率: {oos_acc*100:.1f}%')
        model_lgb.save_model(str(WF_MODEL).replace('.json', '_lgb.txt'))
        log(f'✅ LightGBM模型保存')

    except ImportError:
        log('LightGBM未安装，使用内置SimpleGBM...')
        sys.path.insert(0, str(BASE / 'scripts'))
        from kronos_lightgbm_trainer import SimpleGBMTrainer

        model = SimpleGBMTrainer(n_estimators=150, learning_rate=0.05, max_depth=3)
        model.fit(X_tr, y_tr)

        va_preds = model.predict(X_va)
        va_proba = model.predict_proba(X_va)
        oos_acc  = (va_preds == y_va).mean() if len(y_va) > 0 else 0
        strong   = (va_proba > 0.62) | (va_proba < 0.38)
        s_acc    = (va_preds[strong] == y_va[strong]).mean() if strong.sum() > 0 else 0

        log(f'OOS总体准确率:   {oos_acc*100:.1f}%  (n={len(y_va)})')
        log(f'OOS强信号准确率: {s_acc*100:.1f}%  (n={strong.sum()})')
        log(f'基准(Kronos-Lite v1.0): 47.7%')
        log(f'改进: {(oos_acc*100 - 47.7):+.1f}pp')

        fi = model.feature_importance()
        feat_names = ['p_momentum','p_ema','p_rsi','p_candle','p_volume',
                      'p_bos','p_up','regime','direction','rsi_norm']
        log('特征重要性 TOP5:')
        ranked = sorted(zip(feat_names, [fi.get(f, 0) for f in feat_names]),
                        key=lambda x: -x[1])
        for fname, imp in ranked[:5]:
            log(f'  {fname:<15} {imp:.4f}  {"█"*int(imp*40)}')

        # 里程碑判断
        if oos_acc >= 0.60:
            log('🏆 达到M2里程碑(≥60%)！可准备s23升权评估')
        elif oos_acc >= 0.55:
            log('✅ 达到M1里程碑(≥55%)！方向验证通过')
        elif oos_acc >= 0.52:
            log('🟡 接近M1(≥52%)，继续优化')
        else:
            log('⚠️ 未达里程碑，检查数据质量')

        md = model.to_dict()
        md['oos_acc'] = float(oos_acc)
        md['trained_at'] = datetime.now(timezone.utc).isoformat()
        md['n_train'] = len(y_tr)
        md['n_valid'] = len(y_va)
        md['data_source'] = 'walk_forward_v1'
        WF_MODEL.write_text(json.dumps(md, ensure_ascii=False), encoding='utf-8')
        log(f'✅ 模型保存: {WF_MODEL}')


# ── 入口 ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Kronos Walk-Forward 训练器')
    parser.add_argument('--symbol', nargs='+', default=['BTCUSDT', 'ETHUSDT'])
    parser.add_argument('--days',   type=int,  default=365)
    parser.add_argument('--step',   type=int,  default=3,
                        help='4H步进（越小越密集，越慢）')
    parser.add_argument('--fast',   action='store_true',
                        help='快速模式：步进×4，适合快速验证')
    parser.add_argument('--train',  action='store_true',
                        help='仅训练模型（跳过数据生成）')
    parser.add_argument('--append', action='store_true',
                        help='追加到已有样本文件（默认覆盖）')
    args = parser.parse_args()

    t_start = time.time()
    log(f'=== Kronos Walk-Forward 训练器 v1.0 ===')
    log(f'标的: {args.symbol}  天数: {args.days}  步进: {args.step}  快速: {args.fast}')

    all_samples = []

    if not args.train:
        # 数据生成阶段
        for sym in args.symbol:
            sym_samples = run_walk_forward(
                sym,
                lookback_days=args.days,
                step_4h=args.step,
                fast=args.fast,
            )
            all_samples.extend(sym_samples)
            time.sleep(1)  # 标的间间隔

        # 写入样本文件
        mode = 'a' if args.append else 'w'
        with open(WF_SAMPLES, mode) as f:
            for s in all_samples:
                f.write(json.dumps(s, ensure_ascii=False) + '\n')
        log(f'\n✅ 样本写入: {WF_SAMPLES}  总计={len(all_samples)}条')

        # 统计
        wins  = sum(1 for s in all_samples if s['result'] == 'WIN')
        total = len(all_samples)
        if total > 0:
            log(f'总WR: {wins/total*100:.1f}%  WIN={wins}  LOSS={total-wins}')

    # 训练阶段
    if all_samples or args.train:
        train_on_wf_samples(all_samples if all_samples else None)

    elapsed = time.time() - t_start
    log(f'\n=== 完成 | 耗时 {elapsed/60:.1f}分钟 ===')
