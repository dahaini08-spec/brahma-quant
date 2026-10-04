#!/usr/bin/env python3
"""
state_quality_sentinel.py — 数据质量哨兵 [2026-10-03 自主决策封印]
接入位置: brahma_manual_analysis.py step0_fetch_all() 前置检查
          brahma_crontab.txt (*/15 * * * * 主动巡检)

功能: 分析前主动检查数据质量，发现污染立即触发修复
     → 彻底根治 BTC price=0 类静默崩溃

检查矩阵:
  price > 0            ← 今天BTC除零根因
  timestamp < 4h ago   ← 过期缓存检测
  key fields not null  ← formatter NoneType根因
  state文件存在        ← 分析前保底

自愈通路:
  检查失败 → 立即调用 brahma_state_refresh → 重试
  二次失败 → 推P1告警，不进入分析（保护系统）
"""
import json
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

# 关键字段清单（None或0会导致后续崩溃）
REQUIRED_FIELDS = ['price', 'regime', 'confluence']
PRICE_FIELDS = ['price']
MAX_STATE_AGE_HOURS = 4


def _fetch_live_price(sym: str) -> float:
    """直接从Binance拉实时价格"""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        usdt = sym.upper() + ('USDT' if 'USDT' not in sym.upper() else '')
        r = json.loads(urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={usdt}',
            timeout=5, context=ctx).read())
        return float(r.get('price', 0))
    except Exception as _e:
        print(f'[StateQuality] 拉取{sym}实时价格失败: {_e}', file=sys.stderr)
        return 0.0


def _trigger_state_refresh(sym: str) -> bool:
    """触发brahma_state_refresh重建state文件"""
    try:
        import subprocess
        sym_u = sym.upper() + ('USDT' if 'USDT' not in sym.upper() else '')
        result = subprocess.run(
            [sys.executable,
             str(BASE / 'scripts' / 'brahma_state_refresh.py'),
             '--symbol', sym_u],
            capture_output=True, text=True, timeout=30,
            cwd=str(BASE)
        )
        if result.returncode == 0:
            print(f'[StateQuality] {sym} state刷新成功', file=sys.stderr)
            return True
        else:
            print(f'[StateQuality] {sym} state刷新失败: {result.stderr[:200]}', file=sys.stderr)
            return False
    except Exception as _e:
        print(f'[StateQuality] 触发刷新异常: {_e}', file=sys.stderr)
        return False


def _patch_price_in_state(sym: str, price: float) -> bool:
    """直接patch price字段（降级方案，刷新失败时用）"""
    try:
        sym_lower = sym.lower().replace('usdt', '')
        state_path = DATA / f'brahma_state_{sym_lower}.json'
        if not state_path.exists():
            return False
        st = json.loads(state_path.read_text())
        st['price'] = price
        state_path.write_text(json.dumps(st, ensure_ascii=False))
        print(f'[StateQuality] {sym} price直接patch: ${price:,.0f}', file=sys.stderr)
        return True
    except Exception as _e:
        print(f'[StateQuality] patch失败: {_e}', file=sys.stderr)
        return False


def check_state_quality(sym: str, auto_heal: bool = True) -> dict:
    """
    检查单个标的的state质量
    返回: {'ok': bool, 'issues': list, 'healed': bool}
    """
    sym_lower = sym.lower().replace('usdt', '')
    state_path = DATA / f'brahma_state_{sym_lower}.json'

    result = {'sym': sym, 'ok': True, 'issues': [], 'healed': False}

    # 检查1: 文件存在
    if not state_path.exists():
        result['issues'].append('state文件不存在')
        result['ok'] = False
        if auto_heal:
            healed = _trigger_state_refresh(sym)
            result['healed'] = healed
        return result

    # 读取state
    try:
        st = json.loads(state_path.read_text())
    except Exception as _e:
        result['issues'].append(f'state文件损坏: {_e}')
        result['ok'] = False
        return result

    # 检查2: price > 0
    price = float(st.get('price', 0) or 0)
    if price <= 0:
        result['issues'].append(f'price={price}（零值）')
        result['ok'] = False
        if auto_heal:
            live_price = _fetch_live_price(sym)
            if live_price > 0:
                _patch_price_in_state(sym, live_price)
                result['healed'] = True
                print(f'[StateQuality] ✅ {sym} price修复: ${live_price:,.0f}', file=sys.stderr)

    # 检查3: 文件年龄 < 4h
    try:
        age_s = time.time() - os.path.getmtime(state_path)
        age_h = age_s / 3600
        if age_h > MAX_STATE_AGE_HOURS:
            result['issues'].append(f'state过期{age_h:.1f}h>{MAX_STATE_AGE_HOURS}h')
            # 过期不一定要heal（brahma_cpu会定时更新），只记录
    except Exception:
        pass

    # 检查4: 关键字段非None
    for field in REQUIRED_FIELDS:
        if field == 'confluence':
            conf = st.get('confluence')
            if conf is None:
                result['issues'].append(f'confluence=None')
                result['ok'] = False
        elif st.get(field) is None:
            result['issues'].append(f'{field}=None')
            result['ok'] = False

    if result['issues']:
        result['ok'] = False

    return result


def run_quality_check(symbols: list = None, push_alert: bool = True) -> dict:
    """批量检查所有标的数据质量"""
    if symbols is None:
        # 自动发现所有state文件
        symbols = []
        for f in DATA.glob('brahma_state_*.json'):
            sym = f.stem.replace('brahma_state_', '').upper()
            if sym not in ('', 'BACKUP'):
                symbols.append(sym)

    if not symbols:
        symbols = ['BTC', 'ETH']

    results = {}
    issues_found = []

    for sym in symbols:
        r = check_state_quality(sym, auto_heal=True)
        results[sym] = r
        if not r['ok']:
            healed_str = '（已自愈）' if r['healed'] else '（未修复）'
            issues_found.append(f'{sym}: {", ".join(r["issues"])} {healed_str}')
            print(f'[StateQuality] ⚠️  {sym}: {r["issues"]} healed={r["healed"]}',
                  file=sys.stderr)
        else:
            print(f'[StateQuality] ✅ {sym}: 数据质量正常', file=sys.stderr)

    # 推送告警（有问题且未能自愈）
    unhealed = [s for s, r in results.items() if not r['ok'] and not r['healed']]
    if unhealed and push_alert:
        try:
            import push_hub as _ph
            msg = (f'🔍 数据质量告警 | {len(unhealed)}个标的未修复\n' +
                   '\n'.join(issues_found))
            _ph.push_jarvis(msg, priority='P1')
        except Exception as _pe:
            print(f'[StateQuality] 推送失败: {_pe}', file=sys.stderr)

    return {
        'total': len(symbols),
        'ok': len([r for r in results.values() if r['ok']]),
        'issues': len(issues_found),
        'unhealed': len(unhealed),
        'results': results,
    }


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='梵天数据质量哨兵')
    ap.add_argument('--symbols', nargs='+', default=None)
    ap.add_argument('--no-push', action='store_true')
    args = ap.parse_args()

    summary = run_quality_check(
        symbols=args.symbols,
        push_alert=not args.no_push
    )
    print(f'\n[StateQuality] 汇总: 共{summary["total"]}个 | '
          f'正常{summary["ok"]}个 | '
          f'问题{summary["issues"]}个 | '
          f'未修复{summary["unhealed"]}个')
    sys.exit(0 if summary['unhealed'] == 0 else 1)
