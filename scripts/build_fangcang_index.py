#!/usr/bin/env python3
"""
build_fangcang_index.py — 方仓数据库统一索引构建器
2026-08-27 设计院封印 · 苏摩111批准

问题：214个分散文件，两套格式，重复难找
解法：统一字段 → 去重 → 生成 FANGCANG_MASTER_INDEX.json

字段标准化：
  symbol / timeframe / ts_squeeze_start / ts_burst
  direction(LONG/SHORT/CHOP) / min_bb_width / squeeze_bars
  rsi_at_burst / vol_ratio_peak / future_return / source
"""
import json, glob, time
from pathlib import Path
from collections import defaultdict

DATA = Path('/root/.openclaw/workspace/trading-system/data')
OUT  = DATA / 'FANGCANG_MASTER_INDEX.json'

def parse_ts(v) -> int:
    """时间戳解析：支持int/float/ISO字符串"""
    if not v: return 0
    if isinstance(v, (int, float)): return int(v)
    try:
        from datetime import datetime, timezone
        s = str(v).replace('+00:00','').replace('Z','').strip()
        fmt = '%Y-%m-%dT%H:%M:%S' if 'T' in s else '%Y-%m-%d %H:%M:%S'
        dt = datetime.strptime(s[:19], fmt)
        return int(dt.replace(tzinfo=timezone.utc).timestamp())
    except:
        return 0


def normalize_case(raw: dict, source: str) -> dict | None:
    """统一字段格式"""
    try:
        sym = raw.get('symbol', '').upper().replace('USDT','')  # 统一去掉USDT后缀
        if not sym: return None

        # 方向归一化
        direction = str(raw.get('direction', raw.get('breakout_direction', 'CHOP'))).upper()
        if direction in ('UP', 'LONG', 'BUY'):      direction = 'LONG'
        elif direction in ('DOWN', 'SHORT', 'SELL'): direction = 'SHORT'
        else:                                         direction = 'CHOP'

        # 时间戳（支持字符串和数字）
        ts_start = parse_ts(raw.get('ts_squeeze_start', raw.get('ts_start', 0)))
        ts_burst = parse_ts(raw.get('ts_burst', raw.get('ts_end', 0)))

        # 周期
        tf = str(raw.get('timeframe', raw.get('interval', 'unknown')))

        return {
            'symbol':       sym,
            'timeframe':    tf,
            'ts_start':     ts_start,
            'ts_burst':     ts_burst,
            'direction':    direction,
            'bbw_min':      float(raw.get('min_bb_width', raw.get('compress_bbw_min', 0)) or 0),
            'squeeze_bars': int(raw.get('squeeze_bars', raw.get('compress_bars', 0)) or 0),
            'rsi':          float(raw.get('rsi_at_burst', raw.get('rsi_at_end', 50)) or 50),
            'vol_ratio':    float(raw.get('vol_ratio_peak', raw.get('volume_ratio', 1)) or 1),
            'future_return':float(raw.get('future_return_24h', raw.get('future_return',
                                  raw.get('breakout_pct', 0))) or 0),
            'source':       source,
        }
    except Exception as e:
        return None


def build_index():
    all_cases = []
    dedup_keys = set()  # (symbol, ts_start, direction)
    stats = defaultdict(int)

    # ── 1. cases_ 合并版文件 ─────────────────────────────────────────
    for f in sorted(glob.glob(str(DATA / 'fangcang_cases_*.json'))):
        src = Path(f).stem
        try:
            raw_list = json.load(open(f))
            if not isinstance(raw_list, list): continue
            for raw in raw_list:
                c = normalize_case(raw, src)
                if not c: continue
                key = (c['symbol'], c['ts_start'], c['direction'])
                if key in dedup_keys:
                    stats['dedup_skip'] += 1
                    continue
                dedup_keys.add(key)
                all_cases.append(c)
                stats['cases_series'] += 1
        except Exception as e:
            stats['error'] += 1

    # ── 2. 按币种/周期分文件 ─────────────────────────────────────────
    for f in sorted(glob.glob(str(DATA / 'fangcang_*_*.json'))):
        fname = Path(f).stem
        # 跳过 cases_ 系列（已处理）和 summary/index 文件
        if 'cases' in fname or 'summary' in fname or 'index' in fname \
           or 'labeled' in fname or 'weight' in fname or 'snapshot' in fname:
            continue
        src = fname
        try:
            raw_list = json.load(open(f))
            if not isinstance(raw_list, list): continue
            for raw in raw_list:
                c = normalize_case(raw, src)
                if not c: continue
                key = (c['symbol'], c['ts_start'], c['direction'])
                if key in dedup_keys:
                    stats['dedup_skip'] += 1
                    continue
                dedup_keys.add(key)
                all_cases.append(c)
                stats['tf_series'] += 1
        except:
            stats['error'] += 1

    # ── 3. 排序：按 symbol + ts_start ───────────────────────────────
    all_cases.sort(key=lambda x: (x['symbol'], x['ts_start']))

    # ── 4. 统计摘要 ──────────────────────────────────────────────────
    by_symbol  = defaultdict(int)
    by_dir     = defaultdict(int)
    by_tf      = defaultdict(int)
    for c in all_cases:
        by_symbol[c['symbol']] += 1
        by_dir[c['direction']] += 1
        by_tf[c['timeframe']] += 1

    index = {
        'built_at':      time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'version':       'v1.0',
        'total_cases':   len(all_cases),
        'dedup_skipped': stats['dedup_skip'],
        'source_counts': dict(stats),
        'by_symbol':     dict(sorted(by_symbol.items(), key=lambda x: -x[1])),
        'by_direction':  dict(by_dir),
        'by_timeframe':  dict(sorted(by_tf.items())),
        'cases':         all_cases,
    }

    OUT.write_text(json.dumps(index, ensure_ascii=False, separators=(',', ':')))
    size_mb = OUT.stat().st_size / 1024 / 1024

    print(f'✅ FANGCANG_MASTER_INDEX.json 构建完成')
    print(f'   总案例: {len(all_cases)}条 (去重跳过: {stats["dedup_skip"]}条)')
    print(f'   文件大小: {size_mb:.1f} MB')
    print(f'   来源: cases系列={stats["cases_series"]} 周期分文件={stats["tf_series"]}')
    print(f'\n   币种分布（Top10）:')
    for sym, n in sorted(by_symbol.items(), key=lambda x: -x[1])[:10]:
        print(f'     {sym:12s}: {n}条')
    print(f'\n   方向分布: {dict(by_dir)}')
    print(f'   周期分布: {dict(sorted(by_tf.items()))}')
    return index

if __name__ == '__main__':
    build_index()
