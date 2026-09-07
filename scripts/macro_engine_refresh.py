"""macro_engine_refresh.py — 宏观数据刷新脚本
接入位置：brahma_crontab.txt（每4小时）
2026-09-07 三方评估封印修复
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brahma_brain.narrative_engine import (
    get_fear_greed,
    get_btc_dominance,
    get_active_risk,
)


def main():
    print("=== 宏观数据刷新 ===")
    try:
        fg = get_fear_greed()
        print(f"[FearGreed] {fg.get('value', 'N/A')} ({fg.get('value_classification', 'N/A')})")
    except Exception as e:
        print(f"[FearGreed] 获取失败: {e}")

    try:
        dom = get_btc_dominance()
        print(f"[BTC.D] {dom.get('dominance', 'N/A')}%")
    except Exception as e:
        print(f"[BTC.D] 获取失败: {e}")

    try:
        risk = get_active_risk()
        events = risk.get('events', [])
        print(f"[MacroRisk] 近期风险事件: {len(events)}个")
        for ev in events[:3]:
            print(f"  - {ev.get('date','?')} {ev.get('name','?')}")
    except Exception as e:
        print(f"[MacroRisk] 获取失败: {e}")

    print("=== 宏观刷新完成 ===")


if __name__ == '__main__':
    main()
