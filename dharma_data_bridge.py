#!/usr/bin/env python3
"""
dharma_data_bridge.py · 达摩院 ↔ 梵天 数据桥接 v3.0
设计院 2026-05-30 · 三大升级：
  1. 重复信号去重（指纹机制，4H窗口）
  2. 信号门卫 M0（价格偏离过远拒绝）
  3. Signal Lineage 溯源信息
"""
import os, json, time, hashlib, pathlib
from datetime import datetime, timezone, timedelta

BASE            = os.path.dirname(os.path.abspath(__file__))
LOG_PATH        = os.path.join(BASE, 'data', 'live_signal_log.jsonl')
FEEDBACK_PATH   = os.path.join(BASE, 'data', 'dharma_feedback.json')
FP_PATH         = os.path.join(BASE, 'data', 'signal_fingerprints.json')  # 去重指纹库
LOCK_PATH       = LOG_PATH + '.lock'
pathlib.Path(os.path.dirname(LOG_PATH)).mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════════
# 去重指纹机制
# 同标的 + 同方向 + 入场区偏差<0.5% = 重复信号
# [设计院 2026-06-14 P0] 动态TTL：体制×信号质量自适应去重窗口
# ══════════════════════════════════════════════════════════════════
DEDUP_WINDOW_H  = 4     # 默认去重窗口 4小时（兜底）
DEDUP_ENTRY_TOL = 0.005 # 入场区偏差容忍度 0.5%

# [设计院 2026-06-14] 动态去重TTL矩阵（分钟）
# 原则：体制越稳定 + 信号越强 → TTL越短（不错过Alpha）
#       体制混乱 + 信号弱 → TTL越长（过滤噪音）
DEDUP_TTL_MATRIX = {
    # (体制) → {信号强度级别: TTL分钟}
    # 信号强度：A=grade≥70+score≥138, B=grade≥70, C=其他
    'BULL_TREND':      {'A': 60,  'B': 90,  'C': 240},
    'BULL_EARLY':      {'A': 60,  'B': 90,  'C': 240},
    'BULL_CORRECTION': {'A': 60,  'B': 90,  'C': 240},
    'BEAR_TREND':      {'A': 120, 'B': 180, 'C': 360},  # v360fix: A60→120 B90→180
    'BEAR_EARLY':      {'A': 60,  'B': 90,  'C': 240},
    'BEAR_RECOVERY':   {'A': 60,  'B': 90,  'C': 240},
    'CHOP_MID':        {'A': 120, 'B': 180, 'C': 360},
    'CHOP_HIGH':       {'A': 180, 'B': 240, 'C': 480},
}

def _get_dedup_ttl_min(regime: str = '', grade: float = 0, score: float = 0) -> int:
    """[设计院 2026-06-14 P0] 动态去重TTL：体制×信号强度"""
    regime_key = (regime or '').upper()
    if grade >= 70 and score >= 138:
        level = 'A'
    elif grade >= 70:
        level = 'B'
    else:
        level = 'C'
    matrix = DEDUP_TTL_MATRIX.get(regime_key, {})
    return matrix.get(level, DEDUP_WINDOW_H * 60) if matrix else DEDUP_WINDOW_H * 60

def _make_fingerprint(symbol: str, signal_dir: str, entry_lo: float, price: float) -> str:
    """生成信号指纹：标的+方向+入场区桶（0.5%精度）"""
    if price and price > 0:
        bucket = round(entry_lo / price * 200)  # 0.5%精度桶
    else:
        bucket = round(entry_lo, 2)
    raw = f"{symbol.upper()}_{signal_dir}_{bucket}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]

def _load_fingerprints() -> dict:
    """加载指纹库，自动清理过期条目"""
    try:
        if os.path.exists(FP_PATH):
            data = json.loads(open(FP_PATH).read())
            cutoff = time.time() - 8 * 3600  # 8H兜底清理（覆盖最长TTL=480min）
            cleaned = {}
            for k, v in data.items():
                # 兼容新格式(dict)和旧格式(float)
                ts = v.get('ts', 0) if isinstance(v, dict) else float(v)
                if ts > cutoff:
                    cleaned[k] = ts  # 统一存float
            return cleaned
    except Exception as _e_ignored:
        print(f'[WARN][dharma_data_bridge] {type(_e_ignored).__name__}: {_e_ignored}')
    return {}

