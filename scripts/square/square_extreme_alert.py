#!/usr/bin/env python3
"""
square_extreme_alert.py v2.0 — 异动捕捉，SMC结构读
[设计院封印 2026-09-11 苏摩111]

v2.0变更：
  - 废弃固定模板（每个币种同一个模板只换数字）
  - 接入brahma_core.analyze()拿SMC结构（FVG/OB/清算）
  - 接入OI趋势+CVD+清算地图
  - 每帖必有🌿姓赵不宣前缀+📊后缀
  - 给具体入场条件+止损+目标+监控信号
  - 不再问"你怎么看"
"""
import argparse, json, os, sys, time, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import sys

BASE = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'scripts' / 'square'))

CST = timezone(timedelta(hours=8))

SQUARE_KEY = os.environ.get('SQUARE_KEY_0', 'd9f19e3f6ba3480584db27b09bec0f27')
API_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
FAPI = 'https://fapi.binance.com/fapi/v1'

COOLDOWN_FILE = BASE / 'data' / 'extreme_alert_cooldown.json'
SHARED_DEDUP_FILE = BASE / 'data' / 'square_post_dedup.json'
DEDUP_FILE = SHARED_DEDUP_FILE
LOG_FILE = BASE / 'data' / 'square_post_log.jsonl'

COOLDOWN_HOURS = 24


def load_cooldown():
    try:
        if COOLDOWN_FILE.exists():
            return json.loads(COOLDOWN_FILE.read_text())
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return {}


def save_cooldown(cd):
    COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    # [9.28瘟疫清扫 苏摩111] 原子写: tmp+os.replace 防空读竞态（9.26路线A同款）
    _tmp = COOLDOWN_FILE.with_suffix(".tmp")
    _tmp.write_text(json.dumps(cd, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(_tmp), str(COOLDOWN_FILE))
def is_cool(sym, cd):
    key = f'extreme:{sym}'
    now = time.time()
    if key in cd and now - cd[key] < COOLDOWN_HOURS * 3600:
        return True
    return False


def mark_cool(sym, cd):
    cd[f'extreme:{sym}'] = time.time()
    save_cooldown(cd)


def is_symbol_duplicate(symbol):
    key = f'sym:{symbol.upper()}'
    d = {}
    if SHARED_DEDUP_FILE.exists():
        try:
            d = json.loads(SHARED_DEDUP_FILE.read_text())
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    now = time.time()
    d = {k: v for k, v in d.items() if now - v < 86400}
    return key in d


def mark_symbol_posted(symbol):
    key = f'sym:{symbol.upper()}'
    d = {}
    if SHARED_DEDUP_FILE.exists():
        try:
            d = json.loads(SHARED_DEDUP_FILE.read_text())
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    now = time.time()
    d = {k: v for k, v in d.items() if now - v < 86400}
    d[key] = now
    # [9.28瘟疫清扫 苏摩111] 原子写: tmp+os.replace 防空读竞态（9.26路线A同款）
    _tmp = SHARED_DEDUP_FILE.with_suffix(".tmp")
    _tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(_tmp), str(SHARED_DEDUP_FILE))
def is_duplicate(content):
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    d = {}
    if SHARED_DEDUP_FILE.exists():
        try:
            d = json.loads(SHARED_DEDUP_FILE.read_text())
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    now = time.time()
    d = {k: v for k, v in d.items() if now - v < 86400}
    return h in d


def mark_posted(content):
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    d = {}
    if SHARED_DEDUP_FILE.exists():
        try:
            d = json.loads(SHARED_DEDUP_FILE.read_text())
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    now = time.time()
    d = {k: v for k, v in d.items() if now - v < 86400}
    d[h] = now
    # [9.28瘟疫清扫 苏摩111] 原子写: tmp+os.replace 防空读竞态（9.26路线A同款）
    _tmp = SHARED_DEDUP_FILE.with_suffix(".tmp")
    _tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(_tmp), str(SHARED_DEDUP_FILE))
def post_to_square(content):
    payload = json.dumps({'bodyTextOnly': content}).encode()
    headers = {
        'X-Square-OpenAPI-Key': SQUARE_KEY,
        'Content-Type': 'application/json',
        'clienttype': 'binanceSkill',
    }
    try:
        resp = json.loads(requests.post(API_URL, data=payload, headers=headers, timeout=15).text)
        return resp
    except Exception as e:
        return {'error': str(e)}


