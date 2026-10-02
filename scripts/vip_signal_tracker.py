#!/usr/bin/env python3
"""
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
import json, time, sys
from pathlib import Path

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
