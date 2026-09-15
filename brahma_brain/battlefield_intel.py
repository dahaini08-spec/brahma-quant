#!/usr/bin/env python3
"""
battlefield_intel.py — 统一战场情报中心
2026-09-14 苏摩111 三方联合封印

所有扫描器运行后将结果写入 data/battlefield_intel.json
分析引擎按需读取，消除数据孤岛。

数据结构:
{
  "BTCUSDT": {
    "cvd_1h": -12,           # CVD collector
    "liq_stop_wall": 79022,  # liq_heatmap
    "liq_support_pool": 75923,
    "rsi_15m": 63,           # rsi_structure_watcher
    "rsi_signal": "NEUTRAL",
    "breakout_signal": "NONE", # breakout_watch
    "oi_class": "B",           # oi_advanced_scanner
    "oi_signal": "SHORT_BUILD",
    "screener_score": 0,       # brahma_screener
    "square_extreme": false,   # square_extreme_alert
    "last_update": "2026-09-14T07:08:00Z",
    "last_source": "cvd"       # 最后写入的来源
  },
  ...
}
"""
import json, os, time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
INTEL_PATH = BASE / 'data' / 'battlefield_intel.json'

# 写锁（防止并发写冲突）
_WRITE_LOCK = None

def _load_intel():
    """加载现有intel数据"""
    try:
        if INTEL_PATH.exists():
            return json.loads(INTEL_PATH.read_text())
    except (json.JSONDecodeError, IOError):
        pass
    return {}

def _save_intel(data):
    """保存intel数据（原子写入）"""
    INTEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(INTEL_PATH) + '.tmp'
    with open(tmp_path, 'w') as f:
        f.write(json.dumps(data, ensure_ascii=False, default=str))
    os.replace(tmp_path, str(INTEL_PATH))

def update_symbol(symbol: str, source: str, **fields):
    """
    更新单个标的的情报字段
    
    Args:
        symbol: 标的符号 (如 "BTCUSDT")
        source: 数据来源 (如 "cvd", "liq_heatmap", "rsi_watcher")
        **fields: 要更新的字段
    """
    data = _load_intel()
    if symbol not in data:
        data[symbol] = {}
    data[symbol].update(fields)
    data[symbol]['last_update'] = datetime.now(timezone.utc).isoformat()
    data[symbol]['last_source'] = source
    _save_intel(data)

def update_batch(source: str, updates: dict):
    """
    批量更新多个标的的情报
    
    Args:
        source: 数据来源
        updates: {symbol: {field: value, ...}, ...}
    """
    data = _load_intel()
    now = datetime.now(timezone.utc).isoformat()
    for symbol, fields in updates.items():
        if symbol not in data:
            data[symbol] = {}
        data[symbol].update(fields)
        data[symbol]['last_update'] = now
        data[symbol]['last_source'] = source
    _save_intel(data)

def get_symbol(symbol: str) -> dict:
    """获取单个标的的情报"""
    data = _load_intel()
    return data.get(symbol, {})

def get_all() -> dict:
    """获取全部情报"""
    return _load_intel()

def get_symbols_with_data(min_fields: int = 1) -> list:
    """获取至少有min_fields个字段的标的列表"""
    data = _load_intel()
    result = []
    for sym, fields in data.items():
        # 排除meta字段
        real_fields = {k: v for k, v in fields.items() 
                       if k not in ('last_update', 'last_source') and v is not None}
        if len(real_fields) >= min_fields:
            result.append(sym)
    return sorted(result)

def get_coverage_report() -> dict:
    """获取覆盖报告"""
    data = _load_intel()
    total = len(data)
    by_field = {}
    for sym, fields in data.items():
        for k, v in fields.items():
            if k in ('last_update', 'last_source'):
                continue
            if v is not None:
                by_field[k] = by_field.get(k, 0) + 1
    return {
        'total_symbols': total,
        'by_field': by_field,
        'market_total': 718,
        'coverage_pct': total / 718 * 100 if total > 0 else 0,
    }

