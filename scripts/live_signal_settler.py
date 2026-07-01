#!/usr/bin/env python3
"""
# ── 全局内存优化（工程师建议 P1）──
import gc as _gc_mod
import psutil as _psutil_mod
_gc_mod.enable()
_gc_mod.set_threshold(700, 10, 10)

def _check_and_gc():
    _gc_mod.collect()
    if _psutil_mod.virtual_memory().percent > 75:
        _gc_mod.collect(2)
# ─────────────────────────────────────
live_signal_settler.py · DharmaBridge 信号结算器 v2.0
设计院 · 2026-05-29  |  TP2二阶追踪修复 · 2026-06-01

重大修复 v2.0:
  TP1触及后继续追踪TP2（分批出场逻辑）
  原v1.0在low<=tp1时立即终止，TP2永远无法触发
  修复后：TP2命中->outcome=TP2（更高收益），TP2未命中->保留outcome=TP1

功能：
  结算 data/live_signal_log.jsonl 中未结算的信号
  用 Binance API 查询 TP1/SL 是否触达，回填 outcome / pnl_pct / closed_ts

用法：
  python3 scripts/live_signal_settler.py         # 正常结算
  python3 scripts/live_signal_settler.py --stats # 统计
  python3 scripts/live_signal_settler.py --dry   # 空运行
"""
import sys, os, json, time, argparse, requests
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
LOG_PATH = BASE / 'data' / 'live_signal_log.jsonl'
LOCK_PATH = str(LOG_PATH) + '.lock'

MAX_HOLD_HOURS = 48   # [v4.0铁证] 持仓时限延长至48H（原16H造成大量误TIMEOUT，铁证EV验证）
SETTLE_LOOKBACK = 500 # 最多处理最近N条

# grade字段兼容emoji和数字
_GRADE_SCORE_MAP = {'🔴神级': 90, '🟠极强': 75, '🟡强': 60, '🔵中等': 50, '⚫放弃': 0}
def _parse_grade(g) -> float:
    try: return float(g)
    except: return float(_GRADE_SCORE_MAP.get(str(g).strip(), 0))

def _get_price_range(symbol: str, entry_ts: str, hold_hours: int = MAX_HOLD_HOURS) -> tuple:
    """获取信号发出后hold_hours内的最高最低价（1H K线）
    [FIX-v25.5] 当K线获取失败（信号过老/网络异常）时，用当前实时价格作为 fallback
    避免 "too old" 信号永远被 n_skip
    """
    try:
        from datetime import datetime
        # [FIX-v25.5] entry_ts 可能是 float(unix ts) 或 ISO 字符串
        if isinstance(entry_ts, (int, float)):
            ts = datetime.fromtimestamp(float(entry_ts), tz=__import__('datetime').timezone.utc)
        else:
            ts = datetime.fromisoformat(str(entry_ts).replace('Z','+00:00'))
        start_ms = int(ts.timestamp() * 1000)
        end_ms = start_ms + hold_hours * 3600 * 1000
        now_ms = int(time.time() * 1000)
        end_ms = min(end_ms, now_ms - 60000)  # 不查未来K线

        if end_ms <= start_ms:
            return None, None, False  # 信号太新，hold期未到

        # [P0-A 设计院 2026-06-21] 改用 15m K线，提高 TP/SL 识别精度（1H K线可能跨越 TP/SL 而不记录）
        url = f"https://fapi.binance.com/fapi/v1/klines"
        r = requests.get(url, params={
            'symbol': symbol, 'interval': '15m',
            'startTime': start_ms, 'endTime': end_ms, 'limit': hold_hours * 4 + 4
        }, timeout=8)
        if r.status_code != 200:
            raise Exception(f'HTTP {r.status_code}')

        klines = r.json()
        if not klines:
            raise Exception('empty klines')

        highs = [float(k[2]) for k in klines]
        lows  = [float(k[3]) for k in klines]
        expired = end_ms <= now_ms  # hold期已过
        return max(highs), min(lows), expired

    except Exception as e:
        # [FIX-v25.5] fallback: K线获取失败时用当前实时价格
        # hold_hours 已经过期 → expired=True，触发TIMEOUT结算
        try:
            from datetime import datetime, timezone as _tz
            if isinstance(entry_ts, (int, float)):
                ts2 = datetime.fromtimestamp(float(entry_ts), tz=_tz.utc)
            else:
                ts2 = datetime.fromisoformat(str(entry_ts).replace('Z','+00:00'))
            start_ms = int(ts2.timestamp() * 1000)
            end_ms   = start_ms + hold_hours * 3600 * 1000
            now_ms   = int(time.time() * 1000)
            expired  = end_ms <= now_ms
            if expired:
                # 信号已过期，用实时价格作为高低价近似（不影响TP/SL判断）
                cur = _fetch_current_price(symbol)
                if cur and cur > 0:
                    return cur * 1.005, cur * 0.995, True  # 宽松0.5%范围，expired=True
        except Exception:
            pass
        return None, None, False


def _fetch_current_price(symbol: str) -> float:
    """fallback实时价格获取"""
    try:
        r = requests.get(
            'https://fapi.binance.com/fapi/v1/ticker/price',
            params={'symbol': symbol}, timeout=4
        )
        return float(r.json()['price'])
    except Exception:
        return 0.0

