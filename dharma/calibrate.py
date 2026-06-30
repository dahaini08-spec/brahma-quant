#!/usr/bin/env python3
"""
达摩院 v5.1 · calibrate.py
══════════════════════════════════════════════════════════════════
功能：根据回测结果生成梵天大脑参数校准建议
      - 指标权重调整（WR+PF综合评分）
      - RSI阈值调整（按时间框架）
      - 持有期建议（最优K线根数）
      - 高质量组合发现

调用方：dharma/elite_calibrator.py（cron每24h）
输入：dharma/results/signal_backtest_v5_*.json
输出：dharma/results/calibration_v5_YYYYMMDD_HHMMSS.json

历史：
  v5.0 - 初版（达摩院V5回测结果校准）
  v5.1 - 2026-05-20 设计院补强：文档/版本/日志/异常处理
"""
import json
import glob
import logging
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

# ── 日志配置 ──────────────────────────────────────────────────────
log = logging.getLogger('dharma.calibrate')
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter('[Calibrate] %(asctime)s %(levelname)s %(message)s',
                                       datefmt='%H:%M:%S'))
    log.addHandler(_h)
log.setLevel(logging.INFO)

VERSION = 'v5.1'
RESULTS_DIR = Path(__file__).parent / 'results'

# 梵天大脑当前参数（参考brahma_brain引擎）
CURRENT_BRAHMA_PARAMS = {
    "indicator_weights": {
        "MACD背离": 0.20,
        "RSI超卖超买": 0.15,
        "布林带反弹": 0.15,
        "EMA趋势顺势": 0.20,
        "量价配合": 0.10,
        "MACD金叉死叉": 0.10,
        "MACD零轴位置": 0.10,
    },
    "rsi_thresholds": {
        "oversold": 30,
        "overbought": 70,
        "BULL_TREND": {"oversold": 40, "overbought": 75},
        "BEAR_TREND": {"oversold": 25, "overbought": 60},
    },
    "regime_multipliers": {
        "BULL_TREND": 1.2,
        "BEAR_TREND": 1.2,
        "BULL_PEAK": 0.8,
        "BEAR_CRASH": 0.8,
        "RECOVERY": 1.0,
        "CHOP": 0.6,
        "CHOP_HIGH": 0.7,
        "CHOP_LOW": 0.7,
    },
    "min_signal_score": 0.6,
    "holding_period_default": 12,
}


def find_latest_result(pattern: str) -> Path | None:
    """按glob模式查找最新回测结果文件，无结果返回None"""
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(RESULTS_DIR.glob(pattern))
        return files[-1] if files else None
    except Exception as e:
        log.warning(f'find_latest_result({pattern}) 失败: {e}')
        return None


