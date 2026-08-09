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
            # [SSOT修复 2026-08-05 设计院封印] 废弃 regime_switch_state.json 权威地位
            # 根因: regime_switch_state.json 由已停用的 regime_switch_monitor 写入
            #       → 永久陈旧(79h+)，以它为权威导致体制倒退到旧值
            # 修复: 直接使用 market_state.analyze() 结果，不再读 regime_switch_state
            # regime_label 已由上方 market_state.analyze('BTCUSDT') 正确设置，无需覆盖
            _rss = {}  # 保留变量避免下方 _rss.get() 报错，但不再读文件
            pass  # regime_switch_state 已废弃为权威源
            _mom = _ms.get('momentum', {})
            _trend = _ms.get('trend', {})
            state['regime'] = _regime_label
            state['regime_label'] = _regime_label  # [FIX v25.6 2026-06-20] regime_label与regime保持一致，消除双字段冲突
            # [FIX 2026-08-02] 补写btc_regime/eth_regime兼容字段（brahma_dashboard/square_data_collector依赖）
            # [SSOT修复 2026-08-05] ETH 独立调用 market_state.analyze()
            # 修复根因: _rss={}空字典后 ETH会 fallback到 BTC体制 → 两者错误共享
            try:
                _ms_eth = _ms_analyze('ETHUSDT')
                _eth_regime_val = _ms_eth.get('regime', _regime_label)
            except Exception:
                _eth_regime_val = _regime_label
            state['btc_regime'] = _regime_label
            state['eth_regime'] = _eth_regime_val
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
            pass  # [静默]
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
        # [设计院 2026-08-09 苏摩111] 体制变化才推送，无变化静默
        try:
            _prev_state = {}
            if STATE_FILE.exists():
                _prev_state = json.loads(STATE_FILE.read_text())
            _prev_regime = _prev_state.get('regime', '')
            _new_regime  = state.get('regime', '')
            _regime_changed = _prev_regime != _new_regime
        except:
            _regime_changed = True  # 读取失败时保守推送

        if _regime_changed and _prev_regime:
            print(f'⚡ 体制切换: {_prev_regime} → {_new_regime} | BTC={btc:.0f} ETH={eth:.2f}')
        else:
            print('HEARTBEAT_OK')  # 体制无变化，静默

        # [P2-SSOT 2026-07-25 设计院] 同步三文件钩子（2026-08-05修复per-symbol独立体制）
        try:
            import time as _t
            _now_ts = _t.time()
            _rs_path = BASE / 'data' / 'regime_state.json'
            _rs = {}
            if _rs_path.exists():
                try: _rs = json.loads(_rs_path.read_text())
                except: pass

            # [修复2026-08-05] 各symbol独立读取实时体制，不再共用_regime_btc
                # [SSOT封印 2026-08-05 设计院] 废弃 _calc_regime 自定义算法
            # 使用主链路 market_state.analyze() 已计算的体制，与全系统 SSOT 一致
            _sym_regimes = {
                'BTCUSDT': _regime_label,
                'ETHUSDT': _eth_regime_val,
            }
            # 同步 regime_state.json（只更新regime/confirmed/ts，不覆盖其他字段）
            for _sym, _price in [('BTCUSDT', btc), ('ETHUSDT', eth)]:
                _r = _sym_regimes[_sym]
                if _sym not in _rs or not isinstance(_rs[_sym], dict):
                    _rs[_sym] = {}
                # 仅当旧值来源是state_refresh时才覆盖，手动修正不被覆盖
                _old_src = _rs[_sym].get('source','')
                _manual  = 'manual' in _old_src or 'correction' in _old_src
                if not _manual:
                    _rs[_sym].update({
                        'regime':    _r, 'confirmed': _r,
                        'price':     _price, 'ts': _now_ts,
                        'source':    'brahma_state_refresh_ssot',
                        'updated_at': _now_ts,
                    })
            _rs_tmp = str(_rs_path)+'.tmp'
            with open(_rs_tmp,'w') as _f: json.dump(_rs,_f,ensure_ascii=False,indent=2)
            os.replace(_rs_tmp, str(_rs_path))

            # 同步 brahma_state.json 体制字段
            _bs_path = BASE / 'data' / 'brahma_state.json'
            if _bs_path.exists():
                try:
                    _bs = json.loads(_bs_path.read_text())
                    _bs['regime']       = _sym_regimes['BTCUSDT']
                    _bs['regime_label'] = _sym_regimes['BTCUSDT']
                    _bs['btc_regime']   = _sym_regimes['BTCUSDT']
                    _bs['eth_regime']   = _sym_regimes['ETHUSDT']
                    _bs['_regime_sync_ts']  = _now_ts
                    _bs['_regime_sync_src'] = 'brahma_state_refresh'
                    _bs_tmp = str(_bs_path)+'.tmp'
                    with open(_bs_tmp,'w') as _f: json.dump(_bs,_f,ensure_ascii=False,indent=2)
                    os.replace(_bs_tmp, str(_bs_path))
                except Exception: pass

            # 同步 watcher_state.regime
            _ws_path = BASE / 'data' / 'btc_regime_watcher_state.json'
            if _ws_path.exists():
                try:
                    _ws = json.loads(_ws_path.read_text())
                    _ws['regime'] = _sym_regimes['BTCUSDT']
                    _ws['last_update_ts'] = _now_ts
                    _ws_tmp = str(_ws_path)+'.tmp'
                    with open(_ws_tmp,'w') as _f: json.dump(_ws,_f,ensure_ascii=False,indent=2)
                    os.replace(_ws_tmp, str(_ws_path))
                except Exception: pass

            # 同步 regime_bus
            try:
                import sys as _rbs3; _rbs3.path.insert(0, str(BASE/'scripts'))
                from regime_bus import update as _rb_upd3
                _rb_upd3('BTCUSDT', _sym_regimes['BTCUSDT'], 'CONFIRMED', 'brahma_state_refresh')
                _rb_upd3('ETHUSDT', _sym_regimes['ETHUSDT'], 'CONFIRMED', 'brahma_state_refresh')
            except Exception: pass

        except Exception as _rs_e:
            pass  # 静默失败，不影响主流程

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