def _save_fingerprints(fps: dict):
    try:
        tmp = FP_PATH + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(fps, f)  # fps: {fp_hash: timestamp_float} | 指纹哈希 → 时间戳浮点数字典
        os.replace(tmp, FP_PATH)
    except Exception as _e_ignored:
        print(f'[WARN][dharma_data_bridge] {type(_e_ignored).__name__}: {_e_ignored}')

def _is_duplicate(symbol: str, signal_dir: str, entry_lo: float, price: float,
                  regime: str = '', grade: float = 0, score: float = 0) -> bool:
    """[设计院 2026-06-14 P0] 动态TTL去重检查"""
    fp = _make_fingerprint(symbol, signal_dir, entry_lo, price)
    fps = _load_fingerprints()
    if fp in fps:
        age_min = (time.time() - fps[fp]) / 60
        ttl_min = _get_dedup_ttl_min(regime, grade, score)
        if age_min < ttl_min:
            print(f'[DharmaBridge] 🔄 去重 {symbol} {signal_dir} entry~{entry_lo:.2f} '
                  f'(已有相同信号{age_min:.0f}min前, TTL={ttl_min}min, 体制={regime}, grade={grade:.0f})')
            return True
        else:
            print(f'[DharmaBridge] ✅ 去重TTL已过期 {symbol} {signal_dir} '
                  f'age={age_min:.0f}min > TTL={ttl_min}min，允许重新入场')
    return False

def _register_fingerprint(symbol: str, signal_dir: str, entry_lo: float, price: float):
    fp = _make_fingerprint(symbol, signal_dir, entry_lo, price)
    fps = _load_fingerprints()
    fps[fp] = time.time()
    _save_fingerprints(fps)