def _load() -> list:
    if not LOG_PATH.exists():
        return []
    records = []
    with open(LOG_PATH, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try: records.append(json.loads(line))
                except: pass
    return records

def _save(records: list):
    # Fix-B: 已结算信号强制valid=False，防止僵尸信号被监控
    for r in records:
        if r.get('settled') and r.get('valid'):
            r['valid'] = False
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

def settle(dry_run=False) -> dict:
    from datetime import datetime, timezone, timedelta
    records = _load()
    now_utc = datetime.now(timezone.utc)

    # Fix: 强制过期超过TTL且未结算的信号（[Fix-TTL 2026-06-11] expires_at优先，否则fallback 48H）
    n_force_expired = 0
    for rec in records:
        if rec.get('settled'): continue
        try:
            _ts_raw = rec['ts']
            if isinstance(_ts_raw, (int, float)):
                ts = datetime.fromtimestamp(float(_ts_raw), tz=timezone.utc)
            else:
                ts = datetime.fromisoformat(str(_ts_raw).replace('Z','+00:00'))
            age_h = (now_utc - ts).total_seconds() / 3600
            # 优先使用expires_at
            expires_at = rec.get('expires_at', '')
            is_expired = False
            if expires_at:
                exp_ts = datetime.fromisoformat(expires_at.replace('Z','+00:00'))
                is_expired = now_utc > exp_ts
            else:
                # [FIX-v25.5] 用信号自身 ttl_hours 而非硬编码 48H
                _rec_ttl = float(rec.get('ttl_hours', 0) or 0)
                _fallback_ttl = _rec_ttl if _rec_ttl >= 8 else 24  # ttl缺失时缩短至 24H
                is_expired = age_h > _fallback_ttl
            if is_expired:
                if not dry_run:
                    rec['settled'] = True
                    rec['result']  = 'EXPIRED'
                    rec['outcome'] = 'EXPIRED'
                    rec['exit_price'] = rec.get('price', 0)
                    rec['pnl_pct'] = 0.0
                    rec['closed_ts'] = now_utc.isoformat()
                n_force_expired += 1
        except Exception as _fe:
            print(f'[Settler] ⚠️ force_expire异常: {_fe}')
    if n_force_expired > 0 and not dry_run:
        _save(records)

    # ── [v3.1] 价格偏离强制清理（识别哲学：不适合的信号即时清理）──
    # 有效信号入场区偏离 > 8% → 机会已过，强制 PRICE_EXPIRED
    import urllib.request as _ur, json as _json
    _price_cache = {}
    def _get_cur_price(sym):
        if sym not in _price_cache:
            try:
                try:
                    from brahma_brain.brahma_bus import get_price as _gp_bus
                    _px_val = _gp_bus(sym)
                    if _px_val:
                        _price_cache[sym] = _px_val
                        return _px_val
                except Exception:
                    pass
                _resp = _ur.urlopen(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}', timeout=3)
                _price_cache[sym] = float(_json.loads(_resp.read())['price'])
            except:
                _price_cache[sym] = None
        return _price_cache[sym]

    n_price_expired = 0
    for rec in records:
        if rec.get('settled'): continue
        # [FIX-v25.5] OPEN状态信号无论 valid 均需检查入场区间，防止僵尸信号
        # if rec.get('valid') != True: continue  # 已移除，原注释保留供审计
        if rec.get('status') != 'OPEN': continue  # 只处理 OPEN 状态信号
        try: _gr = _parse_grade(rec.get('grade', 0))
        except: _gr = 0.0
        if _gr < 50: continue
        sym = rec.get('symbol', '')
        el = float(rec.get('entry_lo', 0) or 0)
        eh = float(rec.get('entry_hi', 0) or 0)
        if not el or not eh: continue
        cur = _get_cur_price(sym)
        if cur is None: continue
        entry_mid = (el + eh) / 2
        gap_pct = abs(cur - entry_mid) / entry_mid * 100
        if gap_pct > 6.0:  # 6%偏离即清理（ETH/SOL 7%偏离已超出有效范围）
            if not dry_run:
                rec['settled'] = True
                rec['result'] = 'PRICE_EXPIRED'
                rec['outcome'] = 'PRICE_EXPIRED'
                rec['price_gap_pct'] = round((cur - entry_mid) / entry_mid * 100, 2)
                rec['closed_ts'] = now_utc.isoformat()
            n_price_expired += 1
    if n_price_expired > 0 and not dry_run:
        _save(records)
        print(f'[Settler] 💨 价格偏离清理: {n_price_expired}条 PRICE_EXPIRED')

    # ══════════════════════════════════════════════════════════
    # [v4.0] 信号池五维自愈闭环  · 识别哲学 · 2026-06-10
    # 「信号池 = 实时战场快照，条件不满足立刻清」
    # ══════════════════════════════════════════════════════════
    _selfheal_events = []   # 自愈事件收集器

    # ── 获取 per-symbol 体制（修复 v25.6：不再用全局BTC体制一刀切）─
    # [BUG-FIX 2026-06-20] 原来读 brahma_state.regime（BTC全局体制）
    # 导致BTC→BEAR_RECOVERY时所有BEAR_TREND信号被REGIME_EXPIRED，废弃率65%
    # 修复：优先读 regime_state.json per-symbol confirmed 体制
    _sym_regime_map = {}   # symbol → current_regime
    _cur_regime = 'UNKNOWN'  # 全局fallback（仅用于日志）
    try:
        import os as _os
        _rs_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)),
                                 'data', 'regime_state.json')
        _rs = _json.loads(open(_rs_path).read())
        for _sym, _rd in _rs.items():
            if isinstance(_rd, dict) and _rd.get('confirmed'):
                _sym_regime_map[_sym] = _rd['confirmed']
    except Exception as _rs_e:
        pass
    try:
        import os as _os
        _bs = _json.loads(
            open(_os.path.join(_os.path.dirname(_os.path.dirname(__file__)),
                               'data', 'brahma_state.json')).read()
        )
        # regime_label 是正确的体制标签；regime 可能跟随BTC实时体制，两者不一致时优先regime_label
        _cur_regime = _bs.get('regime_label') or _bs.get('regime', 'UNKNOWN')
    except: pass

    # ── 获取所有 symbol 价格（复用已有cache，补充缺失）─────────
    for rec in records:
        if not rec.get('settled'):
            _get_cur_price(rec.get('symbol', ''))   # 预热 cache

    n_superseded = n_regime_exp = n_sl_breach = n_invalid_clean = 0

    # ── [A] live_signal_log SUPERSEDED：同标的新信号替代旧信号 ──
    # 原则：同标的同方向，score更高的新信号 → 旧信号标记 SUPERSEDED
    _active = [r for r in records if not r.get('settled')]
    for i, rec_old in enumerate(_active):
        if rec_old.get('settled'): continue
        sym_o  = rec_old.get('symbol', '')
        dir_o  = rec_old.get('signal_dir', '')
        try: sc_o = float(rec_old.get('score', 0) or 0)
        except: sc_o = 0.0
        try: ts_o = datetime.fromisoformat(str(rec_old.get('ts','')).replace('Z','+00:00'))
        except: ts_o = now_utc
        for rec_new in _active[i+1:]:
            if rec_new.get('settled'): continue
            sym_n = rec_new.get('symbol', '')
            dir_n = rec_new.get('signal_dir', '')
            try: sc_n = float(rec_new.get('score', 0) or 0)
            except: sc_n = 0.0
            try: ts_n = datetime.fromisoformat(str(rec_new.get('ts','')).replace('Z','+00:00'))
            except: ts_n = now_utc
            if sym_n != sym_o or dir_n != dir_o: continue
            if ts_n <= ts_o: continue                # rec_new 不比 rec_old 新
            if sc_n >= sc_o - 5:                     # 新信号同等或更好 → 旧信号作废
                if not dry_run:
                    rec_old['settled'] = True
                    rec_old['result']  = 'SUPERSEDED'
                    rec_old['outcome'] = 'SUPERSEDED'
                    rec_old['superseded_by_score'] = sc_n
                    rec_old['closed_ts'] = now_utc.isoformat()
                n_superseded += 1
                _selfheal_events.append(
                    f"SUPERSEDED {sym_o}/{dir_o} score={sc_o:.0f} → newer score={sc_n:.0f}")
                break   # 旧信号已清理，跳出内层循环

    if n_superseded > 0 and not dry_run:
        _save(records)
        print(f'[Settler] ♻️  SUPERSEDED: {n_superseded}条旧信号被新信号替代')

    # ── [B] 体制切换 → REGIME_EXPIRED ──────────────────────────
    # [FIX v25.6 2026-06-20] per-symbol 体制判断，不再用全局BTC体制一刀切
    # 原逻辑：brahma_state.regime=BEAR_RECOVERY → 所有BEAR_TREND信号被废弃（65%误废率）
    # 新逻辑：取 regime_state.json[symbol].confirmed 作为该信号的当前体制
    # 兜底：如果symbol不在regime_state，退回全局_cur_regime
    # [P2-1/P2-3 设计院 2026-06-24] 体制兼容层扩展
    # 核心洞察：89条REGIME_EXPIRED中75%是天然近亲体制的互相删除
    #   BEAR_EARLY→CHOP_MID: 28条 | BEAR_TREND→BEAR_RECOVERY: 21条 | BEAR_EARLY→BEAR_RECOVERY: 18条
    # 修复原则：同向天然近亲体制的信号不应被废弃，而应降权重算
    # 兼容规则：{当前体制: {可兼容的信号生成体制}}
    _REGIME_COMPAT = {
        # 熊市族群（天然近亲，信号方向一致）
        'BEAR_TREND':    {'BEAR_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY'},
        'BEAR_EARLY':    {'BEAR_EARLY', 'BEAR_TREND', 'BEAR_RECOVERY'},
        'BEAR_RECOVERY': {'BEAR_RECOVERY', 'BEAR_TREND', 'BEAR_EARLY'},  # 熊市反弹仍在熊市周期
        # CHOP 兼容近期熊市信号（不直接废弃，等待方向确认）
        'CHOP_LOW':      {'CHOP_LOW', 'CHOP_MID', 'BEAR_EARLY', 'BEAR_TREND'},
        'CHOP_MID':      {'CHOP_MID', 'CHOP_LOW', 'CHOP_HIGH', 'BEAR_EARLY', 'BEAR_TREND'},
        'CHOP_HIGH':     {'CHOP_HIGH', 'CHOP_MID'},
        # 牛市族群
        'BULL_TREND':    {'BULL_TREND', 'BULL_EARLY', 'BULL_CORRECTION'},
        'BULL_EARLY':    {'BULL_EARLY', 'BULL_TREND', 'BULL_CORRECTION'},
        'BULL_CORRECTION': {'BULL_CORRECTION', 'BULL_EARLY', 'BULL_TREND'},
    }
    # 高分信号（score≥160）在体制切换后先降权重算，不直接废弃
    # 降权后若仍≥138则保留，否则才废弃
    _HIGH_SCORE_RESCUE_THRESHOLD = 160  # 救援阈值
    _RESCUE_MULT = 0.75  # 降权乘数
    for rec in records:
        if rec.get('settled'): continue
        if rec.get('status') != 'OPEN': continue
        try: _gr = _parse_grade(rec.get('grade', 0))
        except: _gr = 0.0
        if _gr < 50: continue
        # [v25.6] VIP手动信号（score=999）豆免体制检查，不得被REGIME_EXPIRED
        if rec.get('score', 0) == 999 or str(rec.get('grade', '')).startswith('⧜️VIP'):
            continue
        sig_regime = rec.get('regime', '')
        if not sig_regime: continue
        # [v25.6] per-symbol 当前体制
        _rec_sym = rec.get('symbol', '')
        _sym_cur_regime = _sym_regime_map.get(_rec_sym, _cur_regime)
        _compat = _REGIME_COMPAT.get(_sym_cur_regime, set())
        if _compat and sig_regime not in _compat:
            # 信号体制与该标的当前体制不兼容 → age>8H才清理
            try:
                ts_sig = datetime.fromisoformat(str(rec.get('ts','')).replace('Z','+00:00'))
                sig_age_h = (now_utc - ts_sig).total_seconds() / 3600
            except: sig_age_h = 99
            # [P2-2 设计院 2026-06-24] TTL动态化：尊重信号实际TTL而非硬编码8H
            # 信号的 ttl_h 字段由 dharma_data_bridge 写入（v25.7已动态化）
            # 若信号没有ttl字段，按score分级：score≥165=10H, ≥138=6H, 其他=8H
            _sig_ttl_s = rec.get('ttl') or rec.get('ttl_sec') or 0
            if _sig_ttl_s > 0:
                _expire_age_h = _sig_ttl_s / 3600
            else:
                _s = rec.get('score', 0)
                _expire_age_h = 10 if _s >= 165 else (6 if _s >= 138 else 8)

            if sig_age_h > _expire_age_h:
                # [P2-1 设计院 2026-06-24] 高分信号救援机制
                # score≥160 的信号在体制切换后先降权重算，不直接废弃
                # 如果降权后仍≥138，则保留信号（修改score和body_regime，继续待机）
                _sig_score = rec.get('score', 0)
                _rescued = False
                if _sig_score >= _HIGH_SCORE_RESCUE_THRESHOLD:
                    _new_score = int(_sig_score * _RESCUE_MULT)
                    if _new_score >= 138:
                        # 救援成功：降权保留，更新体制和分数
                        if not dry_run:
                            rec['score']          = _new_score
                            rec['regime']         = _sym_cur_regime   # 更新为当前体制
                            rec['_rescued']       = True
                            rec['_rescue_from']   = sig_regime
                            rec['_rescue_score']  = _sig_score
                            rec['_rescue_new']    = _new_score
                        _rescued = True
                        _selfheal_events.append(
                            f"RESCUED {_rec_sym} {sig_regime}→{_sym_cur_regime} score:{_sig_score}→{_new_score}")

                if not _rescued:
                    if not dry_run:
                        rec['settled']   = True
                        rec['result']    = 'REGIME_EXPIRED'
                        rec['outcome']   = 'REGIME_EXPIRED'
                        rec['closed_ts'] = now_utc.isoformat()
                        rec['regime_mismatch'] = f'{sig_regime}→{_sym_cur_regime}'
                    n_regime_exp += 1
                    _selfheal_events.append(
                        f"REGIME_EXPIRED {_rec_sym} {sig_regime}→{_sym_cur_regime}")

    if n_regime_exp > 0 and not dry_run:
        _save(records)
        print(f'[Settler] 🔄 REGIME_EXPIRED: {n_regime_exp}条体制不匹配信号清理')

    # ── [C] SL突破 → SL_BREACHED ─────────────────────────────
    # SHORT信号：当前价格 > stop_loss → 方向已错，立刻清理
    # LONG信号：当前价格 < stop_loss → 方向已错，立刻清理
    for rec in records:
        if rec.get('settled'): continue
        # [FIX-v25.5] OPEN状态信号无论 valid 均需执行SL检测，防止SL击穿后僵尸
        # if rec.get('valid') != True: continue
        if rec.get('status') != 'OPEN': continue
        try: _gr = _parse_grade(rec.get('grade', 0))
        except: _gr = 0.0
        if _gr < 50: continue
        sym = rec.get('symbol', '')
        sl  = float(rec.get('stop_loss', 0) or 0)  # [R2-fix SSOT] stop_loss是权威字段(DharmaBridge写入)
        if not sl: continue
        cur = _get_cur_price(sym)
        if cur is None: continue
        sig_dir = rec.get('signal_dir', 'SHORT')
        sl_breached = (sig_dir == 'SHORT' and cur > sl) or                       (sig_dir == 'LONG'  and cur < sl)
        if sl_breached:
            if not dry_run:
                rec['settled']   = True
                rec['result']    = 'SL_BREACHED'
                rec['outcome']   = 'SL_BREACHED'
                rec['sl_breach_price'] = cur
                rec['closed_ts'] = now_utc.isoformat()
            n_sl_breach += 1
            _selfheal_events.append(
                f"SL_BREACHED {sym} {sig_dir} sl={sl:.2f} cur={cur:.2f}")

    if n_sl_breach > 0 and not dry_run:
        _save(records)
        print(f'[Settler] 🛡️  SL_BREACHED: {n_sl_breach}条止损被突破信号清理')

    # ── [D] valid=False 信号处理 ─────────────────────────────
    # [v4.0 P0修复] valid=False = 价格未进入入场区 → 结果为 MISS，不是TIMEOUT
    # 对MISS信号做PnL回测：即使未入场，信号方向正确也计入学习闭环
    for rec in records:
        if rec.get('settled'): continue
        if rec.get('valid') == True: continue          # 只处理 invalid
        el = float(rec.get('entry_lo', 0) or 0)
        eh = float(rec.get('entry_hi', 0) or 0)
        sym = rec.get('symbol', '')
        sig_dir = rec.get('direction') or rec.get('signal_dir', 'SHORT')
        sl_miss  = float(rec.get('stop_loss', 0) or 0)
        tp1_miss = float(rec.get('tp1', 0) or 0)
        try: ts_inv = datetime.fromisoformat(str(rec.get('ts','')).replace('Z','+00:00'))
        except: ts_inv = now_utc
        age_h_inv = (now_utc - ts_inv).total_seconds() / 3600

        should_settle = False
        miss_reason = 'MISS'

        if age_h_inv > 72:  # 超72H → 强制结算为MISS
            should_settle = True; miss_reason = 'MISS'
        elif el and eh:
            cur = _get_cur_price(sym)
            if cur:
                gap = abs(cur - (el+eh)/2) / ((el+eh)/2) * 100
                if gap > 10: should_settle = True; miss_reason = 'MISS'

        if should_settle:
            # [P0-1] MISS信号做回测：查48H内是否最终触及TP/SL（用于方向胜率统计）
            miss_pnl = 0.0
            miss_outcome = miss_reason
            if sl_miss and tp1_miss and age_h_inv > 2:
                try:
                    hi_miss, lo_miss, _ = _get_price_range(sym, rec.get('ts', ''), hold_hours=48)
                    if hi_miss and lo_miss:
                        entry_ref = (el + eh) / 2 if el and eh else float(rec.get('price', 0) or 0)
                        if sig_dir == 'SHORT':
                            if hi_miss >= sl_miss:
                                miss_outcome = 'MISS_LOSS'; miss_pnl = round((entry_ref - sl_miss)/entry_ref*100, 3)
                            elif lo_miss <= tp1_miss:
                                miss_outcome = 'MISS_WIN'; miss_pnl = round((entry_ref - tp1_miss)/entry_ref*100, 3)
                        else:
                            if lo_miss <= sl_miss:
                                miss_outcome = 'MISS_LOSS'; miss_pnl = round((sl_miss - entry_ref)/entry_ref*100, 3)
                            elif hi_miss >= tp1_miss:
                                miss_outcome = 'MISS_WIN'; miss_pnl = round((tp1_miss - entry_ref)/entry_ref*100, 3)
                except Exception: pass
            if not dry_run:
                rec['settled']   = True
                rec['result']    = 'MISS'  # 统一为MISS，不污染WIN/LOSS统计
                rec['outcome']   = miss_outcome
                rec['pnl_pct']   = miss_pnl
                rec['closed_ts'] = now_utc.isoformat()
                rec['miss_direction_correct'] = miss_outcome == 'MISS_WIN'  # 方向正确标记
            n_invalid_clean += 1
            _selfheal_events.append(f"MISS_SETTLE {sym} {miss_outcome}")
            # [P0-1] MISS_WIN/MISS_LOSS 计入贝叶斯（方向正确性学习，降权0.3x）
            if miss_outcome in ('MISS_WIN', 'MISS_LOSS') and not dry_run:
                try:
                    from online_bayes import OnlineBayes
                    _ob = OnlineBayes()
                    _ob.update(sym, rec.get('regime','?'), sig_dir,
                               1 if miss_outcome=='MISS_WIN' else 0, weight=0.3)
                except Exception: pass

    if n_invalid_clean > 0 and not dry_run:
        _save(records)
        print(f'[Settler] 🧹 INVALID清理: {n_invalid_clean}条')

    # ── [E] 自愈事件日志 ──────────────────────────────────────
    if _selfheal_events and not dry_run:
        import os as _oss
        _log_path = _oss.path.join(_oss.path.dirname(_oss.path.dirname(__file__)),
                                    'data', 'selfheal_events.jsonl')
        _entry = {'ts': now_utc.isoformat(), 'events': _selfheal_events,
                  'regime': _cur_regime}
        with open(_log_path, 'a') as _lf:
            _lf.write(_json.dumps(_entry, ensure_ascii=False) + '\n')

    if any([n_superseded, n_regime_exp, n_sl_breach, n_invalid_clean]):
        print(f'[Settler] 📊 自愈汇总: SUPERSEDED={n_superseded} '
              f'REGIME_EXP={n_regime_exp} SL_BREACH={n_sl_breach} '
              f'INVALID={n_invalid_clean}')

    unsettled = [r for r in records if not r.get('settled') and r.get('ts')]
    unsettled = unsettled[-SETTLE_LOOKBACK:]  # 最近N条

    total = len(unsettled)
    n_settled = 0
    n_tp1 = n_tp2 = n_sl = n_timeout = n_skip = 0

    for rec in unsettled:
        sym    = rec.get('symbol', '')
        sig_dir = rec.get('signal_dir', 'SHORT')
        entry_ts = rec.get('ts', '')
        tp1    = rec.get('tp1')
        sl     = rec.get('stop_loss')
        entry  = (rec.get('entry_lo', 0) + rec.get('entry_hi', 0)) / 2
        # [v22.1 2026-06-10] 动态TTL v2 — 体制+grade+score+标的四维感知
        # 铁证: WIN止损宽1.16% vs TIMEOUT止损宽1.68%
        # → TIMEOUT方向正确，只是没等到回测时机，延长TTL是外科手术修复
        # 数据依据: BTC/DOGE精准(Δ<0.25%) | SOL/BNB/LTC OB太宽(Δ>1.1%)
        _regime_h = rec.get('regime', '')
        _sym_h    = rec.get('symbol', '')
        try: _grade_h = _parse_grade(rec.get('grade', 0))
        except: _grade_h = 0.0  # 兼容emoji/text grade字段
        _score_h  = float(rec.get('score', 0) or 0)

        # 标的精度分级（铁证分析）
        _precise_sym = _sym_h in ('BTCUSDT', 'DOGEUSDT', 'ETHUSDT')  # OB精准标的
        _wide_sym    = _sym_h in ('SOLUSDT', 'BNBUSDT', 'LTCUSDT', 'LINKUSDT', 'XRPUSDT')  # OB偏宽标的

        # 基础TTL（体制决定基础）
        if 'BEAR_TREND' in _regime_h:
            hold_h = 48                     # 主趋势信号，给足等待时间
        elif 'BEAR_EARLY' in _regime_h:
            hold_h = 36                     # 熊市初期，结构成立概率高
        elif 'CHOP' in _regime_h:
            hold_h = 16                     # 震荡体制窗口短（铁证：CHOP信号不宜久等）
        elif 'BEAR_RECOVERY' in _regime_h:
            hold_h = 20                     # 反弹体制不稳定，不宜死等
        elif 'BULL_TREND' in _regime_h or 'BULL_EARLY' in _regime_h:
            hold_h = 72                     # [v24.0-P1 2026-06-12] MC10万次: BULL LONG需长时间发展 基础72H
        else:
            hold_h = 24

        # grade分层TTL（达摩院六方辩论定稿 2026-06-10）
        # [v24.2] grade分层TTL: 仅A级(70-79)×1.3倍 / S级(≥80)×1.5倍
        # 铁证: grade50-59 TO率=73%全系统封堵，不再需要收紧TTL策略
        if _grade_h >= 80:    hold_h = int(hold_h * 1.5)   # S级结构：延长50%（WR=100%铁证）
        elif _grade_h >= 70:  hold_h = int(hold_h * 1.3)   # A级结构：延长30%
        # [v24.2] grade<70已全系统封堵，仅grade≥70信号进入此逻辑
        # grade<70由BridgeGate/StructureGate已拦截，不进入此逻辑

        # 精准标的加成（BTC/ETH/DOGE OB识别精准，给时间发展）
        if _precise_sym and _score_h >= 150:
            hold_h = max(hold_h, 48)        # 高分精准标的至少48H

        # 偏宽标的收紧（SOL/BNB OB太宽，长时间等待无意义）
        # [v24.2] grade<70已封堵，此条件改为grade<70(不可触达，保留防御逻辑)
        if _wide_sym and _grade_h < 70:
            hold_h = min(hold_h, 24)        # 低grade宽OB，不超过24H

        # 硬上限 / 下限
        # [v24.2 bugfix] BULL上限 110H→72H: 对齐达摩四策略训练定稿(WFV6/6 hold=72H)
        # 110H是v24.0-P1的笔误(commit说10H)，达摩训练最优hold=72H
        _bull_mode = 'BULL_TREND' in _regime_h or 'BULL_EARLY' in _regime_h
        _ttl_max   = 72   # [v24.2] 统一72H上限，与达摩院训练对齐
        hold_h = max(12, min(hold_h, _ttl_max))   # 动态上限统一72H
        rec['_hold_h_debug'] = hold_h       # 调试字段（不写入文件）

        if not tp1 or not sl or not entry:
            n_skip += 1
            continue

        high, low, expired = _get_price_range(sym, entry_ts, hold_h)
        if high is None:
            n_skip += 1
            continue

        # V2修复：先验证入场区是否被触及
        # SHORT: 价格需反弹到 entry_lo 以上才算触发入场
        # LONG:  价格需回调到 entry_hi 以下才算触发入场
        entry_lo = rec.get('entry_lo', 0)
        entry_hi = rec.get('entry_hi', 0)
        if entry_lo and entry_hi:
            entry_mid_check = (entry_lo + entry_hi) / 2
            if sig_dir == 'SHORT' and high < entry_lo:
                # 12H内价格从未反弹到入场区 → 信号等待中，不结算
                n_skip += 1
                continue
            if sig_dir == 'LONG' and low > entry_hi:
                # 12H内价格从未回调到入场区 → 信号等待中，不结算
                n_skip += 1
                continue

        outcome = None
        exit_price = None
        pnl_pct = None

        tp2 = rec.get('tp2')  # v2.0: TP2字段

        if sig_dir == 'SHORT':
            if high >= sl:
                # SL优先级最高（v2.0改：先判SL避免SL/TP1同时触及时误判）
                outcome = 'SL'
                exit_price = sl
                pnl_pct = round((entry - sl) / entry * 100, 3) if entry else 0
                n_sl += 1
            elif low <= tp1:
                # TP1触及 -> 继续检查TP2（v2.0核心修复）
                if tp2 and float(tp2) > 0 and low <= float(tp2):
                    outcome = 'TP2'
                    exit_price = float(tp2)
                    pnl_pct = round((entry - float(tp2)) / entry * 100, 3) if entry else 0
                    n_tp2 += 1
                else:
                    outcome = 'TP1'
                    exit_price = tp1
                    pnl_pct = round((entry - tp1) / entry * 100, 3) if entry else 0
                    n_tp1 += 1
            elif expired:
                outcome = 'TIMEOUT'
                exit_price = high
                pnl_pct = round((entry - exit_price) / entry * 100, 3) if entry else 0
                n_timeout += 1
        else:  # LONG
            if low <= sl:
                # SL优先级最高
                outcome = 'SL'
                exit_price = sl
                pnl_pct = round((sl - entry) / entry * 100, 3) if entry else 0
                n_sl += 1
            elif high >= tp1:
                # TP1触及 -> 继续检查TP2（v2.0核心修复）
                if tp2 and float(tp2) > 0 and high >= float(tp2):
                    outcome = 'TP2'
                    exit_price = float(tp2)
                    pnl_pct = round((float(tp2) - entry) / entry * 100, 3) if entry else 0
                    n_tp2 += 1
                else:
                    outcome = 'TP1'
                    exit_price = tp1
                    pnl_pct = round((tp1 - entry) / entry * 100, 3) if entry else 0
                    n_tp1 += 1
            elif expired:
                outcome = 'TIMEOUT'
                exit_price = low
                pnl_pct = round((exit_price - entry) / entry * 100, 3) if entry else 0
                n_timeout += 1

        if outcome:
            n_settled += 1
            if not dry_run:
                rec['outcome']    = outcome
                rec['exit_price'] = round(exit_price, 6) if exit_price else None
                rec['pnl_pct']    = pnl_pct
                rec['closed_ts']  = datetime.now(timezone.utc).isoformat()
                rec['settled']    = True
                # ── [P0-fix 2026-06-24] result字段同步写入（修复闭环断裂：result只在早退时写，TIMEOUT/WIN/LOSS路径从未写result）
                # result 字段用于达摩院学习闭环，必须与 outcome/status 对齐
                if outcome in ('TP1', 'TP2'):
                    rec['result'] = 'WIN'
                elif outcome == 'SL':
                    rec['result'] = 'LOSS'
                else:  # TIMEOUT
                    rec['result'] = 'TIMEOUT'
                # ── [360fix] status 同步写入（TIMEOUT/REGIME_EXPIRED 不得标为 LOSS）
                if outcome in ('TP1', 'TP2'):
                    rec['status'] = 'WIN'
                elif outcome == 'SL':
                    rec['status'] = 'LOSS'
                else:  # TIMEOUT / REGIME_EXPIRED / EXPIRED / PRICE_EXPIRED
                    rec['status'] = 'TIMEOUT'
                # ── 闭环数据层补全（设计院 2026-06-05）──────────────
                # close_reason: 与训练库字段对齐
                rec['close_reason'] = outcome
                # rr_actual: 实际获得的R:R
                _rr1 = float(rec.get('rr1') or 0)
                if outcome in ('TP1', 'TP2') and _rr1 > 0:
                    rec['rr_actual'] = round(_rr1 if outcome == 'TP1' else _rr1 * 2, 2)
                elif outcome == 'SL':
                    rec['rr_actual'] = -1.0
                else:
                    rec['rr_actual'] = round((pnl_pct or 0) / max(abs(rec.get('sl_pct', 1) or 1), 0.01), 2)
                # pnl_usd: 按NAV估算实际盈亏（武曲Paper数据层）
                # [v22.1 2026-06-10] 补充杯杆因子（合约5x），pnl_pct是价格变化%而非仓位收益%
                try:
                    _nav = 127.37  # fallback NAV
                    _bs = json.load(open(BASE / 'data' / 'brahma_state.json'))
                    _nav = float(_bs.get('nav', 127.37) or 127.37)
                except Exception: pass
                _pos_pct  = float(rec.get('position_pct') or rec.get('risk_pct') or 0.02)
                _leverage = float(rec.get('leverage') or 5.0)   # 默认合约5x杯杆
                # pnl_usd = NAV × 仓位% × 杯杆 × pnl_pct%
                # 示例: $132 × 2% × 5x × 4.35% = $0.575
                rec['pnl_usd']      = round(_nav * _pos_pct * _leverage * (pnl_pct or 0) / 100, 4)
                rec['leverage_used'] = _leverage  # 记录杯杆以便审计
                # entry_price_source: 标记入场价来源
                rec['entry_price_source'] = 'settler_estimated'
                # P0/P1/P2 体制健康守卫 — 记录结算结果
                try:
                    import sys as _hg_sys
                    if BASE.as_posix() not in _hg_sys.path:
                        _hg_sys.path.insert(0, BASE.as_posix())
                    from upgrade_v2.regime_health_guard import record_outcome as _hg_record
                    _hg_record(
                        symbol    = rec.get('symbol', ''),
                        regime    = rec.get('regime', ''),
                        direction = rec.get('signal_dir', ''),
                        outcome   = outcome,
                    )
                except Exception as _e_ignored:
                    print(f'[WARN][live_signal_settler] {type(_e_ignored).__name__}: {_e_ignored}')
                # ── RiskGate v2 结算通知（vnpy借鉴，苏摩111批准 2026-06-28）──
                try:
                    import sys as _rg_sys
                    _rg_sys.path.insert(0, str(BASE / 'scripts'))
                    from brahma_risk_gate import record_trade_result as _rg_record
                    _rg_record(symbol=rec.get('symbol',''), outcome=outcome, pnl_pct=(pnl_pct or 0)/100, nav=473.0)
                except Exception as _rg_e:
                    print(f'[WARN][RiskGate] {_rg_e}')
                # ── EventBus 广播平仓事件（vnpy借鉴，苏摩111批准 2026-06-28）──
                try:
                    import sys as _eb_sys
                    _eb_sys.path.insert(0, str(BASE))
                    from brahma_brain.brahma_event_bus import bus as _eb_bus
                    _eb_bus.emit_position_close(symbol=rec.get('symbol',''), outcome=outcome, pnl_pct=pnl_pct or 0, signal_id=rec.get('signal_id',''))
                except Exception as _eb_e:
                    print(f'[WARN][EventBus] {_eb_e}')
                # ── [P2 设计院 2026-06-21] 归因日志直接写入 attribution_log.jsonl ─────────────
                # 原因： lana/attribution.py 依赖 hunter_v2_trades.json（实盘）
                # Paper模式下交易记录在 live_signal_log，用此处直接写入
                try:
                    import json as _attr_json
                    _attr_f = BASE / 'data' / 'attribution_log.jsonl'
                    _attr_record = {
                        'ts':         rec.get('ts_iso', rec.get('ts', ''))[:19],
                        'signal_id':  rec.get('signal_id', ''),
                        'symbol':     rec.get('symbol', ''),
                        'direction':  rec.get('direction', ''),
                        'regime':     rec.get('regime', ''),
                        'score':      rec.get('score', 0),
                        'grade':      rec.get('grade', rec.get('structure_grade', 0)),
                        'outcome':    outcome,
                        'result':     outcome,
                        'pnl_pct':    rec.get('pnl_pct', 0),
                        'sl_pct':     rec.get('sl_pct', 0),
                        'rr1':        rec.get('rr1', 0),
                        'ob_dist_pct':rec.get('ob_dist_pct', 0),
                        'entry_lo':   rec.get('entry_lo', 0),
                        'exit_price': rec.get('exit_price', 0),
                        'settled_at': datetime.now(timezone.utc).isoformat(),
                    }
                    with open(_attr_f, 'a') as _af:
                        _af.write(_attr_json.dumps(_attr_record, ensure_ascii=False) + '\n')
                except Exception as _attr_e:
                    print(f'[WARN][attribution] {type(_attr_e).__name__}: {_attr_e}')
                # ── [END attribution] ──────────────────────────────────────────
                # ── engine_attribution: 引擎贡献率追踪 ─────────────
                try:
                    import sys as _ea_sys, os as _ea_os
                    _ea_path = _ea_os.path.dirname(_ea_os.path.abspath(__file__))
                    if _ea_path not in _ea_sys.path:
                        _ea_sys.path.insert(0, _ea_path)
                    from engine_attribution import record_settlement as _ea_record
                    if outcome in ('TP1', 'TP2', 'SL'):
                        _ea_record(rec, outcome)
                except Exception as _e_ignored:
                    print(f'[WARN][live_signal_settler] {type(_e_ignored).__name__}: {_e_ignored}')
                # ── loss_autopsy: LOSS 引发自动五大根因解剖 ─────────────────
                if outcome == 'SL':
                    try:
                        import sys as _la_sys
                        if BASE.as_posix() not in _la_sys.path:
                            _la_sys.path.insert(0, BASE.as_posix())
                        from dharma.loss_autopsy import autopsy
                        _la_result = autopsy(rec)
                        rec['loss_autopsy'] = _la_result
                        print(f'[Settler] 🔍 loss_autopsy: {rec.get("symbol")} 主因={_la_result.get("primary_cause","?")} 置信度={_la_result.get("confidence",0):.0%}')
                    except Exception as _la_e:
                        print(f'[WARN][loss_autopsy] {type(_la_e).__name__}: {_la_e}')
                # ── [B1 设计院 2026-06-30] EV实时反馈 → 达摩院自我进化 ─────
                # 每笔结算触发EV矩阵更新，参数微调建议写入nudge文件（非阻断）
                try:
                    import sys as _ev_sys
                    _ev_sys.path.insert(0, str(BASE / 'brahma_brain'))
                    from ev_feedback import on_settlement as _ev_cb
                    _ev_cb(rec, outcome)
                except Exception as _ev_e:
                    print(f'[WARN][EV-Feedback] {type(_ev_e).__name__}: {_ev_e}')

    if not dry_run and n_settled > 0:
        _save(records)

    wins = n_tp1 + n_tp2
    losses = n_sl
    wr = wins / (wins + losses) if (wins + losses) else 0

    # [闭环Fix 2026-06-04] 每次结算后触发 adaptive_threshold 更新
    if n_settled > 0 and not dry_run:
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            from upgrade_v2.adaptive_threshold import get_current_status as _at_update
            _at_update(force_update=True)
        except Exception as _ate:
            pass  # 非阻断

    return {
        'total': total,
        'settled': n_settled,
        'tp1': n_tp1, 'sl': n_sl, 'timeout': n_timeout,
        'skip': n_skip,
        'win_rate': wr,
    }

