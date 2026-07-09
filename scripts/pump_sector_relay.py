#!/usr/bin/env python3
"""
pump_sector_relay.py — 暴涨板块联动中继器 v1.0
设计院封印 · 2026-06-29

【核心职责】
  读取 pump_detected.json（market_screener输出）
  → 板块映射 → 找出同板块低位未暴涨标的
  → 写入 sector_candidates.json
  → brahma_scan_all.py --sector 读取合并分析

【零AI消耗】
  纯Python脚本，无agentTurn，无LLM调用
  只做数据映射，不做分析

【触发条件】
  pump_detected.json 中出现 count >= 1（有任何暴涨标的）
  才写入联动候选，否则跳过（输出HEARTBEAT_OK）

【板块定义】
  根据Binance板块分类 + 历史联动数据 手工维护
  每季度由达摩院回放验证后更新
"""

import sys, os, json, time
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

# ══════════════════════════════════════════════════════════════
# 板块映射字典 v1.0  (设计院 2026-06-29)
# key = 触发标的（龙头/暴涨标的）
# value = 同板块联动候选列表（低位，梵天待分析）
# ══════════════════════════════════════════════════════════════
SECTOR_MAP = {
    # ── AI / DePIN 板块 ──────────────────────────────────────
    'RENDERUSDT':  ['FETUSDT', 'AGIXUSDT', 'OCEANUSDT', 'TAORUSDT', 'AIUSDT'],
    'FETUSDT':     ['RENDERUSDT', 'AGIXUSDT', 'OCEANUSDT', 'AIUSDT'],
    'AGIXUSDT':    ['RENDERUSDT', 'FETUSDT', 'OCEANUSDT'],
    'TAORUSDT':    ['RENDERUSDT', 'FETUSDT', 'AIUSDT'],
    'AIUSDT':      ['RENDERUSDT', 'FETUSDT', 'AGIXUSDT'],
    'VIRTUALUSDT': ['AIUSDT', 'FETUSDT', 'RENDERUSDT'],
    'ACTUSDT':     ['VIRTUALUSDT', 'AIUSDT', 'RENDERUSDT', 'FETUSDT'],

    # ── GameFi / Metaverse 板块 ──────────────────────────────
    'AXSUSDT':     ['SANDUSDT', 'ENJUSDT', 'MBOXUSDT', 'CHZUSDT', 'GALAUSDT'],
    'SANDUSDT':    ['AXSUSDT', 'ENJUSDT', 'MBOXUSDT', 'GALAUSDT'],
    'ENJUSDT':     ['SANDUSDT', 'AXSUSDT', 'MBOXUSDT'],
    'MBOXUSDT':    ['SANDUSDT', 'ENJUSDT', 'AXSUSDT'],
    'GALAUSDT':    ['SANDUSDT', 'AXSUSDT', 'ENJUSDT', 'MBOXUSDT'],
    'RAVEUSDT':    ['SANDUSDT', 'ENJUSDT', 'AXSUSDT', 'GALAUSDT'],
    'TACUSDT':     ['SANDUSDT', 'ENJUSDT', 'MBOXUSDT', 'GALAUSDT'],

    # ── Meme / 社区代币 板块 ─────────────────────────────────
    '1000PEPEUSDT':  ['DOGEUSDT', 'SHIBUSDT', 'FLOKIUSDT', '1000BONKUSDT', 'WIFUSDT'],
    'DOGEUSDT':      ['1000PEPEUSDT', 'SHIBUSDT', 'FLOKIUSDT', 'WIFUSDT'],
    'SHIBUSDT':      ['DOGEUSDT', '1000PEPEUSDT', 'FLOKIUSDT'],
    'FLOKIUSDT':     ['DOGEUSDT', '1000PEPEUSDT', 'SHIBUSDT'],
    'WIFUSDT':       ['DOGEUSDT', '1000PEPEUSDT', 'BONKUSDT'],
    'GWEIUSDT':      ['1000PEPEUSDT', 'DOGEUSDT', 'SHIBUSDT'],

    # ── Layer2 / 扩容 板块 ───────────────────────────────────
    'ARBUSDT':     ['OPUSDT', 'POLYUSDT', 'MATICUSDT', 'STRKUSDT'],
    'OPUSDT':      ['ARBUSDT', 'POLYUSDT', 'STRKUSDT'],
    'STRKUSDT':    ['ARBUSDT', 'OPUSDT'],
    'POLUSDT':     ['ARBUSDT', 'OPUSDT', 'STRKUSDT'],

    # ── DeFi 板块 ────────────────────────────────────────────
    'UNIUSDT':     ['AAVEUSDT', 'CRVUSDT', 'MKRUSDT', 'COMPUSDT', 'LDOUSDT'],
    'AAVEUSDT':    ['UNIUSDT', 'CRVUSDT', 'COMPUSDT'],
    'CRVUSDT':     ['UNIUSDT', 'AAVEUSDT', 'COMPUSDT'],
    'LDOUSDT':     ['UNIUSDT', 'AAVEUSDT', 'STXUSDT'],

    # ── 数据/预言机 板块 ─────────────────────────────────────
    'GRTUSDT':     ['RENDERUSDT', 'FETUSDT', 'BANDUSDT'],
    'BANDUSDT':    ['GRTUSDT', 'FETUSDT'],

    # ── Layer1 竞争链 板块 ───────────────────────────────────
    'NEARUSDT':    ['APTUSDT', 'SUIUSDT', 'SEIUSDT'],
    'APTUSDT':     ['NEARUSDT', 'SUIUSDT', 'SEIUSDT'],
    'SUIUSDT':     ['APTUSDT', 'NEARUSDT', 'SEIUSDT'],

    # ── 基础设施/存储 板块 ───────────────────────────────────
    'FILECOINUSDT':['STORJUSDT', 'ARWEAVEUSDT'],
    'ARWEAVEUSDT': ['FILECOINUSDT', 'STORJUSDT'],
}

