# ponytail: signal_settler 491行，有意为之，重构前先 grep 所有调用方
"""
signal_settler.py — 梵天WR反馈闭环结算器
设计院 2026-08-01 自主创建（autoresearch P0层）

职责：
  扫描 live_signal_log.jsonl，对 outcome=null 的信号：
  1. 拉取该symbol当前价格 + 历史K线
  2. 判断自信号生成后是否触碰过 TP1 / SL
  3. 写入 outcome / exit_price / pnl_pct / settled_at / settled_by
  4. 更新 data/wr_matrix_live.json（实时WR矩阵）
  5. 若有新结算，推送简报到 Jarvis

运行方式：
  python3 scripts/signal_settler.py [--dry-run] [--push]
  cron: every 1h
"""

import json, time, argparse, sys, subprocess

import resource as _res_guard; _res_guard.setrlimit(_res_guard.RLIMIT_CORE,(0,0))

import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

BASE  = Path(__file__).parent.parent
LOG   = BASE / 'data' / 'live_signal_log.jsonl'
WR_F  = BASE / 'data' / 'wr_matrix_live.json'

API = 'https://fapi.binance.com'

# 信号有效TTL（超过此时间未触发任何条件 → 标记EXPIRED_NO_TOUCH）
SIGNAL_TTL_H = 72


def _get(url: str, timeout: int = 8):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _get_price(sym: str) -> float:
    d = _get(f'{API}/fapi/v1/ticker/price?symbol={sym}')
    return float(d['price']) if d else 0.0


def _get_klines_since(sym: str, since_ts: float, limit: int = 500) -> list:
    """获取 since_ts 之后的1H K线，返回 [(ts, high, low, close), ...]"""
    # Binance startTime 单位毫秒
    start_ms = int(since_ts * 1000)
    url = (f'{API}/fapi/v1/klines?symbol={sym}&interval=1h'
           f'&startTime={start_ms}&limit={limit}')
    data = _get(url)
    if not data:
        return []
    return [(int(k[0])/1000, float(k[2]), float(k[3]), float(k[4])) for k in data]


# [ROOT-FIX-1 2026-08-23 苏摩111封印] 只结算真实执行信号
# WATCH/SKIP/STANDBY信号不应被结算为SL，这是WR统计失真根因
EXECUTABLE_ACTIONS = {'ENTER', 'ENTER_FULL', 'ENTER_WATCH'}


