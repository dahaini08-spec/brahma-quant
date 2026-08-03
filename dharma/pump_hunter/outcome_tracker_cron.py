#!/usr/bin/env python3
"""
outcome_tracker_cron.py — 暴涨猎手推送结果追踪
设计院 2026-08-03 | 首次建立闭环WR数据

每次运行：检查所有未结算的推送信号，4H和8H后对比价格涨幅
结果写入 data/pump_hunter_outcomes.json
"""
import sys, os, json, time, urllib.request
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from pathlib import Path
from datetime import datetime, timezone

BASE    = Path(__file__).parent.parent.parent
RECORD  = Path(__file__).parent / 'signal_push_record.json'
OUTCOME = BASE / 'data' / 'pump_hunter_outcomes.json'
FAPI    = 'https://fapi.binance.com'

def get_price(sym):
    url = f'{FAPI}/fapi/v1/ticker/price?symbol={sym}'
    return float(json.loads(urllib.request.urlopen(url, timeout=5).read())['price'])

def load_outcomes():
    return json.loads(OUTCOME.read_text()) if OUTCOME.exists() else {}

def save_outcomes(d):
    OUTCOME.write_text(json.dumps(d, indent=2, default=str))

def main():
    record  = json.loads(RECORD.read_text()) if RECORD.exists() else {}
    outcomes = load_outcomes()
    now_ts  = time.time()
    updated = 0
    new_outcomes = 0

    for sym, info in record.items():
        push_ts = info.get('last_push_ts', 0)
        push_px = info.get('push_price')
        if not push_ts or not push_px:
            continue

        age_h = (now_ts - push_ts) / 3600
        key   = f"{sym}_{int(push_ts)}"

        if key in outcomes:
            existing = outcomes[key]
            # 已有8H结果 → 跳过
            if existing.get('result_8h') is not None:
                continue
        else:
            outcomes[key] = {
                'symbol': sym,
                'push_ts': push_ts,
                'push_at': info.get('last_push_at', '?'),
                'push_score': info.get('last_score', 0),
                'push_price': push_px,
                'result_4h': None,
                'result_8h': None,
            }
            new_outcomes += 1

        try:
            cur_px = get_price(sym)
            chg_pct = (cur_px - push_px) / push_px * 100

            if age_h >= 8 and outcomes[key].get('result_8h') is None:
                outcomes[key]['result_8h'] = {
                    'price': cur_px, 'chg_pct': round(chg_pct, 2),
                    'win': chg_pct >= 5.0,   # 8H内涨5%=胜
                    'checked_at': datetime.now(timezone.utc).isoformat()
                }
                updated += 1
            elif age_h >= 4 and outcomes[key].get('result_4h') is None:
                outcomes[key]['result_4h'] = {
                    'price': cur_px, 'chg_pct': round(chg_pct, 2),
                    'win': chg_pct >= 3.0,   # 4H内涨3%=胜
                    'checked_at': datetime.now(timezone.utc).isoformat()
                }
                updated += 1
        except Exception:
            pass

    save_outcomes(outcomes)

    # WR统计
    settled_4h = [v for v in outcomes.values() if v.get('result_4h') is not None]
    settled_8h = [v for v in outcomes.values() if v.get('result_8h') is not None]
    wr_4h = sum(1 for v in settled_4h if v['result_4h']['win']) / len(settled_4h) if settled_4h else None
    wr_8h = sum(1 for v in settled_8h if v['result_8h']['win']) / len(settled_8h) if settled_8h else None

    wr4_str = f'{wr_4h:.1%}' if wr_4h is not None else 'N/A'
    wr8_str = f'{wr_8h:.1%}' if wr_8h is not None else 'N/A'
    print(f'[outcome] 新增={new_outcomes} 更新={updated} | 4H结算={len(settled_4h)}条 WR4H={wr4_str} | 8H结算={len(settled_8h)}条 WR8H={wr8_str}')

    # 若WR有效，推送一次更新
    if settled_8h and updated > 0:
        try:
            from scripts.push_hub import _jarvis
            msg = f'📊 [暴涨猎手WR更新]\n8H胜率: {wr_8h:.1%} (n={len(settled_8h)}) | 4H胜率: {wr_4h:.1%} (n={len(settled_4h)})\n最新结算: {updated}条'
            _jarvis(msg, dedup_ttl=3600)
        except Exception:
            pass

if __name__ == '__main__':
    main()