# 已暴涨标的不纳入联动候选（联动候选必须是低位标的）
PUMP_EXCLUDE_PCT = 20.0   # 24H涨幅>20%的不做联动候选


def run():
    """主逻辑：读取pump_detected → 生成sector_candidates"""
    t0 = time.time()

    # 1. 读取pump_detected.json
    pump_path = DATA / 'pump_detected.json'
    if not pump_path.exists():
        pass  # [静默]
        return False

    pump_data = json.loads(pump_path.read_text())
    pumped_list = pump_data.get('pumped', [])
    gen_ts = pump_data.get('generated', '?')

    if not pumped_list:
        pass  # [静默]
        _write_empty(gen_ts)
        return False

    pumped_syms = {p['symbol'] for p in pumped_list}
    pumped_pct  = {p['symbol']: p['pct24h'] for p in pumped_list}
    pass  # [静默]

    # 2. 读取实时ticker（过滤已暴涨的联动候选）
    import urllib.request
    try:
        raw = urllib.request.urlopen(
            'https://fapi.binance.com/fapi/v1/ticker/24hr', timeout=8
        ).read()
        tickers = json.loads(raw)
        ticker_map = {t['symbol']: float(t['priceChangePercent']) for t in tickers}
    except Exception as e:
        pass  # [静默]
        ticker_map = {}

    # 3. 板块联动扩展
    relay_set = set()
    relay_detail = {}   # symbol -> {from: 触发龙头, pump_pct}

    for pumped_sym in pumped_syms:
        related = SECTOR_MAP.get(pumped_sym, [])
        for rel_sym in related:
            if rel_sym in pumped_syms:
                continue   # 已暴涨，不纳入
            rel_pct = ticker_map.get(rel_sym, 0)
            if rel_pct >= PUMP_EXCLUDE_PCT:
                pass  # [静默]
                continue
            relay_set.add(rel_sym)
            if rel_sym not in relay_detail:
                relay_detail[rel_sym] = {'triggered_by': [], 'current_pct': rel_pct}
            relay_detail[rel_sym]['triggered_by'].append(
                f'{pumped_sym}(+{pumped_pct.get(pumped_sym,0):.1f}%)'
            )

    # 4. 无联动候选 → 也写空文件（让brahma_scan_all正常运行）
    if not relay_set:
        pass  # [静默]
        _write_empty(gen_ts)
        return False

    # 5. 构建候选列表
    candidates = []
    for sym in sorted(relay_set):
        detail = relay_detail.get(sym, {})
        candidates.append({
            'symbol':       sym,
            'source':       'sector_relay',
            'triggered_by': detail.get('triggered_by', []),
            'current_pct':  detail.get('current_pct', 0),
            'priority':     len(detail.get('triggered_by', [])),  # 越多龙头触发优先级越高
        })
    # 按触发数量降序（多龙头联动的优先分析）
    candidates.sort(key=lambda x: x['priority'], reverse=True)

    # 6. 写入sector_candidates.json
    output = {
        'ts':           time.time(),
        'generated':    time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()),
        'pump_source':  gen_ts,
        'pump_count':   len(pumped_list),
        'pumped_syms':  list(pumped_syms),
        'relay_count':  len(candidates),
        'candidates':   candidates,
    }
    out_path = DATA / 'sector_candidates.json'
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))

    elapsed = round(time.time() - t0, 1)
    pass  # [静默]
    for c in candidates[:8]:
        print(f'  {c["symbol"]:<18} 触发: {" / ".join(c["triggered_by"][:2])}  当前{c["current_pct"]:+.1f}%')
    return True


def _write_empty(source_ts: str):
    """写入空的sector_candidates（保证下游脚本不报错）"""
    output = {
        'ts':        time.time(),
        'generated': time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()),
        'pump_source': source_ts,
        'pump_count': 0,
        'pumped_syms': [],
        'relay_count': 0,
        'candidates': [],
    }
    out_path = DATA / 'sector_candidates.json'
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    pass  # [静默]


if __name__ == '__main__':
    result = run()
    if not result:
        pass  # [静默]