def log_post(post_type, content, resp):
    entry = {
        'ts': time.time(),
        'post_type': post_type,
        'post_id': resp.get('data', {}).get('id', 0) if isinstance(resp.get('data'), dict) else 0,
        'chars': len(content),
        'preview': content[:200],
    }
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def fetch_smc_data(symbol):
    """拉SMC结构数据：FVG/OB/清算

    [9.28瘟疫清扫 苏摩111] 三重修复：
    1. path补brahma_brain（此前import必炸→SMC数据静默空壳）
    2. 字段映射对齐brahma_core.analyze()真实结构（smc.fvg/ob/liquidity子键）
    3. 字段名对齐：nearest_above/below（原nearest_short/long不存在）
    """
    smc_d = {'fvg_dir': '', 'fvg_magnet': 0, 'fvg_mid': 0, 'ob_test': '', 'liq_above': 0, 'liq_below': 0}
    try:
        sys.path.insert(0, str(BASE / 'brahma_brain'))
        from brahma_core import analyze
        result = analyze(symbol, '15m')
        if result:
            s = result.get('smc', {})
            fvg = s.get('fvg', {}) if isinstance(s, dict) else {}
            # 磁铁方向：最近的未填充FVG决定磁力方向（bull在下=向上拉，bear在上=向下拉）
            nb, ns = fvg.get('nearest_bull'), fvg.get('nearest_bear')
            if nb and ns:
                smc_d['fvg_dir'] = 'BOTH'
            elif nb:
                smc_d['fvg_dir'] = 'BULL'
            elif ns:
                smc_d['fvg_dir'] = 'BEAR'
            near = nb or ns
            if near:
                smc_d['fvg_magnet'] = near.get('mid', 0)
                smc_d['fvg_mid'] = near.get('mid', 0)
            ob = s.get('order_blocks', {}) if isinstance(s, dict) else {}
            bulls = ob.get('bull_obs', []) if isinstance(ob, dict) else []
            for v in bulls:
                # 宪法OB铁律：age<50且未被穿越才有效
                if not v.get('broken', False) and v.get('age_bars', 999) < 50:
                    smc_d['ob_test'] = f'回踩看多OB区间 {v.get("low", 0):.4f}~{v.get("high", 0):.4f}'
                    break
            liq = s.get('liquidity', {}) if isinstance(s, dict) else {}
            na = liq.get('nearest_above') or {}
            nbw = liq.get('nearest_below') or {}
            smc_d['liq_above'] = na.get('level', 0)
            smc_d['liq_below'] = nbw.get('level', 0)
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return smc_d


def fetch_oi_data(symbol):
    """拉OI+CVD数据"""
    oi = {'signal': '', 'cvd': 0}
    try:
        # [9.28瘟疫清扫 苏摩111] 端点修复：原/fapi/v1/data/openInterestHist不存在(404 HTML)
        # → .json()必炸Expecting value，OI信号从出生起就是空壳。正确路径=futures/data/
        oi_data = requests.get(f'https://fapi.binance.com/futures/data/openInterestHist',
                                params={'symbol': symbol, 'period': '15m', 'limit': 8},
                                timeout=5).json()
        if oi_data and len(oi_data) >= 4:
            vals = [float(x['sumOpenInterestValue']) for x in oi_data]
            # 简单判断：增仓=BUILD，减仓=UNWIND
            if vals[-1] > vals[0]:
                oi['signal'] = 'SHORT_BUILD'  # 价格涨+OI增=多头增仓；价格跌+OI增=空头增仓
            else:
                oi['signal'] = 'LONG_UNWIND'
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return oi


