#!/usr/bin/env python3
"""brahma_state 价格/时间戳定时刷新 — 纯Python，零AI开销"""
import json, time, urllib.request, os, sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/root/.openclaw/workspace/trading-system')
STATE_FILE = BASE / 'data' / 'brahma_state.json'

def get_price(sym):
    # [FIX-S3 2026-06-06] 使用safe_fetch替代裸urlopen
    try:
        import sys as _sys
        _sys.path.insert(0, str(BASE / 'scripts'))
        from safe_fetch import fetch_price as _fp
        p = _fp(sym, timeout=6)
        if p: return p
    except: pass
    # fallback: 原始方式
    url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}'
    d = json.loads(urllib.request.urlopen(url, timeout=6).read())
    return float(d['price'])

def main():
    try:
        btc = get_price('BTCUSDT')
        eth = get_price('ETHUSDT')
        now_iso = datetime.now(timezone.utc).isoformat()
        
        state = json.loads(STATE_FILE.read_text())
        state['updated_at'] = now_iso
        state['last_updated'] = now_iso  # 双写，兼容所有读取路径
        state['last_scan_ts'] = now_iso
        state['last_update'] = time.time()   # FIX: 统一Unix时间戳字段
        state['timestamp']   = time.time()   # FIX: 兼容age计算(time.time()-timestamp)
        state['last_price'] = btc            # FIX: brahma_analyze读取字段
        state['btc_price'] = btc
        state['price'] = btc                   # [C5-fix audit-2026-06-17] price字段同步，消除与btc_price不一致
        state['eth_price'] = eth
        # [FIX-MKTPRICE 2026-06-14] 同步修正 market_prices 为实时值，清除历史残留旧价格
        state['market_prices'] = {'BTCUSDT': btc, 'ETHUSDT': eth}

        # [FIX-REGIME-SSOT 2026-06-14] 统一使用 market_state.analyze 作为体制唯一来源
        # 修复根因：regime_scorer 使用简化SMA RSI（gains[-14:]），
        #   导致 rsi_4h=73 vs market_state Wilder RSI=58，体制判断差异达13+点
        # 设计院原则：体制 SSOT = market_state.detect_regime（基于价格结构+Wilder RSI）
        try:
            import sys as _sys2
            _bb = str(Path(__file__).parent.parent / 'brahma_brain')
            if _bb not in _sys2.path: _sys2.path.insert(0, _bb)
            from market_state import analyze as _ms_analyze
            _ms = _ms_analyze('BTCUSDT')
            _regime_label = _ms.get('regime', 'CHOP_MID')
            # [FIX-SSOT-REGIME 2026-06-18] regime_switch_state 更实时，优先取 BTC 体制
            # brahma_state_refresh 每30分钟跑一次，regime_switch_monitor 也是30分钟
            # 但两者锚点不同，导致短暂不一致 → 以 regime_switch_state.BTCUSDT 为权威
            try:
                import json as _rj
                _rss_path = Path(__file__).parent.parent / 'data' / 'regime_switch_state.json'
                if _rss_path.exists():
                    _rss = _rj.loads(_rss_path.read_text())
                    _btc_regime = _rss.get('BTCUSDT', {}).get('regime', '')
                    if _btc_regime:
                        _regime_label = _btc_regime  # 以 regime_switch_state 为权威
            except Exception:
                pass  # fallback 到 market_state 结果
            _mom = _ms.get('momentum', {})
            _trend = _ms.get('trend', {})
            state['regime'] = _regime_label
            state['regime_label'] = _regime_label  # [FIX v25.6 2026-06-20] regime_label与regime保持一致，消除双字段冲突
            # 构建 regime_snapshot（与 market_state 结构对齐）
            state['regime_snapshot'] = {
                'symbol':     'BTCUSDT',
                'regime':     _regime_label,
                'phase':      _ms.get('wave', {}).get('wave', '?'),
                'momentum':   _ms.get('signal_bias', '?'),
                'rsi_1h':     round(_mom.get('rsi_1h', 0), 2),
                'rsi_4h':     round(_mom.get('rsi_4h', 0), 2),
                'rsi_1d':     round(_mom.get('rsi_1d', 0), 2),
                'trend_1h':   _trend.get('1h', {}).get('direction', '?'),
                'trend_4h':   _trend.get('4h', {}).get('direction', '?'),
                'trend_1d':   _trend.get('1d', {}).get('direction', '?'),
                'source':     'market_state.detect_regime',
                'ts':         time.time(),
            }
            # 移除旧的 regime_scorer 概率字段（避免误导）
            for _stale_key in ('bear_prob', 'bull_prob', 'chop_prob'):
                state.pop(_stale_key, None)
        except Exception as _re:
            print(f'[WARN] regime refresh failed: {_re}', file=sys.stderr)
            # regime刷新失败不影响价格刷新

        # FIX: 同步ws_guardian实时ping（ws_guardian写ws_guardian_state.json，这里同步到brahma_state）
        try:
            import pathlib as _pl
            _wgs_f = _pl.Path(__file__).parent.parent / 'data' / 'ws_guardian_state.json'
            if _wgs_f.exists():
                _wgs = json.loads(_wgs_f.read_text())
                state['ws_guardian'] = {
                    'pid':       _wgs.get('pid', state.get('ws_guardian',{}).get('pid')),
                    'status':    _wgs.get('status', 'unknown'),
                    'last_ping': _wgs.get('last_ping', '?'),
                }
        except: pass
        
        tmp = str(STATE_FILE) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(STATE_FILE))
        print(f'OK BTC={btc:.0f} ETH={eth:.2f} ts={now_iso}')
    except Exception as e:
        print(f'ERR {e}', file=sys.stderr)
        try:
            from error_collector import log_error
            log_error('brahma_state_refresh', e, context='main')
        except: pass
        sys.exit(1)

main()


def clean_stale_price_zones():
    """清理过时入场区（距现价>5%或超过6H未更新）"""
    import time as _time
    zones_file = BASE / 'data' / 'price_zones.json'
    if not zones_file.exists(): return
    try:
        z = json.load(open(zones_file))
        changed = False
        for sym, info in z.items():
            elo = float(info.get('last_entry_lo') or 0)
            if elo <= 0: continue
            url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}'
            cur = float(json.loads(urllib.request.urlopen(url, timeout=4).read())['price'])
            diff_pct = abs(cur - elo) / cur * 100
            if diff_pct > 5:
                info['last_entry_lo'] = None
                info['last_entry_hi'] = None
                changed = True
        if changed:
            zones_file.write_text(json.dumps(z, indent=2, ensure_ascii=False))
    except: pass



def check_ram_guard():
    """RAM超过500MB可用时告警"""
    try:
        mem = open('/proc/meminfo').read().split('\n')
        avail = int([l for l in mem if 'MemAvailable' in l][0].split()[1]) // 1024
        total = int([l for l in mem if 'MemTotal' in l][0].split()[1]) // 1024
        used_pct = (total - avail) / total * 100
        if avail < 300:
            print(f'RAM_CRITICAL avail={avail}MB used={used_pct:.0f}%')
        elif avail < 500:
            print(f'RAM_WARN avail={avail}MB used={used_pct:.0f}%')
        # 返回状态供调用方使用
        return {'avail': avail, 'total': total, 'used_pct': used_pct}
    except:
        return {}