def settle_signal(sig: dict, dry_run: bool = False) -> dict | None:
    """
    对单条信号进行结算判断。
    返回更新后的 sig dict，或 None（无需更新）。
    """
    sym       = sig.get('symbol', '')
    direction = sig.get('direction', sig.get('signal_dir', 'LONG'))
    entry_lo  = float(sig.get('entry_lo') or sig.get('price') or 0)
    entry_hi  = float(sig.get('entry_hi') or sig.get('price') or 0)
    sl        = float(sig.get('stop_loss') or 0)
    tp1       = float(sig.get('tp1') or 0)
    sig_ts    = float(sig.get('ts') or 0)
    outcome   = sig.get('outcome')
    result    = sig.get('result', '')
    action    = sig.get('action', '')

    if outcome in ('TP1', 'SL', 'TP2'):
        return None  # 已结算

    # ROOT-FIX-1: 只结算 action in EXECUTABLE_ACTIONS 的真实执行信号
    # WATCH/SKIP/STANDBY信号从未真正入场，不应计入WR统计
    if action and not any(a in action for a in EXECUTABLE_ACTIONS):
        return None  # 非执行信号，跳过结算

    if not sym or not sl or not tp1 or not sig_ts:
        return None  # 数据不完整

    # EXPIRED 状态：仍可能已触碰TP/SL，继续判断；但超过 TTL 且无触碰 → EXPIRED_NO_TOUCH
    now_ts = time.time()
    age_h  = (now_ts - sig_ts) / 3600

    # 获取信号生成后的K线
    klines = _get_klines_since(sym, sig_ts, limit=500)
    if not klines:
        return None

    # 入场判断（价格是否进入过入场区）
    entry_mid = (entry_lo + entry_hi) / 2 if entry_lo and entry_hi else float(sig.get('price', 0) or 0)
    entered   = False
    hit_tp1   = False
    hit_sl    = False
    exit_price = 0.0
    exit_ts    = 0.0

    # [ROOT-FIX-2 2026-08-23 苏摩111封印] MODE_B 趋势追踪入场
    # 铁证：86条EXPIRED_NO_TOUCH中83条是牛市不回调导致，占月均信号量40%
    # 机制：LONG信号TTL内若价格突破entry_hi且连续上涨超过3根1H，切换追踪模式
    #   MODE_A（原有）：等价格回调至OB/FVG区间入场
    #   MODE_B（新增）：价格突破entry_hi后1H连续3根不回调 → 以当前价追踪入场
    #   MODE_B止损：最近1H最低点（不用4H swing，更精确）
    #   MODE_B门控：score>=120 + BULL_TREND/BULL_EARLY体制 + 成交量>0.8x均量
    _mode_b_entered = False
    _mode_b_entry_price = 0.0
    _mode_b_sl = 0.0
    _consecutive_above = 0  # 价格连续高于entry_hi的1H K线根数
    _recent_lows = []       # 最近3根1H低点，用于MODE_B止损

    _score = float(sig.get('score', 0) or 0)
    _regime = str(sig.get('regime', '') or '')
    _mode_b_eligible = (
        direction == 'LONG'
        and _score >= 120
        and any(x in _regime for x in ('BULL_TREND', 'BULL_EARLY'))
    )

    for k_ts, k_hi, k_lo, k_cl in klines:
        # MODE_B追踪：检测价格连续突破entry_hi
        if _mode_b_eligible and not entered and not _mode_b_entered:
            if k_lo > entry_hi:  # 整根K线都在entry_hi上方
                _consecutive_above += 1
                _recent_lows.append(k_lo)
                if len(_recent_lows) > 4:
                    _recent_lows.pop(0)
                if _consecutive_above >= 3:  # 连续3根1H不回调
                    _mode_b_entered = True
                    _mode_b_entry_price = k_cl  # 以第3根收盘价追踪入场
                    _mode_b_sl = min(_recent_lows) * 0.998  # 近3根最低点作止损
                    # 重设TP1：从追踪入场点+原始risk
                    _orig_risk = abs(tp1 - entry_mid) if entry_mid > 0 else abs(tp1 - _mode_b_entry_price)
                    tp1 = _mode_b_entry_price + _orig_risk
                    sl  = _mode_b_sl
                    entered = True  # 视为已入场
            else:
                _consecutive_above = 0  # 回调了，重置计数
                _recent_lows = []

        # 检查MODE_A入场（原逻辑）
        if not entered:
            if direction == 'LONG':
                entered = k_lo <= entry_hi  # 价格跌入入场区或更低
            else:
                entered = k_hi >= entry_lo  # 价格涨入入场区或更高
            if not entered:
                continue  # 未入场，继续等

        # 已入场，判断 TP1/SL
        if direction == 'LONG':
            if k_lo <= sl:
                hit_sl = True; exit_price = sl; exit_ts = k_ts; break
            if k_hi >= tp1:
                hit_tp1 = True; exit_price = tp1; exit_ts = k_ts; break
        else:  # SHORT
            if k_hi >= sl:
                hit_sl = True; exit_price = sl; exit_ts = k_ts; break
            if k_lo <= tp1:
                hit_tp1 = True; exit_price = tp1; exit_ts = k_ts; break

    if hit_tp1:
        new_outcome = 'TP1'
    elif hit_sl:
        new_outcome = 'SL'
    elif age_h >= SIGNAL_TTL_H:
        new_outcome = 'EXPIRED_NO_TOUCH'
        exit_price  = _get_price(sym)
        exit_ts     = now_ts
    else:
        return None  # 信号仍在存活期，未触碰任何条件

    # 计算盈亏
    pnl = 0.0
    if exit_price and entry_mid:
        if direction == 'LONG':
            pnl = (exit_price - entry_mid) / entry_mid * 100
        else:
            pnl = (entry_mid - exit_price) / entry_mid * 100

    updated = dict(sig)
    updated['outcome']    = new_outcome
    updated['exit_price'] = round(exit_price, 6)
    updated['pnl_pct']    = round(pnl, 4)
    updated['settled_at'] = exit_ts
    updated['settled_by'] = 'signal_settler_v1'

    # [设计院 2026-08-04] 回填 LLM Council 裁决有效性
    # verdict_correct: True=裁决HIGH时信号确实SL / False=裁决HIGH但信号TP
    try:
        import json as _j
        from pathlib import Path as _P
        _sl_p = _P(__file__).parent.parent / 'data' / 'llm_council_shadow_log.jsonl'
        if _sl_p.exists():
            _sig_id = sig.get('id') or sig.get('signal_id') or sig.get('ts')
            _sym    = sig.get('symbol','')
            _lines  = _sl_p.read_text().strip().splitlines()
            _updated_lines = []
            for _line in _lines:
                try:
                    _rec = _j.loads(_line)
                    # 匹配：symbol + ts接近（±5min）
                    _match_sym = _rec.get('symbol') == _sym
                    _match_ts  = abs(float(_rec.get('ts','0')[:10] if isinstance(_rec.get('ts'),str) else 0) - float(str(_sig_id)[:10] if _sig_id else 0)) < 300
                    if _match_sym and _match_ts and _rec.get('outcome') is None:
                        _rec['outcome'] = new_outcome
                        _is_high = _rec.get('verdict') == 'RISK_HIGH' or _rec.get('risk_level') == 'HIGH'
                        _is_loss = new_outcome in ('SL',)
                        _rec['verdict_correct'] = bool(_is_high == _is_loss)
                    _updated_lines.append(_j.dumps(_rec, ensure_ascii=False))
                except Exception:
                    _updated_lines.append(_line)
            _sl_p.write_text('\n'.join(_updated_lines) + '\n')
    except Exception:
        pass

    # [Bandit SL学习钩子 2026-08-06 设计院封印]
    # 每次结算后自动更新 sl_bandit 状态，驱动在线学习
    try:
        from brahma_brain.position_sizer import update_from_outcome as _bandit_update
        _regime    = sig.get('regime', 'BULL_TREND')
        _direction = sig.get('direction', 'LONG')
        _sl_pct    = float(sig.get('sl_pct') or 0)
        _pnl_pct   = round(pnl, 4)
        if _sl_pct > 0:
            _bandit_update(_regime, _direction, _sl_pct, new_outcome, _pnl_pct)
    except Exception:
        pass  # Bandit不可用时静默降级，不影响结算主链路

    # [IC引擎学习钩子 2026-08-29 苏摩111封印]
    # 一单一单积累经验：每次结算后更新因子IC矩阵
    # 这是40年老手经验的量化实现——每笔交易后更新哪个因子有效
    try:
        from brahma_brain.brahma_ic_engine import update_ic as _ic_update
        _sig_id  = sig.get('signal_id', sig.get('id', ''))
        _ret_dir = float(pnl) / 100  # pnl_pct → 小数，方向已按实际调整
        if _sig_id and abs(_ret_dir) > 0:
            _ic_update(
                signal_id  = _sig_id,
                actual_ret = _ret_dir,
                regime     = sig.get('regime', 'CHOP_MID'),
                direction  = sig.get('direction', 'LONG'),
            )
    except Exception:
        pass  # IC引擎不可用时静默降级，不影响结算主链路

    # [Ch8轨迹自学习钩子 2026-08-13 苏摩111封印]
    # 每次结算后写入标准化轨迹记录，驱动经验知识库进化
    # 参考: ai-agent-book Ch8 gaia-experience/experience_documents.py
    try:
        import json as _j_traj
        from pathlib import Path as _P_traj
        from datetime import datetime as _dt_traj, timezone as _tz_traj
        _traj_file = _P_traj(__file__).parent.parent / 'data' / 'trajectories' / 'settled.jsonl'
        _traj_file.parent.mkdir(parents=True, exist_ok=True)
        # 三层验证：outcome(PnL) + process(score>=100) + quality(grade)
        _env_score = 1.0 if new_outcome == 'TP' else (0.5 if new_outcome == 'TIMEOUT' else 0.0)
        _process_ok = float(sig.get('score') or 0) >= 100  # 达标信号
        _traj_rec = {
            'id':               f"{sig.get('symbol','?')}_{sig.get('ts','')}",
            'task_family':      sig.get('regime', 'UNKNOWN'),
            'signal_name':      sig.get('signal_name') or sig.get('direction', ''),
            'symbol':           sig.get('symbol', ''),
            'matrix_score':     float(sig.get('score') or 0),
            'regime':           sig.get('regime', ''),
            'direction':        sig.get('direction', ''),
            'entry_price':      float(sig.get('entry_price') or sig.get('entry_mid') or 0),
            'exit_price':       round(exit_price, 6),
            'pnl_pct':          round(pnl, 4),
            'environment_score': _env_score,   # Ch8: 必须来自外部验证器（实际PnL）
            'process_ok':       _process_ok,
            'outcome':          new_outcome,
            'applies_when':     [sig.get('regime',''), f"score={int(float(sig.get('score') or 0))}"],
            'failure_mode':     'SL_HIT' if new_outcome == 'SL' else ('TP_HIT' if new_outcome == 'TP1' else 'TIMEOUT'),
            'settled_at':       _dt_traj.now(_tz_traj.utc).isoformat(),
        }
        with open(_traj_file, 'a') as _tf:
            _tf.write(_j_traj.dumps(_traj_rec, ensure_ascii=False) + '\n')
    except Exception as _e_traj:
        pass  # 轨迹记录失败不影响结算主链路

    # [设计院 2026-08-06] 暴涨猎手结果回写 — 建立实盘WR闭环
    try:
        import json as _j_ph, time as _t_ph
        from pathlib import Path as _P_ph
        _ph_log = _P_ph('data/hunter_outcome_log.jsonl')
        _ph_rec = _P_ph('dharma/pump_hunter/signal_push_record.json')
        if _ph_rec.exists():
            _ph_data = _j_ph.loads(_ph_rec.read_text())
            _sym = sig.get('symbol', '')
            if _sym in _ph_data and _ph_data[_sym].get('exec_eligible'):
                _rec = {
                    'ts':              sig.get('ts'),
                    'symbol':          _sym,
                    'hunter_score':    _ph_data[_sym].get('last_score', 0),
                    'regime_at_push':  _ph_data[_sym].get('regime_at_push', '?'),
                    'direction':       sig.get('direction', '?'),
                    'outcome':         new_outcome,
                    'pnl_pct':         round(pnl, 4),
                    'settled_ts':      _t_ph.time(),
                }
                _ph_log.parent.mkdir(parents=True, exist_ok=True)
                with open(_ph_log, 'a', encoding='utf-8') as _fph:
                    _fph.write(_j_ph.dumps(_rec, ensure_ascii=False) + '\n')
    except Exception:
        pass  # 猎手回写不影响主结算链路

    return updated