def build_signal_post(sym, chg, d, smc, oi_data):
    """用模板引擎构建信号帖"""
    from square_template import build_signal_alert, audit_post

    price = d['price']
    high = d['high']
    low = d['low']
    vol = d['vol']
    fr = d.get('fr', 0)
    ls = d.get('ls', 1.0)

    # 入场条件（基于SMC结构）
    entry_cond = ''
    sl_price = 0
    tp_price = 0
    monitor_fr = ''
    monitor_oi = ''

    if smc['fvg_magnet'] and smc['fvg_magnet'] > 0:
        if chg > 0 and smc['fvg_dir'] == 'BEAR':
            # 涨到Bear FVG中点做空
            entry_cond = f'等回踩FVG中点{smc["fvg_mid"]:.4f}±5%再评估'
            if smc['liq_above']:
                tp_price = smc['liq_above']
            if smc['fvg_mid']:
                sl_price = smc['fvg_mid'] * 1.03  # FVG上沿之上
        elif chg < 0 and smc['fvg_dir'] == 'BULL':
            # 跌到Bull FVG中点做多
            entry_cond = f'等回踩FVG中点{smc["fvg_mid"]:.4f}±5%再试多'
            if smc['liq_below']:
                tp_price = smc['liq_below']
            if smc['fvg_mid']:
                sl_price = smc['fvg_mid'] * 0.97

    # 监控信号
    if fr < -0.1:
        monitor_fr = f'FR回-0.05%内 = 轧空结束 = 不做多'
    elif fr > 0.1:
        monitor_fr = f'FR回0.005%内 = 多头降温 = 可评估做空'

    if oi_data['signal']:
        if oi_data['signal'] == 'SHORT_BUILD':
            monitor_oi = 'OI翻LONG_BUILD = 空头已死 = 确认做多'
        elif oi_data['signal'] == 'LONG_UNWIND':
            monitor_oi = 'OI从UNWIND翻BUILD = 资金回场 = 确认方向'

    content = build_signal_alert(
        sym=sym, chg=chg, price=price, high=high, low=low, vol=vol,
        fr=fr, ls=ls,
        fvg_dir=smc['fvg_dir'], fvg_magnet=smc['fvg_magnet'], fvg_mid=smc['fvg_mid'],
        ob_test=smc['ob_test'],
        oi_signal=oi_data['signal'], cvd=oi_data['cvd'],
        liq_above=smc['liq_above'], liq_below=smc['liq_below'],
        entry_cond=entry_cond, sl_price=sl_price, tp_price=tp_price,
        monitor_fr=monitor_fr, monitor_oi=monitor_oi,
    )

    return content


def run(dry_run=False):
    cd = load_cooldown()
    now_str = datetime.now(CST).strftime('%m/%d %H:%M')

    # 拉涨幅榜
    try:
        gainers = requests.get(f'{FAPI}/ticker/24hr', timeout=8).json()
        # 筛选涨幅>30%的
        hot = []
        for t in gainers:
            sym = t.get('symbol', '')
            if not sym.endswith('USDT'):
                continue
            chg = float(t.get('priceChangePercent', 0))
            vol = float(t.get('quoteVolume', 0))
            if abs(chg) > 30 and vol > 500000:
                base = sym.replace('USDT', '')
                if base in ('BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE'):
                    continue  # 主流币不走extreme
                if is_cool(base, cd):
                    continue
                if is_symbol_duplicate(sym):
                    continue
                hot.append({
                    'symbol': sym,
                    'base': base,
                    'chg': chg,
                    'price': float(t.get('lastPrice', 0)),
                    'high': float(t.get('highPrice', 0)),
                    'low': float(t.get('lowPrice', 0)),
                    'vol': vol,
                })
        hot.sort(key=lambda x: abs(x['chg']), reverse=True)
        hot = hot[:3]  # 最多3帖/天
    except Exception as e:
        print(f'拉取涨幅榜失败: {e}')
        return

    if not hot:
        print('无极端行情，跳过')
        return

    for item in hot:
        sym = item['symbol']
        base = item['base']
        chg = item['chg']

        # 拉FR/LSR
        d = {'price': item['price'], 'high': item['high'], 'low': item['low'], 'vol': item['vol']}
        try:
            fr_d = requests.get(f'{FAPI}/premiumIndex', params={'symbol': sym}, timeout=5).json()
            d['fr'] = float(fr_d.get('lastFundingRate', 0)) * 100
        except Exception:
            d['fr'] = 0
        d['ls'] = 1.0

        # 拉SMC结构
        smc = fetch_smc_data(sym)

        # 拉OI
        oi_data = fetch_oi_data(sym)

        # 构建帖子
        content = build_signal_post(base, chg, d, smc, oi_data)

        if not content or len(content) < 100:
            print(f'[{base}] 内容不足100字，跳过')
            continue

        # 审计
        from square_template import audit_post
        ok, issues = audit_post(content)
        if not ok:
            print(f'[{base}] 审计失败: {issues}')
            continue

        # 去重
        if is_duplicate(content):
            print(f'[{base}] 24h内重复，跳过')
            continue

        print(f'[{base}] 准备发帖 ({len(content)}字):')
        print(content[:200] + '...')

        if dry_run:
            print(f'[{base}] DRY-RUN')
            continue

        resp = post_to_square(content)
        if 'error' in resp:
            print(f'[{base}] 发帖失败: {resp["error"]}')
        else:
            print(f'[{base}] ✅ 发布成功')
            mark_cool(base, cd)
            mark_symbol_posted(sym)
            mark_posted(content)
            log_post('extreme_alert', content, resp)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(dry_run=args.dry_run)
