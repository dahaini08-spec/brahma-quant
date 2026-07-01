"""
tv_signal_handler.py — TradingView Premium Webhook 信号处理桥
设计院·梵天 2026-06-30 封印

TV Pine Script → POST /hooks/tv → 此脚本 → brahma_bus → brahma_core增强

支持的TV信号类型:
  1. OB_SIGNAL    — SMC Order Block标记
  2. FVG_SIGNAL   — Fair Value Gap标记  
  3. LIQ_LEVEL    — Liquidity Level (机构清算位)
  4. VOL_PROFILE  — Volume Profile POC/VAH/VAL
  5. ALERT_CROSS  — 价格穿越关键位
  6. STRUCTURE    — MSB/CHoCH结构突破

调用方式: 由openclaw webhook hooks自动触发，不直接运行
"""

import json, os, time
from pathlib import Path
from datetime import datetime, timezone

BASE      = Path(__file__).parent.parent.parent
TV_CACHE  = BASE / 'data' / 'tv_signals'
TV_LOG    = BASE / 'data' / 'tv_signal_log.jsonl'

TV_CACHE.mkdir(parents=True, exist_ok=True)


def process_tv_signal(payload: dict) -> dict:
    """
    处理从TV Pine Script收到的webhook payload
    写入tv_signals/缓存供brahma_core读取
    """
    ts     = datetime.now(timezone.utc).isoformat()
    sym    = payload.get('symbol', '').upper().replace('.P','').replace('BINANCE:','')
    if not sym.endswith('USDT'):
        sym = sym + 'USDT'

    sig_type = payload.get('type', 'UNKNOWN').upper()
    price    = float(payload.get('price', 0))
    tf       = payload.get('timeframe', '1H')
    note     = payload.get('note', '')

    record = {
        'ts': ts, 'symbol': sym, 'type': sig_type,
        'price': price, 'timeframe': tf, 'note': note,
        'raw': payload,
    }

    # 按标的写入缓存文件（brahma_core读取）
    cache_file = TV_CACHE / f'{sym}.json'
    existing = {}
    if cache_file.exists():
        try: existing = json.loads(cache_file.read_text())
        except: pass

    # 按类型存储，保留最新5条
    if sig_type not in existing:
        existing[sig_type] = []
    existing[sig_type].insert(0, record)
    existing[sig_type] = existing[sig_type][:5]   # 只保留最新5条
    existing['_last_updated'] = ts

    cache_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2))

    # 追加日志
    with open(TV_LOG, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')

    print(f'[TV-Bridge] ✅ {sym} {sig_type} ${price:.2f} @{tf} → {cache_file}')
    return {'ok': True, 'symbol': sym, 'type': sig_type}


def get_tv_signals(symbol: str) -> dict:
    """
    brahma_core调用: 获取指定标的的最新TV信号缓存
    返回: {OB_SIGNAL: [...], FVG_SIGNAL: [...], LIQ_LEVEL: [...], ...}
    """
    sym = symbol.upper()
    cache_file = TV_CACHE / f'{sym}.json'
    if not cache_file.exists():
        return {}
    try:
        data = json.loads(cache_file.read_text())
        # 过滤超过4小时的信号（避免过时数据影响）
        cutoff = time.time() - 4 * 3600
        fresh = {}
        for k, signals in data.items():
            if k.startswith('_'): continue
            valid = [s for s in signals
                     if datetime.fromisoformat(s['ts'].replace('Z','+00:00')).timestamp() > cutoff]
            if valid:
                fresh[k] = valid
        return fresh
    except:
        return {}


def format_tv_enhancement(symbol: str) -> str:
    """格式化TV信号增强摘要（供分析报告使用）"""
    signals = get_tv_signals(symbol)
    if not signals:
        return '  [TV] 暂无TradingView信号增强'

    lines = ['  [TV Premium增强]:']
    type_labels = {
        'OB_SIGNAL':   'OB',
        'FVG_SIGNAL':  'FVG',
        'LIQ_LEVEL':   '机构清算位',
        'VOL_PROFILE': 'Volume POC',
        'ALERT_CROSS': '价格穿越',
        'STRUCTURE':   '结构突破',
    }
    for sig_type, items in signals.items():
        label = type_labels.get(sig_type, sig_type)
        for s in items[:2]:
            lines.append(f'    {label}: ${s["price"]:.2f} @{s["timeframe"]}  {s.get("note","")}')
    return '\n'.join(lines)


if __name__ == '__main__':
    # 测试模式
    test_payload = {
        'symbol': 'BTCUSDT',
        'type': 'LIQ_LEVEL',
        'price': 60347.5,
        'timeframe': '4H',
        'note': '空头密集止损区',
    }
    result = process_tv_signal(test_payload)
    print('测试结果:', result)
    print('缓存内容:', get_tv_signals('BTCUSDT'))