def merge_cvd_snapshots():
    """从CVD快照文件合并到intel"""
    updates = {}
    for f in os.listdir(BASE / 'data'):
        if f.startswith('cvd_realtime_') and f.endswith('.json'):
            sym = f.replace('cvd_realtime_', '').replace('.json', '').upper()
            try:
                with open(BASE / 'data' / f) as fh:
                    d = json.load(fh)
                cvd_1h = d.get('cvd_1h')
                if cvd_1h is not None:
                    updates[sym] = {'cvd_1h': cvd_1h}
            except:
                pass
    if updates:
        update_batch('cvd', updates)
    return len(updates)

def merge_liq_heatmaps():
    """从清算热图文件合并到intel"""
    updates = {}
    for f in os.listdir(BASE / 'data'):
        if f.startswith('liq_heatmap_') and f.endswith('.json'):
            sym = f.replace('liq_heatmap_', '').replace('.json', '').upper()
            try:
                with open(BASE / 'data' / f) as fh:
                    d = json.load(fh)
                fields = {}
                stop_wall = d.get('nearest_short_liq') or d.get('stop_wall') or d.get('nearest_short')
                support_pool = d.get('nearest_long_liq') or d.get('support_pool') or d.get('nearest_long')
                if stop_wall:
                    fields['liq_stop_wall'] = float(stop_wall)
                if support_pool:
                    fields['liq_support_pool'] = float(support_pool)
                bull_score = d.get('liq_bull_score')
                if bull_score is not None:
                    fields['liq_bull_score'] = int(bull_score)
                bear_score = d.get('liq_bear_score')
                if bear_score is not None:
                    fields['liq_bear_score'] = int(bear_score)
                if fields:
                    updates[sym] = fields
            except:
                pass
    if updates:
        update_batch('liq_heatmap', updates)
    return len(updates)

def merge_scan_candidates():
    """从screener候选文件合并到intel"""
    updates = {}
    cand_path = BASE / 'data' / 'scan_candidates.json'
    if cand_path.exists():
        try:
            d = json.loads(cand_path.read_text())
            for c in d.get('candidates', []):
                sym = c.get('symbol', '')
                if sym:
                    updates[sym] = {'screener_score': c.get('score', 0)}
        except:
            pass
    if updates:
        update_batch('screener', updates)
    return len(updates)

def merge_rsi_watcher():
    """从rsi_structure_watcher状态文件合并到intel"""
    updates = {}
    state_path = BASE / 'data' / 'rsi_watcher_state.json'
    if state_path.exists():
        try:
            d = json.loads(state_path.read_text())
            if isinstance(d, dict):
                # 扁平格式: {"BTCUSDT_rsi": 42.1, "BTCUSDT_bb": 0.682, ...}
                for key, val in d.items():
                    if not isinstance(val, (int, float)):
                        continue
                    # 解析symbol和field
                    if key.endswith('_rsi'):
                        sym = key[:-4].upper()
                        if sym not in updates:
                            updates[sym] = {}
                        updates[sym]['rsi_15m'] = float(val)
                        # RSI信号分类
                        if val < 30:
                            updates[sym]['rsi_signal'] = 'OVERSOLD'
                        elif val > 70:
                            updates[sym]['rsi_signal'] = 'OVERBOUGHT'
                        elif val < 45:
                            updates[sym]['rsi_signal'] = 'BEARISH'
                        elif val > 55:
                            updates[sym]['rsi_signal'] = 'BULLISH'
                        else:
                            updates[sym]['rsi_signal'] = 'NEUTRAL'
                    elif key.endswith('_bb'):
                        sym = key[:-3].upper()
                        if sym not in updates:
                            updates[sym] = {}
                        updates[sym]['bb_width'] = float(val)
        except Exception as e:
            import sys; print(f'rsi_watcher merge error: {e}', file=sys.stderr)
    # trigger events - 高优先事件
    trigger_path = BASE / 'data' / 'rsi_trigger_event.json'
    if trigger_path.exists():
        try:
            d = json.loads(trigger_path.read_text())
            if isinstance(d, dict):
                for sym, v in d.items():
                    if not isinstance(v, dict):
                        continue
                    if sym not in updates:
                        updates[sym] = {}
                    # 高优先级事件
                    if v.get('high_priority'):
                        events = v.get('events', [])
                        event_names = [e.get('event','') for e in events if isinstance(e, dict)]
                        if event_names:
                            updates[sym]['rsi_trigger'] = event_names[0]
                    # RSI 1h值
                    rsi_1h = v.get('rsi_1h')
                    if rsi_1h is not None and 'rsi_15m' not in updates.get(sym,{}):
                        updates[sym]['rsi_15m'] = float(rsi_1h)
        except:
            pass
    if updates:
        update_batch('rsi_watcher', updates)
    return len(updates)