def load_json(path: Path) -> dict:
    """安全加载JSON文件，失败返回空dict"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log.error(f'load_json({path}) 失败: {e}')
        return {}


def calibrate() -> tuple | None:
    """
    主校准函数：载入回测结果并生成参数校准建议。
    返回 (calibration_dict, output_path_str)，失败返回 None。
    """
    log.info(f'达摩院校准器 {VERSION} 启动')

    # 加载最新回测结果
    signal_file = find_latest_result('signal_backtest_v5_*.json')
    combo_file  = find_latest_result('combo_test_v5_*.json')

    if not signal_file:
        log.error('未找到signal_backtest结果，请先运行signal_backtest.py')
        return None

    log.info(f'加载回测文件: {signal_file.name}')
    signal_data = load_json(signal_file)
    combo_data  = load_json(combo_file) if combo_file else {}

    calibration = {
        "generated_at": datetime.now().isoformat(),
        "based_on": {
            "signal_backtest": str(signal_file),
            "combo_test": str(combo_file) if combo_file else None,
        },
        "weight_adjustments": {},
        "rsi_threshold_adjustments": {},
        "regime_multiplier_adjustments": {},
        "holding_period_recommendations": {},
        "summary": [],
        "new_params": {},
    }

    # 1. 权重调整建议
    log.info("=== 权重校准 ===")
    indicator_wrs = {}
    for ind, data in signal_data.items():
        wr = data.get('overall', {}).get('wr', 0)
        pf = data.get('overall', {}).get('pf', 0)
        n = data.get('overall', {}).get('n', 0)
        indicator_wrs[ind] = {'wr': wr, 'pf': pf, 'n': n}

    # 归一化：以WR和PF综合打分
    scores = {}
    for ind, stats in indicator_wrs.items():
        if stats['n'] > 50:
            score = stats['wr'] * 0.6 + min(stats['pf'] / 3.0, 1.0) * 0.4
        else:
            score = 0.3  # 样本不足，保守
        scores[ind] = score

    total_score = sum(scores.values()) or 1.0
    new_weights = {ind: round(s / total_score, 4) for ind, s in scores.items()}

    # 确保权重和为1
    w_sum = sum(new_weights.values())
    # 找最大权重指标做调整
    max_ind = max(new_weights, key=new_weights.get)
    new_weights[max_ind] = round(new_weights[max_ind] + (1.0 - w_sum), 4)

    for ind in CURRENT_BRAHMA_PARAMS['indicator_weights']:
        old_w = CURRENT_BRAHMA_PARAMS['indicator_weights'].get(ind, 0)
        new_w = new_weights.get(ind, old_w)
        delta = new_w - old_w
        action = 'RAISE' if delta > 0.02 else 'LOWER' if delta < -0.02 else 'KEEP'
        calibration['weight_adjustments'][ind] = {
            'current': old_w,
            'recommended': new_w,
            'delta': round(delta, 4),
            'action': action,
            'basis_wr': indicator_wrs.get(ind, {}).get('wr', 0),
            'basis_pf': indicator_wrs.get(ind, {}).get('pf', 0),
        }
        log.info(f"  {ind}: {old_w:.2f} → {new_w:.2f} [{action}] (WR={indicator_wrs.get(ind,{}).get('wr',0):.1%})")

    # 2. RSI阈值调整建议
    log.info("=== RSI阈值校准 ===")
    rsi_data = signal_data.get('RSI超卖超买', {})
    by_tf = rsi_data.get('by_timeframe', {})
    by_regime_raw = rsi_data.get('by_regime', {})

    rsi_adj = {}
    # 如果1h RSI WR低，建议更严格阈值
    rsi_1h_wr = by_tf.get('1h', {}).get('wr', 0.5)
    if rsi_1h_wr < 0.52:
        rsi_adj['1h_oversold'] = {'current': 30, 'recommended': 25, 'reason': f'1h WR仅{rsi_1h_wr:.1%}，建议收紧至25'}
        log.info(f"  1h RSI超卖阈值建议调整: 30 → 25")
    else:
        rsi_adj['1h_oversold'] = {'current': 30, 'recommended': 30, 'reason': f'1h WR={rsi_1h_wr:.1%}，保持'}

    rsi_1d_wr = by_tf.get('1d', {}).get('wr', 0.5)
    if rsi_1d_wr > 0.58:
        rsi_adj['1d_oversold'] = {'current': 30, 'recommended': 35, 'reason': f'1d WR={rsi_1d_wr:.1%}表现好，可放宽至35'}
        log.info(f"  1d RSI超卖阈值建议放宽: 30 → 35")
    else:
        rsi_adj['1d_oversold'] = {'current': 30, 'recommended': 30, 'reason': f'1d WR={rsi_1d_wr:.1%}，保持'}

    calibration['rsi_threshold_adjustments'] = rsi_adj

    # 3. 持有期建议
    log.info("=== 持有期建议 ===")
    holding_recs = {}
    for ind, data in signal_data.items():
        best_h = data.get('best_holding', {})
        all_h = data.get('all_holdings', {})
        if best_h.get('n_bars'):
            holding_recs[ind] = {
                'current_default': CURRENT_BRAHMA_PARAMS['holding_period_default'],
                'recommended': best_h['n_bars'],
                'best_wr': best_h['wr'],
                'all_periods': {str(k): v.get('wr', 0) for k, v in all_h.items()},
            }
            flag = '⚠️' if best_h['n_bars'] != CURRENT_BRAHMA_PARAMS['holding_period_default'] else '✅'
            log.info(f"  {ind}: 最佳持有={best_h['n_bars']}根 WR={best_h['wr']:.1%} {flag}")
    calibration['holding_period_recommendations'] = holding_recs

    # 4. 高质量组合建议
    good_combos = combo_data.get('高质量组合_WR65_PF1.5_N100', {})
    calibration['high_quality_combos'] = good_combos

    # 5. 综合总结
    top_indicators = sorted(indicator_wrs.items(), key=lambda x: x[1]['wr'], reverse=True)[:3]
    bottom_indicators = sorted(indicator_wrs.items(), key=lambda x: x[1]['wr'])[:2]

    summary = []
    top_str = ', '.join(['{0}(WR={1:.1%})'.format(n, v['wr']) for n, v in top_indicators])
    bottom_str = ', '.join(['{0}(WR={1:.1%})'.format(n, v['wr']) for n, v in bottom_indicators])
    summary.append(f"Top表现指标（权重应提升）: {top_str}")
    summary.append(f"弱表现指标（权重应降低）: {bottom_str}")

    # 找最佳整体持有期
    all_period_wrs = {}
    for ind, data in signal_data.items():
        for h_str, stats in data.get('all_holdings', {}).items():
            h = int(h_str)
            if h not in all_period_wrs:
                all_period_wrs[h] = []
            all_period_wrs[h].append(stats.get('wr', 0))
    if all_period_wrs:
        avg_by_period = {h: sum(wrs)/len(wrs) for h, wrs in all_period_wrs.items() if wrs}
        best_overall_h = max(avg_by_period, key=avg_by_period.get)
        summary.append(f"整体最佳持有期: {best_overall_h}根K线 (平均WR={avg_by_period[best_overall_h]:.1%})")

    total_good_combos = sum(len(v) for v in good_combos.values()) if isinstance(good_combos, dict) else 0
    summary.append(f"发现高质量组合: {total_good_combos}个 (WR>65%, PF>1.5, N>100)")

    calibration['summary'] = summary

    # 6. 新参数建议（可直接对比brahma_brain）
    calibration['new_params'] = {
        "indicator_weights": new_weights,
        "rsi_thresholds": {
            "oversold": 30,
            "overbought": 70,
            "1h": {"oversold": rsi_adj.get('1h_oversold', {}).get('recommended', 30)},
            "1d": {"oversold": rsi_adj.get('1d_oversold', {}).get('recommended', 30)},
        },
        "holding_period_default": best_overall_h if all_period_wrs else 12,
        "regime_multipliers": CURRENT_BRAHMA_PARAMS['regime_multipliers'],  # 保持不变，数据不足
    }

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = RESULTS_DIR / f'calibration_v5_{ts}.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(calibration, f, ensure_ascii=False, indent=2)

    log.info(f"✅ 校准完成，结果保存至: {out_path}")
    return calibration, str(out_path)


if __name__ == '__main__':
    result, path = calibrate()
    if result:
        log.info("=== 参数校准摘要 ===")
        for line in result['summary']:
            log.info(f"  • {line}")

# ── 自检 ──
if __name__ == "__main__":
    assert VERSION == "v5.1", f"version={VERSION}"
    assert callable(calibrate), "calibrate must be callable"
    assert callable(find_latest_result), "find_latest_result must be callable"
    print(f"✅ dharma/calibrate {VERSION} 自检通过")

def get_latest_calibration() -> dict:
    """读取最新校准结果，无结果返回空dict"""
    f = find_latest_result('calibration_v5_*.json')
    return load_json(f) if f else {}

def get_calibration_summary() -> list:
    """获取最新校准摘要文本列表"""
    cal = get_latest_calibration()
    return cal.get('summary', ['暂无校准数据'])

def should_recalibrate(min_trades: int = 50) -> bool:
    """判断是否需要重新校准（实盘笔数≥min_trades）"""
    try:
        import json, pathlib
        trades_file = pathlib.Path(__file__).parent.parent / 'data/hunter_v2_trades.json'
        trades = json.loads(trades_file.read_text())
        closed = [t for t in trades if t.get('status') == 'CLOSED']
        return len(closed) >= min_trades
    except Exception:
        return False