# ══════════════════════════════════════════════════════════════════
#  写入信号
# ══════════════════════════════════════════════════════════════════
def log_signal(result: dict) -> bool:
    try:
        score = result.get('score_final', result.get('confluence', {}).get('total', 0))
        # [P0 2026-06-30 设计院修复] valid判断逻辑增强
        # 原因：valid_signal在部分信号生成路径中未正确传递，导致338条历史信号valid全为False
        # 修复：多级判断：(1)valid_signal (2)rr_gate=PASS (3)kelly_mult>0
        _cf = result.get('confluence', {})
        _params = result.get('params', {})
        _rr_gate = _cf.get('rr_gate', '')
        _kelly   = float(_cf.get('kelly_mult', 0) or 0)
        _params_valid = _params.get('valid', False)
        _rr1 = float(_params.get('rr1', 0) or 0)
        valid = (
            result.get('valid_signal', False)  # 原始判断
            or (_rr_gate == 'PASS' and _kelly > 0)  # rr通过+kelly正常
            or (_params_valid and _rr1 >= 1.0)        # params内部有效且RR达标
        )
        _sq_grade = result.get('confluence', {}).get('structure_grade', 0) or 0
        _action   = result.get('confluence', {}).get('action', '')

        # [v8.0-fix] 写入门控（设计院2026-06-04 结构铁律）
        # 铁律（数据铁证）：
        #   grade A(70-89) WR=100% | grade B(50-69) WR=100% | A/B级胜率均达100%
        #   grade C(25-49) WR=17%  | grade X(0-24)  WR=0%   | C级胜率17%，X级胜率0%
        # 结论：grade<70 = 噪音(B/C/X级TO率=73~100%)，无论valid是否为True，禁止写入 [v24.2]
        # 提前解析symbol/dir，避免后续引用未定义变量
        _symbol    = result.get('symbol', '')
        _sig_dir   = result.get('signal_dir', '')

        # P0修复：grade<70一律拒绝，不受valid=True豁免
        # [v24.2 2026-06-12] 从50提升至70
        # 铁证：grade 50-60 TIMEOUT率=73% | grade≥70 TIMEOUT率=8%
        # 武曲Paper干净68条统计：A级WR=92% vs B级WR=27%
        # [v8.1-fix 2026-06-07] brahma_multi_scan产生的信号structure_grade=None
        # None时使用fallback=55（B级默认），B级也被新门槛拦截
        if _sq_grade is None:
            _sq_grade = 55  # fallback：多扫信号无grade时默认B级
        if _sq_grade < 70:  # [v24.2] 50→70 | 门槛从50提升至70
            print(f'[Bridge-Gate] 🚫 {_symbol} {_sig_dir} grade={_sq_grade}<70 → B/C级TO率=73~100%，拒绝写入')
            # [Zone Watcher v1.0 2026-06-10] score≥150的高分低级信号写入待触达池
            # 逻辑：方向对、评分高，但结构暂弱 → 等价格进入入场区再重分析
            try:
                _score_for_zone = float(score or 0)
                _params_zone = result.get('params', {})
                if _score_for_zone >= 150 and _params_zone.get('entry_lo') and _params_zone.get('entry_hi'):
                    import json as _jz, time as _tz
                    from pathlib import Path as _Pz
                    _zone_path = _Pz(__file__).parent / 'data' / 'pending_zones.jsonl'

                    # ── gap合理性门：入场区距现价不得超过5% ──
                    _cur_p = result.get('price', 0) or 0
                    _elo   = float(_params_zone['entry_lo'])
                    _ehi   = float(_params_zone['entry_hi'])
                    if _cur_p > 0:
                        _gap_chk = ((_elo - _cur_p) / _cur_p * 100) if _sig_dir == 'SHORT' else ((_cur_p - _ehi) / _cur_p * 100)
                        if _gap_chk > 5.0 or _gap_chk < -2.0:
                            print(f'[ZoneWatcher] ⏭️ {_symbol} gap={_gap_chk:.1f}% 进场区偏离过大，不入池')
                            return False

                    # ── 去重：同 symbol+direction 只保留最新一条 ──
                    _existing = []
                    if _zone_path.exists():
                        for _zl in _zone_path.read_text().splitlines():
                            if _zl.strip():
                                try:
                                    _zr = _jz.loads(_zl)
                                    _zkey = f"{_zr.get('symbol')}_{_zr.get('direction')}"
                                    if _zkey != f'{_symbol}_{_sig_dir}':
                                        _existing.append(_zl)
                                except Exception as _e_ignored:
                                    print(f'[WARN][dharma_data_bridge] {type(_e_ignored).__name__}: {_e_ignored}')

                    _zone_rec = {
                        'symbol':    _symbol,
                        'direction': _sig_dir,
                        'score':     _score_for_zone,
                        'grade':     _sq_grade,
                        'regime':    result.get('regime', ''),
                        'entry_lo':  float(_params_zone['entry_lo']),
                        'entry_hi':  float(_params_zone['entry_hi']),
                        'stop_loss': float(_params_zone.get('stop_loss', 0) or 0),
                        'tp1':       float(_params_zone.get('tp1', 0) or 0),
                        'tp2':       float(_params_zone.get('tp2', 0) or 0),
                        'ts':        datetime.now(timezone.utc).isoformat(),
                        'expires_ts': _tz.time() + 86400,  # 24H过期
                        'trigger_count': 0,
                        'zone_id':   f"{_symbol}_{int(_tz.time())}",
                    }
                    _existing.append(_jz.dumps(_zone_rec, ensure_ascii=False))
                    _zone_path.write_text('\n'.join(_existing) + '\n')
                    print(f'[ZoneWatcher] 📌 {_symbol} {_sig_dir} score={_score_for_zone:.0f} grade={_sq_grade} 入待触达池 入场区=${_params_zone["entry_lo"]:.2f}~${_params_zone["entry_hi"]:.2f}')
            except Exception as _ze:
                print(f'[ZoneWatcher] ⚠️ 写入异常: {_ze}')
            return False
        if score < 120 and not valid:
            return False

        params  = result.get('params', {})
        cf      = result.get('confluence', {})
        ms      = result.get('momentum', {})
        now_utc = datetime.now(timezone.utc)

        _entry_lo  = params.get('entry_lo', 0) or 0
        _entry_hi  = params.get('entry_hi', 0) or 0
        _cur_price = result.get('price', 0) or 0

        # ── M0 门卫：价格偏离过远 ──────────────────────────────
        _gate_pass   = True
        _gate_reason = ''
        if _entry_lo and _entry_hi and _cur_price:
            _sl = params.get('stop_loss', 0) or 0
            if _sig_dir == 'SHORT':
                if _sl and _cur_price >= _sl:
                    _gate_pass   = False
                    _gate_reason = f'价格${_cur_price:.2f}已超越SL${_sl:.2f}'
                elif _cur_price > _entry_hi * 1.05:
                    _gate_pass   = False
                    _gate_reason = f'价格${_cur_price:.2f}高于入场区上沿5%+'
            elif _sig_dir == 'LONG':
                if _sl and _cur_price <= _sl:
                    _gate_pass   = False
                    _gate_reason = f'价格${_cur_price:.2f}已低于SL${_sl:.2f}'
                elif _cur_price < _entry_lo * 0.95:
                    _gate_pass   = False
                    _gate_reason = f'价格${_cur_price:.2f}低于入场区下沿5%+'

        if not _gate_pass:
            print(f'[M0-Gate] 🚫 {_symbol} {_sig_dir} 被门卫拒绝: {_gate_reason}')
            return False

        # ── 去重检查 ────────────────────────────────────────────
        if _entry_lo and _cur_price:
            _regime_for_dedup = result.get('regime', '')
            _grade_for_dedup  = float(_sq_grade or 0)
            _score_for_dedup  = float(score or 0)
            if _is_duplicate(_symbol, _sig_dir, _entry_lo, _cur_price,
                             regime=_regime_for_dedup, grade=_grade_for_dedup, score=_score_for_dedup):
                return False  # 动态TTL去重，不写日志

        # ── 分配信号ID ──────────────────────────────────────────
        raw_id = f"{_symbol}_{now_utc.isoformat()}_{score}"
        sig_id = hashlib.md5(raw_id.encode()).hexdigest()[:12]

        # ── Signal Lineage ──────────────────────────────────────
        _entry_mid = (_entry_lo + _entry_hi) / 2 if _entry_lo and _entry_hi else 0
        _dist_pct  = abs(_cur_price - _entry_mid) / _entry_mid * 100 if _entry_mid else None
        _lineage = {
            'price_used':      _cur_price,
            'price_source':    result.get('price_source', 'unknown'),
            'price_ts':        now_utc.isoformat(),
            'entry_dist_pct':  round(_dist_pct, 2) if _dist_pct else None,
            'gate_passed':     True,
        }

        record = {
            'signal_id':    sig_id,
            'ts':           now_utc.timestamp(),
            'ts_iso':       now_utc.isoformat(),
            'symbol':       _symbol,
            'signal_dir':   _sig_dir,
            'direction':    _sig_dir,   # [BUG-FIX 2026-05-30] 兼容旧读取方
            'regime':       result.get('regime'),
            'price':        _cur_price,
            'rsi_1h':       ms.get('rsi_1h'),
            'rsi_4h':       ms.get('rsi_4h'),
            'atr_1h':       ms.get('atr_1h'),
            'atr_4h':       ms.get('atr_4h'),
            'fg':           result.get('extra', {}).get('fg', {}).get('value'),
            'score':        score,
            'valid':        valid,
            'grade':        cf.get('grade'),
            'structure_grade': cf.get('structure_grade', 0),  # [v7.0-fix] 结构质量评分(0/15/35/55/72/82)，用于达摩院OOS统计和StructureGate
            'rr1':          params.get('rr1'),
            'rr_gate':      cf.get('rr_gate'),
            'entry_lo':     _entry_lo,
            'entry_hi':     _entry_hi,
            'stop_loss':    params.get('stop_loss'),
            'tp1':          params.get('tp1'),
            'tp2':          params.get('tp2'),
            'sl_pct':       params.get('sl_pct'),
            'nodes_pass':   result.get('nodes_pass', 0),
            'nodes_verdict':result.get('nodes_verdict', 'UNKNOWN'),
            'outcome':      None,
            'exit_price':   None,
            'pnl_pct':      None,
            'closed_ts':    None,
            'settled':      False,
            '_lineage':     _lineage,
            '_breakdown':   cf.get('breakdown') or None,
            # TTL v3.0 — 动态有效期（grade×体制，与live_signal_settler对齐）
            # 大样本验证: BEAR_TREND(熊市趋势) grade50-59 TTL=27H / grade70+ TTL=62H
            'generated_price': _cur_price,
        }
        # 动态计算TTL
        _grade_v = float(cf.get('structure_grade', _sq_grade) or _sq_grade)
        _regime_v = str(result.get('regime', '') or '')
        # [v24.2] grade<70已全系统封堵，此处仅对grade≥70信号执行
        if _grade_v >= 80:
            _ttl_h = 72   # S级: 72H
        elif _grade_v >= 70:
            _ttl_h = 62   # A级: 62H
        else:
            _ttl_h = 24   # 安全fallback (不应触达，grade<70已被封堵)
        _ttl_h = max(12, min(_ttl_h, 72))
        record['expires_at'] = (datetime.now(timezone.utc) + timedelta(hours=_ttl_h)).isoformat()
        record['_ttl_hours'] = _ttl_h

        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

        # 注册指纹（去重窗口4H）
        if _entry_lo and _cur_price:
            _register_fingerprint(_symbol, _sig_dir, _entry_lo, _cur_price)

        return True

    except Exception as e:
        import traceback
        print(f'[DharmaBridge] ❌ log_signal 失败: {e}')
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════════════
#  读取 / 结算 / 统计（不变）
# ══════════════════════════════════════════════════════════════════
def load_signals(settled: bool = None, symbol: str = None,
                 min_score: int = 0, limit: int = 0) -> list:
    if not os.path.exists(LOG_PATH):
        return []
    records = []
    with open(LOG_PATH, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                r = json.loads(line)
                if settled is not None and r.get('settled') != settled: continue
                if symbol and r.get('symbol','').upper() != symbol.upper(): continue
                if r.get('score', 0) < min_score: continue
                records.append(r)
            except Exception as _e_ignored:
                print(f'[WARN][dharma_data_bridge] {type(_e_ignored).__name__}: {_e_ignored}')
    if limit > 0:
        records = records[-limit:]
    return records


def settle_signal(signal_id: str, outcome: str, exit_price: float, pnl_pct: float) -> bool:
    if not os.path.exists(LOG_PATH):
        return False
    try:
        records = []
        updated = False
        with open(LOG_PATH, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                r = json.loads(line)
                if r.get('signal_id') == signal_id and not r.get('settled'):
                    r['outcome']    = outcome
                    r['result']     = outcome   # Fix-A: result与outcome保持一致
                    r['exit_price'] = round(exit_price, 4)
                    r['pnl_pct']    = round(pnl_pct, 4)
                    r['closed_ts']  = datetime.now(timezone.utc).isoformat()
                    r['settled']    = True
                    r['valid']      = False  # Fix-A: 已结算信号不再参与监控
                    updated = True
                records.append(r)
        if updated:
            tmp = LOG_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + '\n')
            os.replace(tmp, LOG_PATH)
        return updated
    except Exception as e:
        print(f'[DharmaBridge] settle_signal 失败: {e}')
        return False


def get_stats(symbol: str = None, min_score: int = 0) -> dict:
    records = load_signals(settled=True, symbol=symbol, min_score=min_score)
    if not records:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'avg_pnl': 0.0}
    wins   = [r for r in records if (r.get('pnl_pct') or 0) > 0]
    losses = [r for r in records if (r.get('pnl_pct') or 0) <= 0]
    gross_win  = sum(r['pnl_pct'] for r in wins)
    gross_loss = abs(sum(r['pnl_pct'] for r in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
    by_regime = {}
    for r in records:
        rg = r.get('regime', 'UNKNOWN')
        by_regime.setdefault(rg, []).append(r.get('pnl_pct', 0))
    regime_pf = {}
    for rg, pnls in by_regime.items():
        w = sum(p for p in pnls if p > 0)
        l = abs(sum(p for p in pnls if p <= 0))
        regime_pf[rg] = round(w / l if l > 0 else 99.0, 3)
    return {
        'n': len(records), 'n_win': len(wins), 'n_loss': len(losses),
        'wr': round(len(wins)/len(records), 4),
        'pf': round(pf, 3),
        'avg_pnl': round(sum(r.get('pnl_pct',0) for r in records)/len(records), 4),
        'by_regime': regime_pf,
        'symbol': symbol or 'ALL',
        'updated_ts': datetime.now(timezone.utc).isoformat(),
    }


if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'status':
        all_sigs  = load_signals()
        settled   = load_signals(settled=True)
        unsettled = load_signals(settled=False)
        print(f'\n📊 live_signal_log 状态')
        print(f'   总记录: {len(all_sigs)} 条')
        print(f'   已结算: {len(settled)} 条')
        print(f'   待结算: {len(unsettled)} 条')
        fps = _load_fingerprints()
        print(f'   去重指纹库: {len(fps)} 条（4H窗口）')
        if settled:
            s = get_stats()
            print(f'\n📈 已结算统计')
            print(f'   WR={s["wr"]*100:.1f}%  PF={s["pf"]:.3f}  avg={s["avg_pnl"]:+.3f}%  n={s["n"]}')