def merge_oi_signals():
    """从oi_advanced_scanner jsonl合并到intel"""
    updates = {}
    signals_path = BASE / 'data' / 'oi_advanced_signals.jsonl'
    if signals_path.exists():
        try:
            with open(signals_path) as f:
                for line in f:
                    try:
                        d = json.loads(line.strip())
                        sym = d.get('symbol', '')
                        if not sym:
                            continue
                        fields = {}
                        # direction字段 = OI信号方向(LONG_BUILD/SHORT_BUILD/SHORT_COVER)
                        direction = d.get('direction')
                        if direction:
                            fields['oi_signal'] = str(direction)
                        oi_score = d.get('oi_score')
                        if oi_score is not None:
                            fields['oi_score'] = float(oi_score)
                        chg_1h = d.get('chg_1h')
                        if chg_1h is not None:
                            fields['oi_chg_1h'] = float(chg_1h)
                        # 额外有用字段
                        rsi_1h = d.get('rsi_1h')
                        if rsi_1h is not None:
                            fields['oi_rsi_1h'] = float(rsi_1h)
                        whale_l = d.get('whale_l')
                        if whale_l is not None:
                            fields['oi_whale_l'] = float(whale_l)
                        regime = d.get('regime')
                        if regime:
                            fields['oi_regime'] = str(regime)
                        action = d.get('action')
                        if action:
                            fields['oi_action'] = str(action)
                        if fields:
                            # jsonl追加写，取最新条目覆盖
                            updates[sym] = fields
                    except:
                        pass
        except:
            pass
    if updates:
        update_batch('oi_scanner', updates)
    return len(updates)

def merge_whale_monitor():
    """从whale_monitor文件合并到intel"""
    updates = {}
    data_dir = BASE / 'data'
    for f in os.listdir(data_dir):
        if not f.startswith('whale_') or not f.endswith('.json'):
            continue
        sym = f.replace('whale_', '').replace('.json', '').upper()
        try:
            with open(data_dir / f) as fh:
                d = json.load(fh)
            fields = {}
            whale_dir = d.get('whale_direction') or d.get('direction') or d.get('side')
            if whale_dir:
                fields['whale_direction'] = str(whale_dir)
            whale_net = d.get('whale_net_usd') or d.get('net_usd')
            if whale_net is not None:
                try:
                    fields['whale_net_usd'] = float(whale_net)
                except:
                    pass
            whale_score = d.get('whale_score')
            if whale_score is not None:
                fields['whale_score'] = int(whale_score)
            whale_events = d.get('whale_event_count') or d.get('event_count')
            if whale_events is not None:
                fields['whale_event_count'] = int(whale_events)
            whale_ls = d.get('whale_ls_ratio')
            if whale_ls is not None:
                try:
                    fields['whale_ls_ratio'] = float(whale_ls)
                except:
                    pass
            whale_oi = d.get('oi_signal')
            if whale_oi:
                fields['whale_oi_signal'] = str(whale_oi)
            if fields:
                updates[sym] = fields
        except:
            pass
    if updates:
        update_batch('whale_monitor', updates)
    return len(updates)

def merge_breakout_watch():
    """从breakout_watch文件合并到intel"""
    updates = {}
    bw_path = BASE / 'data' / 'breakout_watch_latest.json'
    if bw_path.exists():
        try:
            d = json.loads(bw_path.read_text())
            # results = 被监控的标的列表
            results = d.get('results', [])
            alerts = d.get('alerts', [])
            # alerts里的标的有突破信号
            alert_syms = set()
            for a in alerts:
                if isinstance(a, dict):
                    sym = a.get('symbol', '')
                    if sym:
                        alert_syms.add(sym)
                        fields = {'breakout_signal': a.get('type', 'BREAKOUT')}
                        direction = a.get('direction')
                        if direction:
                            fields['breakout_direction'] = str(direction)
                        updates[sym] = fields
                elif isinstance(a, str):
                    alert_syms.add(a)
                    updates[a] = {'breakout_signal': 'BREAKOUT'}
            # 被监控但无告警的标的标记为WATCH
            for sym in results:
                if sym not in alert_syms:
                    updates[sym] = {'breakout_signal': 'WATCH'}
        except:
            pass
    if updates:
        update_batch('breakout_watch', updates)
    return len(updates)

