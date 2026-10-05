#!/usr/bin/env python3
"""
import sys as _sys, json as _json
from pathlib import Path as _Path
_vip_f = _Path(__file__).parent.parent / 'data' / 'vip_signal_state.json'
try:
    _vip = _json.loads(_vip_f.read_text()) if _vip_f.exists() else {}
    _has_vip = any(_vip.get(k) for k in ['btc_sl','eth_sl','btc_entry','eth_entry'])
    if not _has_vip:
        print('HEARTBEAT_OK (no vip strategy)', file=_sys.stderr)
        _sys.exit(0)
except Exception:
    pass


vip_signal_tracker.py — VIP策略版本追踪 + 止损墙漂移检测
[2026-10-02 苏摩111封印]

解决问题：
  止损墙更新后，旧版VIP挂单点位过期，苏摩不知道
  → 本脚本对比上次记录的liq点位，若漂移>0.5%则推送更替通知

设计：
  data/vip_signal_state.json — 持久化最近一次VIP点位
  每次sentinel或analysis运行后调用
  止损墙漂移 > 0.5% → push_hub CRITICAL推送「版本作废」警告

接入位置：
  scripts/structure_sentinel.py sense_btc_eth 末尾（每15min检查）
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json, time, sys
from pathlib import Path

# push_hub统一入口 [Fix 2026-10-03 苏摩111]
try:
    import push_hub
except ImportError:
    import sys as _ph_sys, pathlib as _ph_pl
    _ph_sys.path.insert(0, str(_ph_pl.Path(__file__).parent))
    import push_hub


_DATA   = Path(__file__).parent.parent / 'data'
_STATE  = _DATA / 'vip_signal_state.json'
_DRIFT_THRESHOLD = 0.005   # 0.5% 漂移触发更替通知

def load_state() -> dict:
    try:
        return json.loads(_STATE.read_text()) if _STATE.exists() else {}
    except Exception:
        return {}

def save_state(state: dict) -> None:
    try:
        tmp = _STATE.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        tmp.rename(_STATE)
    except Exception as e:
        print(f'[vip_tracker] 保存失败: {e}', file=sys.stderr)

def check_liq_drift(sym: str) -> dict:
    """
    检查止损墙/支撑池是否漂移超过阈值
    返回 {'drifted': bool, 'sym': sym, 'old_wall': float, 'new_wall': float, 'drift_pct': float}
    """
    sym_l = sym.lower()
    liq_f = _DATA / f'liq_heatmap_{sym_l}usdt.json'
    if not liq_f.exists():
        return {'drifted': False}

    try:
        liq   = json.loads(liq_f.read_text())
        new_wall = float(liq.get('nearest_short_liq', 0) or 0)
        new_pool = float(liq.get('nearest_long_liq',  0) or 0)
        liq_ts   = float(liq.get('updated_at', time.time()) or time.time())
    except Exception:
        return {'drifted': False}

    state = load_state()
    key   = sym.upper()
    old   = state.get(key, {})
    old_wall = float(old.get('wall', 0) or 0)
    old_pool = float(old.get('pool', 0) or 0)

    drift_wall = abs(new_wall - old_wall) / old_wall if old_wall > 0 else 0
    drift_pool = abs(new_pool - old_pool) / old_pool if old_pool > 0 else 0
    max_drift  = max(drift_wall, drift_pool)

    # 更新state
    state[key] = {
        'wall':    new_wall,
        'pool':    new_pool,
        'ts':      liq_ts,
        'updated': time.time(),
    }
    save_state(state)

    if old_wall == 0:
        return {'drifted': False, 'first_run': True}

    return {
        'drifted':    max_drift >= _DRIFT_THRESHOLD,
        'sym':        sym.upper(),
        'old_wall':   old_wall,
        'new_wall':   new_wall,
        'old_pool':   old_pool,
        'new_pool':   new_pool,
        'drift_wall': drift_wall * 100,
        'drift_pool': drift_pool * 100,
        'max_drift':  max_drift * 100,
    }

def push_drift_alert(result: dict) -> None:
    """止损墙漂移 → push_hub 推送版本作废通知"""
    sym  = result['sym']
    ts   = time.strftime('%H:%M UTC', time.gmtime())
    wall_dir = '⬆️' if result['new_wall'] > result['old_wall'] else '⬇️'
    pool_dir = '⬆️' if result['new_pool'] > result['old_pool'] else '⬇️'

    msg = (
        f'📍 **{sym} VIP点位更替** | {ts}\n\n'
        f'止损墙 {wall_dir} ${result["old_wall"]:,.0f} → ${result["new_wall"]:,.0f}'
        f'（漂移{result["drift_wall"]:.1f}%）\n'
        f'支撑池 {pool_dir} ${result["old_pool"]:,.0f} → ${result["new_pool"]:,.0f}'
        f'（漂移{result["drift_pool"]:.1f}%）\n\n'
        f'⚠️ **旧版VIP挂单点位已过期**\n'
        f'请用新点位重新挂单或发 `分析{sym[:3]}` 获取最新VIP策略'
    )

    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location('push_hub',
            Path(__file__).parent / 'push_hub.py')
        _ph = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_ph)
        _ph.push_jarvis(msg, priority='P1',
                        dedup_key=f'liq_drift_{sym}_{int(time.time()//3600)}',
                        dedup_ttl=3600)
        print(f'[vip_tracker] {sym} 漂移{result["max_drift"]:.1f}% 推送成功', flush=True)
    except Exception as e:
        print(f'[vip_tracker] 推送失败: {e}', file=sys.stderr)

# ── VIP战绩帖自动出稿 [2026-10-03 苏摩111] ──────────────────────────────
_TRADE_STATE = _DATA / 'vip_trade_state.json'


def load_trade_state() -> dict:
    try:
        return json.loads(_TRADE_STATE.read_text()) if _TRADE_STATE.exists() else {}
    except Exception:
        return {}


def save_trade_state(state: dict) -> None:
    _TRADE_STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def check_trade_result(sym: str, entry: float, sl: float, tp1: float,
                       direction: str) -> dict:
    """
    检查持仓中的VIP信号是否出现止盈或止据结果。
    返回: {'result': 'win'|'loss'|'running', 'pnl_pct': float}
    """
    try:
        import urllib.request, ssl, json as _j
        _ctx = ssl.create_default_context()
        sym_u = sym.upper() + 'USDT' if not sym.endswith('USDT') else sym.upper()
        # [Fix 2026-10-03] brahma_http统一入口
        _sym_price = None
        try:
            from brahma_http import fetch as _bfetch2
            _pr = _bfetch2(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym_u}', timeout=5)
            _sym_price = float(_pr['price']) if _pr else None
        except Exception:
            pass  # [WARN] vip_signal_tracker: brahma_http fallback
        if _sym_price is None:
            r = _j.loads(urllib.request.urlopen(
                f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym_u}',
                timeout=5, context=_ssl_ctx).read())
            _sym_price = float(r['price'])
        price = float(r.get('price', 0))
    except Exception:
        return {'result': 'running', 'pnl_pct': 0.0}

    if direction == 'SHORT':
        pnl_pct = (entry - price) / entry * 100
        if price <= tp1 and tp1 > 0:
            return {'result': 'win', 'pnl_pct': pnl_pct, 'price': price}
        if price >= sl and sl > 0:
            return {'result': 'loss', 'pnl_pct': pnl_pct, 'price': price}
    else:  # LONG
        pnl_pct = (price - entry) / entry * 100
        if price >= tp1 and tp1 > 0:
            return {'result': 'win', 'pnl_pct': pnl_pct, 'price': price}
        if price <= sl and sl > 0:
            return {'result': 'loss', 'pnl_pct': pnl_pct, 'price': price}
    return {'result': 'running', 'pnl_pct': pnl_pct if 'pnl_pct' in dir() else 0.0, 'price': price}


def generate_trade_result_post(sym: str, direction: str, entry: float,
                               exit_price: float, pnl_pct: float,
                               result: str) -> str:
    """自动生成战绩帖草稿（人工确认后发布）"""
    from datetime import datetime, timezone, timedelta
    cst = timezone(timedelta(hours=8))
    date_str = datetime.now(cst).strftime('%m/%d %H:%M')
    arrow = '🔴' if direction == 'SHORT' else '🟢'
    side_cn = '空单' if direction == 'SHORT' else '多单'
    result_cn = '止盈封单' if result == 'win' else '止据出场'
    sign = '+' if pnl_pct > 0 else ''

    lines = [
        f'{arrow} {sym} {side_cn}实盘战绩 | {date_str} CST',
        '',
        f'开仓: ${entry:,.2f}',
        f'出场: ${exit_price:,.2f}',
        f'此单浮盈: {sign}{pnl_pct:.2f}%（{result_cn}）',
        '',
        '策略故事：',
        f'止据墙区域上方挂空，OI全周期 SHORT_BUILD，散户多头拥挤→主力猾杀目标。',
        '系统找到共振点入场，硬止据出。',
        '',
        '🔗 www.bsmkweb.cc/register?ref=XZBX666',
        '🌿 姓赵不宣 | 不是建议',
        '#实盘战绩 #合约交易 #BTC',
    ]
    return '\n'.join(lines)


def run(symbols=None) -> None:
    if symbols is None:
        symbols = ['BTC', 'ETH']
    for sym in symbols:
        r = check_liq_drift(sym)
        if r.get('drifted'):
            push_drift_alert(r)
        elif r.get('first_run'):
            print(f'[vip_tracker] {sym} 首次记录 wall=${r.get("new_wall",0):,.0f}', flush=True)
        else:
            print(f'[vip_tracker] {sym} 漂移{r.get("max_drift",0):.2f}% < {_DRIFT_THRESHOLD*100:.1f}% STABLE', flush=True)

if __name__ == '__main__':
    run()