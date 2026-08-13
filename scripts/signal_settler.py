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

    if outcome in ('TP1', 'SL', 'TP2'):
        return None  # 已结算

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

    for k_ts, k_hi, k_lo, k_cl in klines:
        # 检查入场
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
        from sl_bandit import update_from_outcome as _bandit_update
        _regime    = sig.get('regime', 'BULL_TREND')
        _direction = sig.get('direction', 'LONG')
        _sl_pct    = float(sig.get('sl_pct') or 0)
        _pnl_pct   = round(pnl, 4)
        if _sl_pct > 0:
            _bandit_update(_regime, _direction, _sl_pct, new_outcome, _pnl_pct)
    except Exception:
        pass  # Bandit不可用时静默降级，不影响结算主链路

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

        # 重建WR矩阵
        wr_matrix = rebuild_wr_matrix(updated_lines)
        WR_F.write_text(json.dumps({
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'total_settled': sum(v['total'] for v in wr_matrix.values()),
            'matrix': wr_matrix,
        }, indent=2, ensure_ascii=False))
        print(f'[settler] WR矩阵已更新 → {WR_F}')

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
            from signal_weight_updater import update_weights as _sw_update
            _sw_result = _sw_update(dry_run=False)
            _sw_upd = _sw_result.get('updated', 0)
            if _sw_upd > 0:
                print(f'[settler] signal_weights动态更新 {_sw_upd} 个key → data/signal_weights.json')
            else:
                print(f'[settler] signal_weights: 样本不足或无变化（n_trades={_sw_result.get("n_trades",0)}）')
        except Exception as _sw_e:
            print(f'[settler] signal_weight_updater跳过: {_sw_e}')

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

if __name__ == '__main__':
    main()