def merge_square_extreme():
    """从square_extreme_alert文件合并到intel"""
    updates = {}
    # 查找square相关文件
    for f in os.listdir(BASE / 'data'):
        if 'square' in f.lower() and f.endswith('.json'):
            try:
                with open(BASE / 'data' / f) as fh:
                    d = json.load(fh)
                if isinstance(d, list):
                    for item in d:
                        if isinstance(item, dict):
                            sym = item.get('symbol', '')
                            if sym:
                                updates[sym] = {
                                    'square_extreme': True,
                                    'square_type': item.get('type', 'EXTREME'),
                                }
                elif isinstance(d, dict):
                    for sym, v in d.items():
                        if isinstance(v, dict) and v.get('extreme') or v.get('alert'):
                            updates[sym] = {'square_extreme': True}
            except:
                pass
    if updates:
        update_batch('square_extreme', updates)
    return len(updates)

def get_candidates(min_dimensions: int = 3, direction: str = None, min_score: float = 0) -> list:
    """
    从战场情报中筛选多维共振候选池
    
    Args:
        min_dimensions: 最少几个维度同向（默认3）
        direction: 'LONG'/'SHORT'，None=不限方向
        min_score: 最低OI分数阈值
    
    Returns:
        [{symbol, signals, score, direction, fields}, ...] 按score降序
    """
    data = _load_intel()
    candidates = []
    
    for sym, fields in data.items():
        signals = []
        sym_score = 0
        
        # 维度1: CVD方向
        cvd = fields.get('cvd_1h')
        if cvd is not None:
            if direction == 'SHORT' and cvd < 0:
                signals.append('cvd_bear')
                sym_score += 1
            elif direction == 'LONG' and cvd > 0:
                signals.append('cvd_bull')
                sym_score += 1
            elif direction is None:
                signals.append('cvd_bear' if cvd < 0 else 'cvd_bull')
                sym_score += 1
        
        # 维度2: OI信号方向
        oi_sig = fields.get('oi_signal', '')
        if oi_sig:
            if direction == 'LONG' and ('LONG' in oi_sig):
                signals.append('oi_long')
                sym_score += 2
            elif direction == 'SHORT' and ('SHORT' in oi_sig):
                signals.append('oi_short')
                sym_score += 2
            elif direction is None:
                signals.append(f'oi_{oi_sig.lower()}')
                sym_score += 2
        
        # 维度3: 鲸鱼方向
        whale_dir = fields.get('whale_direction', '')
        whale_score = fields.get('whale_score', 0)
        if whale_dir and whale_score > 0:
            if direction == 'LONG' and whale_dir in ('多', '多头', 'BULLISH', 'LONG'):
                signals.append('whale_bull')
                sym_score += 1
            elif direction == 'SHORT' and whale_dir in ('空', '空头', 'BEARISH', 'SHORT'):
                signals.append('whale_bear')
                sym_score += 1
            elif direction is None:
                signals.append(f'whale_{whale_dir}')
                sym_score += 1
        
        # 维度4: RSI信号
        rsi_sig = fields.get('rsi_signal', '')
        rsi_trigger = fields.get('rsi_trigger', '')
        if rsi_sig:
            if direction == 'LONG' and rsi_sig in ('OVERSOLD', 'BULLISH'):
                signals.append(f'rsi_{rsi_sig.lower()}')
                sym_score += 1
            elif direction == 'SHORT' and rsi_sig in ('OVERBOUGHT', 'BEARISH'):
                signals.append(f'rsi_{rsi_sig.lower()}')
                sym_score += 1
            elif direction is None:
                signals.append(f'rsi_{rsi_sig.lower()}')
                sym_score += 1
        if rsi_trigger:
            signals.append(f'rsi_trigger:{rsi_trigger}')
            sym_score += 2
        
        # 维度5: 清算热图偏斜
        bull_score = fields.get('liq_bull_score', 0)
        bear_score = fields.get('liq_bear_score', 0)
        if bull_score != bear_score:
            if direction == 'LONG' and bull_score > bear_score:
                signals.append('liq_bull_skew')
                sym_score += 1
            elif direction == 'SHORT' and bear_score > bull_score:
                signals.append('liq_bear_skew')
                sym_score += 1
            elif direction is None:
                signals.append('liq_bull_skew' if bull_score > bear_score else 'liq_bear_skew')
                sym_score += 1
        
        # 维度6: Breakout信号
        bo_sig = fields.get('breakout_signal', '')
        if bo_sig and bo_sig != 'WATCH':
            signals.append(f'breakout_{bo_sig.lower()}')
            sym_score += 2
        
        # 维度7: Screener分数
        sc_score = fields.get('screener_score', 0)
        if sc_score > 0:
            signals.append(f'screener_{sc_score:.0f}')
            sym_score += 1
        
        # 维度8: Square极端信号
        if fields.get('square_extreme'):
            signals.append('square_extreme')
            sym_score += 2
        
        # 去重signals
        unique_signals = list(dict.fromkeys(signals))
        
        # OI分数阈值
        oi_score = fields.get('oi_score', 0)
        if min_score > 0 and oi_score < min_score:
            continue
        
        if len(unique_signals) >= min_dimensions:
            candidates.append({
                'symbol': sym,
                'signals': unique_signals,
                'dim_count': len(unique_signals),
                'score': sym_score,
                'direction': direction or 'ANY',
                'oi_score': oi_score,
                'cvd_1h': cvd,
                'whale_direction': whale_dir,
                'rsi_signal': rsi_sig,
                'liq_bull_score': bull_score,
                'liq_bear_score': bear_score,
                'breakout_signal': bo_sig,
            })
    
    candidates.sort(key=lambda x: (-x['score'], -x['dim_count']))
    return candidates