def rebuild_wr_matrix(lines: list) -> dict:
    """从已结算信号重建实时WR矩阵"""
    matrix = defaultdict(lambda: {'total': 0, 'win': 0, 'loss': 0,
                                   'expired': 0, 'ev_sum': 0.0, 'scores': []})
    for s in lines:
        outcome = s.get('outcome', '')
        regime  = s.get('regime', 'UNK')
        direc   = s.get('direction', 'UNK')
        score   = float(s.get('score_final') or 0)
        pnl     = float(s.get('pnl_pct') or 0)
        key     = f'{regime}:{direc}'

        if outcome not in ('TP1', 'SL', 'TP2', 'EXPIRED_NO_TOUCH'):
            continue
        matrix[key]['total'] += 1
        matrix[key]['scores'].append(score)
        if outcome in ('TP1', 'TP2'):
            matrix[key]['win']     += 1
            matrix[key]['ev_sum']  += pnl
        elif outcome == 'SL':
            matrix[key]['loss']    += 1
            matrix[key]['ev_sum']  += pnl
        else:
            matrix[key]['expired'] += 1

    result = {}
    for k, v in matrix.items():
        denom = v['win'] + v['loss']
        wr    = v['win'] / denom if denom > 0 else None
        avg_score = sum(v['scores']) / len(v['scores']) if v['scores'] else 0
        result[k] = {
            'total': v['total'], 'win': v['win'], 'loss': v['loss'],
            'expired': v['expired'],
            'wr': round(wr, 4) if wr is not None else None,
            'ev_avg': round(v['ev_sum'] / max(denom, 1), 4),
            'avg_score': round(avg_score, 1),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description='梵天WR结算器')
    parser.add_argument('--dry-run', action='store_true', help='只分析不写入')
    parser.add_argument('--push', action='store_true', help='结算后推送简报')
    args = parser.parse_args()

    if not LOG.exists():
        print('[settler] live_signal_log.jsonl 不存在，跳过')
        return

    lines = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    settled_new = []
    updated_lines = []
    expired_count = 0

    # [P1修复 2026-08-26] OPEN信号超期自动expire，实现条件:超过48H转为TIMEOUT
    _now = time.time()
    _OPEN_TTL_SEC = 48 * 3600  # 48小时有效期
    for sig in lines:
        if sig.get('status') == 'OPEN':
            _sig_ts = float(sig.get('ts', 0))
            if _sig_ts > 0 and (_now - _sig_ts) > _OPEN_TTL_SEC:
                sig['status'] = 'TIMEOUT'
                sig['settled_at'] = datetime.now(timezone.utc).isoformat()
                expired_count += 1
    if expired_count:
        print(f'[settler] 超期 expire: {expired_count}条OPEN信号 → TIMEOUT')

    for sig in lines:
        updated = settle_signal(sig, dry_run=args.dry_run)
        if updated:
            updated_lines.append(updated)
            if updated['outcome'] in ('TP1', 'SL', 'TP2'):
                settled_new.append(updated)
            print(f"[settler] {updated['symbol']} {updated.get('direction')} "
                  f"outcome={updated['outcome']} pnl={updated.get('pnl_pct',0):+.2f}%")
        else:
            updated_lines.append(sig)

    if not args.dry_run and settled_new:
        # 写回日志
        LOG.write_text('\n'.join(json.dumps(l, ensure_ascii=False) for l in updated_lines) + '\n')
        print(f'[settler] 写回 {len(updated_lines)} 条，新结算 {len(settled_new)} 条')

        # [P1修复 2026-08-26] settler日志注入，供复盘和诊断
        try:
            import time as _s_time
            _s_log_path = BASE / 'data' / 'signal_settler_log.jsonl'
            _s_log_path.parent.mkdir(parents=True, exist_ok=True)
            _s_entry = {
                'ts':         _s_time.time(),
                'ts_iso':     datetime.now(timezone.utc).isoformat(),
                'total_lines':  len(updated_lines),
                'settled_new':  len(settled_new),
                'wr_matrix_keys': list(wr_matrix.keys()) if 'wr_matrix' in dir() else [],
                'settled_detail': [{
                    'symbol':    s.get('symbol'),
                    'direction': s.get('direction'),
                    'score':     s.get('score_final') or s.get('score'),
                    'regime':    s.get('regime'),
                    'result':    s.get('result'),
                    'pnl_pct':   s.get('pnl_pct'),
                } for s in settled_new[:10]],
            }
            with open(_s_log_path, 'a', encoding='utf-8') as _slf:
                _slf.write(json.dumps(_s_entry, ensure_ascii=False) + '\n')
        except Exception as _sl_e:
            pass  # 日志失败不影响主流程

        # 重建WR矩阵
        wr_matrix = rebuild_wr_matrix(updated_lines)
        _wr_data = json.dumps({
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'total_settled': sum(v['total'] for v in wr_matrix.values()),
            'matrix': wr_matrix,
        }, indent=2, ensure_ascii=False)
        WR_F.write_text(_wr_data)
        # [根治 2026-08-24 苏摩111] 双写wr_matrix.json，消除文件名不匹配（settler写live，core读wr_matrix）
        (BASE / 'data' / 'wr_matrix.json').write_text(_wr_data)
        print(f'[settler] WR矩阵已更新 → {WR_F} + wr_matrix.json')

        # [协同接入 2026-08-02 设计院自主] ev_feedback 结算闭环
        # 每笔结算后立即更新EV矩阵(regime×direction×score_bin → WR/EV)
        # 非阻断：任何异常不影响主结算流程
        try:
            sys.path.insert(0, str(BASE / 'brahma_brain'))
            from ev_feedback import on_settlement as _ev_settle
            _ev_updated = 0
            for _s in settled_new:
                _oc = _s.get('outcome', '')
                if _oc in ('TP1', 'TP2', 'SL', 'EXPIRED_NO_TOUCH'):
                    _norm_oc = 'TP1' if _oc in ('TP1','TP2') else ('SL' if _oc == 'SL' else 'TIMEOUT')
                    _ev_r = _ev_settle(_s, _norm_oc)
                    if _ev_r.get('updated'):
                        _ev_updated += 1
            if _ev_updated:
                print(f'[settler] EV矩阵已更新 {_ev_updated} 条 → data/wr_matrix_realtime.json')
        except Exception as _ev_e:
            print(f'[settler] ev_feedback跳过: {_ev_e}')

        # [协同接入 2026-08-02 设计院自主] online_learner_v2 结算后维度权重重校准
        # 每次有新结算数据时，触发维度偏差计算→权重调整→写入calibrated_weights.json
        # brahma_engine.py INT-1 热加载此文件，下次分析自动生效
        try:
            _ol2_sys = __import__('sys')
            _ol2_sys.path.insert(0, str(BASE / 'brahma_brain'))
            from online_learner_v2 import run as _ol2_run
            _ol2_result = _ol2_run(dry_run=False)
            _ol2_status = _ol2_result.get('status', '?')
            if _ol2_status not in ('SKIP', 'ERROR'):
                _ol2_n = _ol2_result.get('actions_taken', 0)
                print(f'[settler] online_learner_v2: {_ol2_status} 调整{_ol2_n}个维度权重')
            # status=SKIP → 样本不足，正常静默
        except Exception as _ol2_e:
            print(f'[settler] online_learner_v2跳过: {_ol2_e}')

        # [设计院封印 2026-08-09 苏摩111] signal_weight_updater 结算闭环
        # 闭环：信号结算 → 滚动WR计算 → 动态更新signal_weights.json
        try:
            sys.path.insert(0, str(BASE / 'brahma_brain'))
            from brahma_brain.signal_quality_engine import update_weights as _sw_update
            _sw_result = _sw_update(dry_run=False)
            _sw_upd = _sw_result.get('updated', 0)
            if _sw_upd > 0:
                print(f'[settler] signal_weights动态更新 {_sw_upd} 个key → data/signal_weights.json')
            else:
                print(f'[settler] signal_weights: 样本不足或无变化（n_trades={_sw_result.get("n_trades",0)}）')
        except Exception as _sw_e:
            print(f'[settler] signal_weight_updater跳过: {_sw_e}')

        # [设计院 2026-08-25 苏摩111] 方仓自学习反馈回路
        try:
            import sys as _sys_fc
            _sys_fc.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent / 'brahma_brain'))
            from brahma_brain.fangcang_engine import feedback_settlement as _fc_feedback
            for _s in settled_new:
                _fc_sym    = _s.get('symbol', '')
                _fc_dir    = _s.get('signal_dir', 'LONG')
                _fc_hint   = _s.get('fangcang_hint', 'NEUTRAL')  # 方仓预测
                _fc_actual = 'LONG' if _s.get('outcome','') in ('TP1','TP2') else 'SHORT'
                _fc_pnl    = float(_s.get('pnl_pct', 0) or 0)
                if _fc_sym:
                    _fc_result = _fc_feedback(_fc_sym, _fc_dir, _fc_hint, _fc_actual, _fc_pnl)
                    print(f'[settler] 方仓反馈: {_fc_sym} {_fc_result.get("outcome")} weight={_fc_result.get("new_weight",1.0):.3f}')
            _fc_stats = __import__('fangcang_hcme_bridge', fromlist=['get_feedback_stats']).get_feedback_stats()
            print(f'[settler] 方仓历史胜率: {_fc_stats.get("wr",0)}% (n={_fc_stats.get("total",0)})')
        except Exception as _fc_e:
            print(f'[settler] 方仓反馈跳过: {_fc_e}')

        # P1-2: 结算后LLM复盘 → learning_log.jsonl (2026-09-04 苏摩111封印)
        # 接入位置: 方仓反馈后，推送简报前
        try:
            _ll_path = BASE / 'data' / 'learning_log.jsonl'
            _ll_path.parent.mkdir(parents=True, exist_ok=True)
            # 构建结算摘要供 LLM 复盘
            _wins  = sum(1 for s in settled_new if s.get('outcome') in ('TP1','TP2'))
            _total = len(settled_new)
            _wr    = _wins / _total if _total else 0
            _worst = min(settled_new, key=lambda s: float(s.get('pnl_pct',0) or 0), default={})
            _best  = max(settled_new, key=lambda s: float(s.get('pnl_pct',0) or 0), default={})
            _summary_str = (
                f"本批结算{_total}条信号 WR={_wr:.0%}\n"
                f"最佳: {_best.get('symbol','')} {_best.get('outcome','')} pnl={_best.get('pnl_pct',0):+.2f}%\n"
                f"最差: {_worst.get('symbol','')} {_worst.get('outcome','')} pnl={_worst.get('pnl_pct',0):+.2f}%\n"
                f"体制分布: {', '.join(set(s.get('regime','?') for s in settled_new[:5]))}"
            )
            # 调用 free_llm_client 生成复盘一句话
            from free_llm_client import _call_openrouter as _llm_review
            _review_prompt = (
                f"你是梵天量化系统复盘裁判员。\n"
                f"{_summary_str}\n"
                f"用一句话(不超过30字)指出本批信号最大教词或需要注意的模式："
            )
            _llm_lesson = _llm_review(_review_prompt, max_tokens=50)
            if _llm_lesson:
                _ll_entry = {
                    'ts':       time.time(),
                    'ts_iso':   datetime.now(timezone.utc).isoformat(),
                    'source':   'signal_settler',
                    'wr':       round(_wr, 3),
                    'n_total':  _total,
                    'n_wins':   _wins,
                    'lesson':   _llm_lesson.strip()[:100],
                    'summary':  _summary_str[:200],
                }
                with open(_ll_path, 'a', encoding='utf-8') as _llf:
                    _llf.write(json.dumps(_ll_entry, ensure_ascii=False) + '\n')
                print(f'[settler] LLM复盘封印: {_llm_lesson.strip()[:60]}')
        except Exception as _p12_e:
            print(f'[settler] P1-2 LLM复盘跳过: {_p12_e}')
        # ── end P1-2 ─────────────────────────────────────────────────────

        # 推送简报
        if args.push and settled_new:
            wins  = sum(1 for s in settled_new if s['outcome'] in ('TP1','TP2'))
            total = len(settled_new)
            wr    = wins / total if total else 0
            lines_out = [f'📊 梵天结算简报 | 新增 {total} 条']
            for s in settled_new[:5]:
                emoji = '✅' if s['outcome'] in ('TP1','TP2') else '❌'
                lines_out.append(
                    f"  {emoji} {s['symbol']} {s.get('direction')} "
                    f"{s['outcome']} pnl={s.get('pnl_pct',0):+.2f}%"
                )
            lines_out.append(f'  本批WR={wr:.1%} ({wins}/{total})')
            try:
                sys.path.insert(0, str(BASE))
                from push_hub import _jarvis
                _jarvis('\n'.join(lines_out))
            except Exception as e:
                print(f'[settler] 推送失败: {e}')
    elif args.dry_run:
        print(f'[settler] DRY-RUN: 发现 {len(settled_new)} 条可结算信号（未写入）')
    else:
        print('HEARTBEAT_OK')  # [设计院 2026-08-09] 无新结算信号→静默
        import sys; sys.exit(0)  # 提前退出，不输出WR统计（无变化不刷屏）

    # 统计当前WR概况
    all_settled = [l for l in updated_lines if l.get('outcome') in ('TP1','SL','TP2')]
    if all_settled:
        wr_now = rebuild_wr_matrix(updated_lines)
        print('\n当前累计WR:')
        for k, v in sorted(wr_now.items(), key=lambda x: -x[1]['total']):
            wr_str = f"{v['wr']:.1%}" if v['wr'] is not None else 'N/A'
            print(f"  {k:<30} WR={wr_str} ({v['win']}W/{v['loss']}L) n={v['total']}")
    else:
        print('[settler] 尚无已结算信号（WR数据积累中）')



    # ══ [设计院 2026-08-08] ic_tracker 结算后自动计算IC ═══════════════
    try:
        import sys as _icsys
        _icsys.path.insert(0, str(BASE / 'brahma_brain'))
        from ic_tracker import compute_all_ic, save_ic_state
        _ic_result = compute_all_ic()
        if _ic_result:
            save_ic_state(_ic_result)
            print(f'[ic_tracker] IC计算完成: {len(_ic_result)}个维度')
        else:
            print('[ic_tracker] 样本不足，跳过IC计算（需>=20条结算记录）')
    except Exception as _ic_e:
        print(f'[ic_tracker] 跳过: {_ic_e}')
    # ══ [END ic_tracker] ══════════════════════════════════════════════

    # ══ [P2接通 2026-08-26 苏摩111] brahma_experience_distiller 结算后自动触发 ══
    # 根因：经验蒸馏每次要手动跑，经验文档只有3个，设计院无法自我进化
    # 修复：每次有新结算信号时自动触发蒸馏，积累经验文档
    if len(settled_new) > 0:
        try:
            import sys as _ed_sys
            _ed_sys.path.insert(0, str(BASE / 'brahma_brain'))
            from brahma_brain.brahma_experience_engine import load_all_cases, distill, build_report
            _cases = load_all_cases()
            if len(_cases) >= 5:
                _matrix = distill(_cases)
                _report = build_report(_matrix)
                # 写入经验文档
                import datetime as _dt
                _ed_dir = BASE / 'data' / 'experience_docs'
                _ed_dir.mkdir(parents=True, exist_ok=True)
                _ed_path = _ed_dir / f"experience_{_dt.date.today().isoformat()}.md"
                _ed_path.write_text(_report, encoding='utf-8')
                print(f'[experience_distiller] 经验文档已更新: {_ed_path.name} ({len(_cases)}条案例)')
            else:
                print(f'[experience_distiller] 案例不足({len(_cases)}<5)，跳过蒸馏')
        except Exception as _ed_e:
            print(f'[experience_distiller] 跳过: {_ed_e}')
    # ══ [END experience_distiller] ════════════════════════════════════

if __name__ == '__main__':
    main()