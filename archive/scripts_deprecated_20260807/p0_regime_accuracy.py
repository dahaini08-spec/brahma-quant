#!/usr/bin/env python3
"""
P0: 代理体制 vs 真实35维体制 准确率验证
设计院 2026-07-24 苏摩111封印

对比：
  - 代理体制 (r4h/ema2050 快速推断)
  - 真实体制 (brahma_engine.analyze() 35维结果)

采样策略：取最近 N 根 4H K线，每个时间点做体制推断
"""
import sys
import json
import time
import os
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
sys.path.insert(0, str(Path(__file__).parent))

from brahma_brain.brahma_engine import analyze, get_klines

# ─── 配置 ───────────────────────────────────────────────
SYMBOLS = ['BTCUSDT', 'ETHUSDT']
SAMPLE_N = 50  # 每个标的取50个采样点（共100个）
OUTPUT = Path(__file__).parent.parent / 'data' / 'backtest' / 'p0_regime_accuracy.json'


def proxy_regime(r4h: float, ema20_4h: float, ema50_4h: float) -> str:
    """代理体制推断（快速，不用LLM）"""
    if r4h is None or ema20_4h is None or ema50_4h is None:
        return 'UNKNOWN'
    ema_spread = (ema20_4h - ema50_4h) / ema50_4h * 100
    if r4h > 60 and ema_spread > 1.5:
        return 'BULL_TREND'
    elif r4h < 40 and ema_spread < -1.5:
        return 'BEAR_TREND'
    elif r4h > 50 and ema_spread > 0:
        return 'BULL_EARLY'
    elif r4h < 50 and ema_spread < 0:
        return 'BEAR_EARLY'
    else:
        return 'CHOP_MID'


def get_proxy_regime_from_klines(klines_4h: list, idx: int) -> str:
    """从K线数据计算代理体制"""
    if idx < 50:
        return 'UNKNOWN'
    closes = [float(k[4]) for k in klines_4h[:idx+1]]

    # RSI-4H 简化计算（14期）
    gains, losses = [], []
    for i in range(1, 15):
        diff = closes[-i] - closes[-i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / 14 or 1e-9
    avg_loss = sum(losses) / 14 or 1e-9
    rs = avg_gain / avg_loss
    r4h = 100 - 100 / (1 + rs)

    # EMA20/EMA50
    def ema(data, period):
        k = 2 / (period + 1)
        e = data[0]
        for v in data[1:]:
            e = v * k + e * (1 - k)
        return e

    ema20 = ema(closes[-21:], 20)
    ema50 = ema(closes[-51:], 50)
    return proxy_regime(r4h, ema20, ema50)


def main():
    print("🏛️ P0: 代理体制准确率验证")
    print("=" * 50)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    results = []
    confusion = defaultdict(lambda: defaultdict(int))
    errors = []

    for sym in SYMBOLS:
        print(f"\n▶ {sym}: 拉取4H K线...")
        try:
            klines = get_klines(sym, '4h', limit=200)
        except Exception as e:
            print(f"  ❌ K线拉取失败: {e}")
            continue

        print(f"  获取 {len(klines)} 根4H K线")

        # 采样最近 SAMPLE_N 个4H点
        step = max(1, (len(klines) - 60) // SAMPLE_N)
        sample_idxs = list(range(60, len(klines), step))[:SAMPLE_N]

        for i, idx in enumerate(sample_idxs):
            ts_ms = int(klines[idx][0])
            price = float(klines[idx][4])

            # 代理体制（快速，无API）
            proxy = get_proxy_regime_from_klines(klines, idx)

            # 真实体制：调用当前brahma_engine
            # 注意：无法回溯历史时间点，只能用当前值
            # 对于当前最新点，true_regime来自实时analyze
            if idx == sample_idxs[-1]:
                try:
                    result = analyze(sym, signal_dir='SHORT', deep=False)
                    true_regime = result.get('regime', 'UNKNOWN')
                except Exception as e:
                    print(f"  ⚠️ analyze失败: {e}")
                    true_regime = 'UNKNOWN'
            else:
                # 历史点只能用代理体制作参考基准
                # 真实体制 = 以当前最新analyze结果为金标准
                # P0阶段：验证代理体制与当前真实体制的一致性
                true_regime = None  # 历史点无法获取真实35维

            if true_regime:
                match = (proxy == true_regime)
                confusion[proxy][true_regime] += 1
                if not match:
                    errors.append({
                        'sym': sym,
                        'ts': ts_ms,
                        'price': price,
                        'proxy': proxy,
                        'real': true_regime
                    })
                results.append({
                    'sym': sym,
                    'idx': idx,
                    'price': price,
                    'proxy': proxy,
                    'real': true_regime,
                    'match': match
                })
                print(f"  [{i+1}/{len(sample_idxs)}] proxy={proxy:<12} real={true_regime:<12} {'✅' if match else '❌'}")

            # 限速，避免API压力
            if i % 5 == 4:
                time.sleep(0.5)

    # ─── 统计 ───────────────────────────────────────────
    if not results:
        print("\n❌ 无有效采样结果（历史点无真实体制基准）")
        print("\n📊 P0结论：")
        print("  当前架构：brahma_engine只能做实时分析，无历史回放能力")
        print("  代理体制：基于r4h+ema2050快速推断，覆盖所有历史K线")
        print("  差距量化：需要历史回放接口才能完成精确准确率计算")
        print("\n  当前实时验证（最新K线对比）:")
        for sym in SYMBOLS:
            klines = get_klines(sym, '4h', limit=100)
            proxy_now = get_proxy_regime_from_klines(klines, len(klines)-1)
            try:
                r = analyze(sym, signal_dir='SHORT', deep=False)
                real_now = r.get('regime', 'N/A')
                match = '✅' if proxy_now == real_now else '❌'
                print(f"  {sym}: proxy={proxy_now} real={real_now} {match}")
            except Exception as e:
                print(f"  {sym}: proxy={proxy_now} real=ERROR({e})")
        return

    total = len(results)
    matches = sum(1 for r in results if r['match'])
    accuracy = matches / total * 100

    # 转换confusion为普通dict
    confusion_plain = {k: dict(v) for k, v in confusion.items()}

    output_data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'symbols': SYMBOLS,
        'total': total,
        'matches': matches,
        'accuracy': round(accuracy, 1),
        'confusion': confusion_plain,
        'errors': errors[:20],
        'all_results': results
    }
    OUTPUT.write_text(json.dumps(output_data, indent=2, default=str))

    print(f"\n{'='*50}")
    print(f"📊 P0结果: 准确率 {accuracy:.1f}% ({matches}/{total})")
    print(f"\n混淆矩阵:")
    for proxy_r, real_dict in confusion_plain.items():
        total_row = sum(real_dict.values())
        correct = real_dict.get(proxy_r, 0)
        print(f"  代理={proxy_r:<14}: {correct}/{total_row} ({correct/total_row*100:.0f}%正确)")

    if errors:
        print(f"\n主要误差 (前5):")
        for e in errors[:5]:
            print(f"  {e['sym']} proxy={e['proxy']} → real={e['real']}")

    print(f"\n结果已保存: {OUTPUT}")


if __name__ == '__main__':
    main()