def merge_all():
    """一次性合并所有8个数据源"""
    cvd_count = merge_cvd_snapshots()
    liq_count = merge_liq_heatmaps()
    screener_count = merge_scan_candidates()
    rsi_count = merge_rsi_watcher()
    oi_count = merge_oi_signals()
    whale_count = merge_whale_monitor()
    breakout_count = merge_breakout_watch()
    square_count = merge_square_extreme()
    report = get_coverage_report()
    return {
        'cvd': cvd_count,
        'liq_heatmap': liq_count,
        'screener': screener_count,
        'rsi_watcher': rsi_count,
        'oi_scanner': oi_count,
        'whale_monitor': whale_count,
        'breakout_watch': breakout_count,
        'square_extreme': square_count,
        'total_symbols': report['total_symbols'],
        'coverage_pct': report['coverage_pct'],
    }

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'merge':
        r = merge_all()
        print(f"合并完成:")
        for k in ['cvd','liq_heatmap','screener','rsi_watcher','oi_scanner','whale_monitor','breakout_watch','square_extreme']:
            print(f"  {k}: {r[k]}标的")
        print(f"总计: {r['total_symbols']}标的 ({r['coverage_pct']:.1f}%)")
    elif len(sys.argv) > 1 and sys.argv[1] == 'report':
        r = get_coverage_report()
        print(f"总标的: {r['total_symbols']} / 718 ({r['coverage_pct']:.1f}%)")
        for k, v in r['by_field'].items():
            print(f"  {k}: {v}标的")
    elif len(sys.argv) > 1 and sys.argv[1] == 'candidates':
        min_dim = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        direction = sys.argv[3] if len(sys.argv) > 3 else None
        cands = get_candidates(min_dimensions=min_dim, direction=direction)
        print(f"候选池: {len(cands)}个标的 (≥{min_dim}维共振, dir={direction or 'ANY'})")
        for c in cands[:20]:
            print(f"  {c['symbol']:15s} score={c['score']:2d} dims={c['dim_count']} signals={c['signals']}")
    else:
        print("用法: python3 battlefield_intel.py [merge|report|candidates <min_dim> <direction>]")