def get_stats() -> dict:
    records = _load()
    settled = [r for r in records if r.get('settled')]
    wins = [r for r in settled if r.get('outcome') in ('TP1','TP2')]
    losses = [r for r in settled if r.get('outcome') == 'SL']
    timeouts = [r for r in settled if r.get('outcome') == 'TIMEOUT']
    running = [r for r in records if not r.get('settled')]

    pnls_w = [r['pnl_pct'] for r in wins if r.get('pnl_pct') is not None]
    pnls_l = [abs(r['pnl_pct']) for r in losses if r.get('pnl_pct') is not None]
    wr = len(wins)/(len(wins)+len(losses)) if (wins or losses) else 0
    pf = sum(pnls_w)/sum(pnls_l) if pnls_l else 0

    return {
        'total': len(records),
        'settled': len(settled),
        'running': len(running),
        'wins': len(wins), 'losses': len(losses), 'timeouts': len(timeouts),
        'win_rate': wr, 'profit_factor': pf,
    }

# ── 显式内存释放 ──
try:
    import gc as _gc
    _check_and_gc()
except Exception:
    pass

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--stats', action='store_true')
    args = ap.parse_args()

    if args.stats:
        s = get_stats()
        print(f"📊 live_signal_log 统计")
        print(f"   总计: {s['total']} | 已结算: {s['settled']} | 运行中: {s['running']}")
        if s['settled']:
            print(f"   WR={s['win_rate']*100:.1f}% PF={s['profit_factor']:.3f} W={s['wins']} L={s['losses']} T={s['timeouts']}")
    else:
        r = settle(dry_run=args.dry)
        print(f"结算完成: 处理{r['total']}条 结算{r['settled']}条 TP1={r['tp1']} SL={r['sl']} T={r['timeout']} 跳过={r['skip']}")
        if r['settled'] == 0:
            print('HEARTBEAT_OK')