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
╔══════════════════════════════════════════════════════════════════╗
║  梵天自进化引擎 · auto_learner.py                                ║
║  自动学习触发器 v1.0                                              ║
║                                                                  ║
║  触发条件：每满N条新结算信号                                       ║
║  执行动作：                                                       ║
║    1. 自适应门槛重算                                              ║
║    2. grade分布重统计 → SSOT更新                                  ║
║    3. 最优score区间检测 → 广播预警                                 ║
║    4. 体制WR监控 → 异常报警                                       ║
║                                                                  ║
║  设计院 v1.0 · 2026-06-05                                        ║
╚══════════════════════════════════════════════════════════════════╝
"""
import json, sys, os, subprocess
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

# ── 苏摩·能量门控 P4 ──
try:
    import sys as _s; _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from soma_manager import can_run as _can
    if not _can(priority=4, task='auto_learner'):
        print('[Soma] 跳过: auto_learner'); import sys; sys.exit(0)
except Exception:
    pass
# ── end soma ──

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
NOW  = datetime.now(timezone.utc)

W='\033[0m'; G='\033[92m'; Y='\033[93m'; R='\033[91m'; BOLD='\033[1m'

TRIGGER_EVERY_N = 10   # 每10条结算触发一次学习


def load_settled() -> list:
    """加载所有已结算信号"""
    recs = []
    path = DATA / 'live_signal_log.jsonl'
    if not path.exists():
        return recs
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                r = json.loads(line)
                if r.get('outcome') and not r.get('_data_quality'):
                    # [P1-3 设计院 2026-06-24] grade字段可能是中文字符串('🟠极强'/'🔴神级')
                    # 需要先做字符串→数值映射，否则 float() 抛异常导致整行被吞
                    _grade_raw = r.get('structure_grade') or r.get('grade') or 0
                    _grade_map = {'x级':10,'x':10,'c级':35,'c':35,'b级':60,'b':60,
                                  'a级':75,'a':75,'s级':95,'s':95,
                                  '极弱':10,'弱':35,'一般':60,'强':70,'极强':80,'神级':95,
                                  '🔴神级':95,'🟠极强':80,'🟡强':70,'🟢一般':60,'⚪弱':35}
                    if isinstance(_grade_raw, str):
                        _grade_clean = _grade_raw.strip().lower().lstrip('🔴🟠🟡🟢⚪ ')
                        _grade_num = _grade_map.get(_grade_clean,
                                     _grade_map.get(_grade_raw.strip(), 50))
                    else:
                        try: _grade_num = float(_grade_raw)
                        except: _grade_num = 50
                    r['grade'] = _grade_num
                    r['score'] = float(r.get('score') or 0)
                    recs.append(r)
            except Exception:
                pass
    return recs


def load_learner_state() -> dict:
    path = DATA / 'auto_learner_state.json'
    if path.exists():
        try: return json.load(open(path))
        except: pass
    return {'last_n': 0, 'last_run': None, 'runs': 0}


def save_learner_state(state: dict):
    (DATA / 'auto_learner_state.json').write_text(
        json.dumps(state, indent=2, ensure_ascii=False)
    )


# ─── 学习任务1：grade分布统计 ────────────────────────────────────
def analyze_grade_distribution(settled: list) -> dict:
    grade_stats = defaultdict(lambda: {'n': 0, 'wins': 0, 'losses': 0})
    for r in settled:
        g = int(r['grade'])
        if g < 25:   seg = 'X'
        elif g < 50: seg = 'C'
        elif g < 70: seg = 'B'
        elif g < 90: seg = 'A'
        else:        seg = 'S'

        grade_stats[seg]['n'] += 1
        outcome = r.get('outcome', '')
        # [P1-3 设计院 2026-06-24] 修复WIN判断：实盘无TP1/TP2，用pnl_pct或result字段
        _is_win = (outcome in ('TP1','TP2') or
                   r.get('result') == 'WIN' or
                   (r.get('pnl_pct') is not None and r['pnl_pct'] > 0))
        _is_loss = (outcome in ('SL','SL_BREACHED') or
                    r.get('result') == 'LOSS' or
                    (r.get('pnl_pct') is not None and r['pnl_pct'] <= 0 and outcome not in ('REGIME_EXPIRED','PRICE_EXPIRED','EXPIRED')))
        if _is_win:
            grade_stats[seg]['wins'] += 1
        elif _is_loss:
            grade_stats[seg]['losses'] += 1

    result = {}
    for seg in ['X', 'C', 'B', 'A', 'S']:
        v = grade_stats[seg]
        total = v['wins'] + v['losses']
        wr = v['wins'] / total * 100 if total > 0 else 0
        result[seg] = {'n': v['n'], 'wins': v['wins'], 'losses': v['losses'], 'wr': round(wr, 1)}
    return result


# ─── 学习任务2：最优score区间检测 ────────────────────────────────
def find_optimal_score_range(settled: list) -> dict:
    score_stats = defaultdict(lambda: {'n': 0, 'wins': 0, 'losses': 0})
    for r in settled:
        sc = int(r['score'] // 10) * 10  # 10分段
        score_stats[sc]['n'] += 1
        # [P1-3] 用pnl_pct判断胜负
        _pnl = r.get('pnl_pct'); _res = r.get('result',''); _oc = r.get('outcome','')
        if _oc in ('TP1','TP2') or _res=='WIN' or (_pnl is not None and _pnl > 0):
            score_stats[sc]['wins'] += 1
        elif _oc in ('SL','SL_BREACHED') or _res=='LOSS' or (_pnl is not None and _pnl <= 0 and _oc not in ('REGIME_EXPIRED','PRICE_EXPIRED','EXPIRED')):
            score_stats[sc]['losses'] += 1

    best_range = None
    best_wr = 0
    for sc, v in sorted(score_stats.items()):
        total = v['wins'] + v['losses']
        if total < 3: continue
        wr = v['wins'] / total * 100
        if wr > best_wr:
            best_wr = wr
            best_range = (sc, sc + 10)

    return {'best_range': best_range, 'best_wr': best_wr, 'score_stats': dict(score_stats)}


# ─── 学习任务3：体制WR异常检测 ───────────────────────────────────
def detect_regime_anomaly(settled: list) -> list:
    regime_stats = defaultdict(lambda: {'n': 0, 'wins': 0, 'losses': 0})
    for r in settled:
        reg = r.get('regime', 'UNKNOWN')
        regime_stats[reg]['n'] += 1
        # [P1-3] 用pnl_pct判断胜负
        _pnl = r.get('pnl_pct')
        _res = r.get('result','')
        _oc  = r.get('outcome','')
        if _oc in ('TP1','TP2') or _res=='WIN' or (_pnl is not None and _pnl > 0):
            regime_stats[reg]['wins'] += 1
        elif r.get('outcome') == 'SL':
            regime_stats[reg]['losses'] += 1

    alerts = []
    for reg, v in regime_stats.items():
        total = v['wins'] + v['losses']
        if total < 3: continue
        wr = v['wins'] / total * 100
        if wr < 40:
            alerts.append(f'⚠️ 体制{reg} WR={wr:.0f}% ({total}条) 低于警戒线40%')
        elif wr > 90:
            alerts.append(f'✅ 体制{reg} WR={wr:.0f}% ({total}条) 表现优异')
    return alerts


# ─── 学习任务4：自适应门槛重算 ───────────────────────────────────
def recalculate_adaptive_threshold(settled: list) -> dict:
    """
    基于实盘结算数据动态调整门槛
    逻辑：当WR持续>80% → 可适当收紧门槛（提高质量）
          当WR持续<60% → 放宽门槛（增加样本）
    """
    try:
        state = json.load(open(DATA / 'adaptive_threshold_state.json'))
        current_thr = int(state.get('threshold', 140))
    except Exception:
        current_thr = 140

    if len(settled) < 100:  # [R5-fix audit-2026-06-17] 设计院大样本原则: n≥100才是铁证
        return {'action': 'no_change', 'reason': '样本不足100条(设计院原则: n<100不得调参)', 'current_thr': current_thr, 'new_thr': current_thr, 'recent_wr': '?', 'recent_n': '?'}

    # 最近20条
    recent = sorted(settled, key=lambda x: x.get('ts', ''), reverse=True)[:100]  # [R5-fix] 滑窗由20→100
    # [P1-3] 用pnl_pct判断胜负
    wins = sum(1 for r in recent if
               r.get('outcome') in ('TP1','TP2') or
               r.get('result') == 'WIN' or
               (r.get('pnl_pct') is not None and r['pnl_pct'] > 0))
    losses = sum(1 for r in recent if r.get('outcome') == 'SL')
    total = wins + losses
    if total < 5:
        return {'action': 'no_change', 'reason': '有效结算不足5条', 'current_thr': current_thr, 'new_thr': current_thr, 'recent_wr': '?', 'recent_n': total}

    wr = wins / total * 100

    new_thr = current_thr
    action = 'no_change'

    if wr >= 85 and total >= 50:  # [R5-fix] 触发条件由15→50
        # 持续高胜率 → 适度提高门槛（提升信号质量）
        new_thr = min(current_thr + 2, 155)
        action = 'increase'
    elif wr <= 55 and total >= 30:  # [R5-fix] 触发条件由10→30
        # 胜率下滑 → 降低门槛（增加样本积累速度）
        new_thr = max(current_thr - 2, 130)
        action = 'decrease'

    return {
        'action': action,
        'current_thr': current_thr,
        'new_thr': new_thr,
        'recent_wr': round(wr, 1),
        'recent_n': total,
    }


# ─── 主流程 ──────────────────────────────────────────────────────
def main():
    force = '--force' in sys.argv
    dry_run = '--dry-run' in sys.argv

    settled = load_settled()
    state   = load_learner_state()
    n_now   = len(settled)
    n_last  = state.get('last_n', 0)
    n_new   = n_now - n_last

    print(f'{BOLD}╔══════════════════════════════════════════╗{W}')
    print(f'{BOLD}║  梵天自进化引擎 · auto_learner v1.0      ║{W}')
    print(f'{BOLD}╚══════════════════════════════════════════╝{W}')
    print(f'  总结算: {n_now}条  上次: {n_last}条  新增: {n_new}条')
    print(f'  触发阈值: 每{TRIGGER_EVERY_N}条  {"[FORCE]" if force else ""}')

    should_run = force or (n_new >= TRIGGER_EVERY_N)

    if not should_run:
        remain = TRIGGER_EVERY_N - n_new
        print(f'\n  ⏳ 等待 {remain} 条新结算后触发学习')
        print('  HEARTBEAT_OK')
        return 0

    print(f'\n{G}🔄 触发学习更新！新增{n_new}条结算{W}')
    print()

    # ── 1. grade分布 ──
    print(f'{BOLD}【1】Grade分布分析{W}')
    grade_dist = analyze_grade_distribution(settled)
    for seg in ['S', 'A', 'B', 'C', 'X']:
        v = grade_dist[seg]
        if v['n'] == 0: continue
        col = G if v['wr'] >= 65 else (Y if v['wr'] >= 40 else R)
        print(f'  {seg}级: {v["n"]:3d}条  WR={col}{v["wr"]:.1f}%{W}  {v["wins"]}W/{v["losses"]}L')

    # ── 2. 最优score区间 ──
    print(f'\n{BOLD}【2】最优Score区间{W}')
    opt = find_optimal_score_range(settled)
    if opt['best_range']:
        print(f'  最优区间: score {opt["best_range"][0]}~{opt["best_range"][1]}  WR={G}{opt["best_wr"]:.1f}%{W}')

    # ── 3. 体制WR监控 ──
    print(f'\n{BOLD}【3】体制WR监控{W}')
    alerts = detect_regime_anomaly(settled)
    for a in alerts:
        print(f'  {a}')
    if not alerts:
        print('  无异常体制')

    # ── 4. 自适应门槛 ──
    print(f'\n{BOLD}【4】自适应门槛建议{W}')
    thr_result = recalculate_adaptive_threshold(settled)
    print(f'  当前门槛: {thr_result["current_thr"]}')
    print(f'  近期WR: {thr_result.get("recent_wr","?")}%  ({thr_result.get("recent_n","?")}条)')
    if thr_result['action'] == 'increase':
        print(f'  {G}建议提高门槛: {thr_result["current_thr"]} → {thr_result["new_thr"]}{W}')
    elif thr_result['action'] == 'decrease':
        print(f'  {Y}建议降低门槛: {thr_result["current_thr"]} → {thr_result["new_thr"]}{W}')
    else:
        print(f'  ✅ 门槛维持不变: {thr_result["current_thr"]}')

    if not dry_run:
        # 更新状态
        state['last_n']   = n_now
        state['last_run'] = NOW.isoformat()
        state['runs']     = state.get('runs', 0) + 1
        state['last_grade_dist'] = grade_dist
        state['last_thr_suggestion'] = thr_result
        save_learner_state(state)

        # 写入学习报告
        report = {
            'ts': NOW.isoformat(),
            'n_settled': n_now,
            'n_new': n_new,
            'grade_dist': grade_dist,
            'optimal_score': opt,
            'regime_alerts': alerts,
            'threshold_suggestion': thr_result,
        }
        (DATA / 'auto_learner_report.json').write_text(
            json.dumps(report, indent=2, ensure_ascii=False)
        )
        print(f'\n{G}✅ 学习报告写入: data/auto_learner_report.json{W}')

        # [P1-哲学修复 设计院 2026-06-24] 乘数自动回流
        print(f'\n  【5】乘数回流检测')
        try:
            # 函数定义在 main 之后，用 globals() 动态获取
            _fn = globals().get('update_symbol_regime_mult')
            if _fn is None:
                # 回退：从文件所在目录重新导入
                import importlib.util, pathlib
                _spec = importlib.util.spec_from_file_location(
                    'auto_learner_fn',
                    pathlib.Path(__file__).resolve()
                )
                _m = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_m)
                _fn = getattr(_m, 'update_symbol_regime_mult', None)
            if _fn:
                _fn(settled)
            else:
                print('  [乘数回流] 函数未找到，跳过')
        except Exception as _e:
            print(f'  [乘数回流] 错误: {_e}')

    print()
    return 0


# ── 显式内存释放 ──
try:
    import gc as _gc
    _check_and_gc()
except Exception:
    pass

if __name__ == '__main__':
    sys.exit(main())


# ── [P1-哲学修复 设计院 2026-06-24] 乘数自动回流 ─────────────────────────
def update_symbol_regime_mult(settled: list):
    """
    根据实盘结算数据，自动调整 brahma_core.py 中的中小币乘数矩阵。
    哲学：让数据说话，不封禁，让评分自然淘汰。

    规则：
      - 实盘WR < 参考WR - 20pp → 乘数 × 0.85（最小0.25）
      - 实盘WR > 参考WR + 10pp → 乘数 × 1.05（最大1.2）
      - n < 30 → 不调整（样本不足）
    """
    from collections import defaultdict
    import re

    ADJUST_DOWN_THRESHOLD = -20   # pp
    ADJUST_UP_THRESHOLD   = +10   # pp
    MIN_N = 30

    BTC_ETH_REF = {
        'BEAR_TREND_SHORT': 71.8, 'BEAR_TREND_LONG': 45.0,
        'BEAR_EARLY_SHORT': 66.5, 'BEAR_EARLY_LONG': 50.4,
        'BULL_EARLY_LONG':  64.4, 'BULL_EARLY_SHORT': 51.9,
        'BULL_TREND_LONG':  70.3, 'BULL_TREND_SHORT': 47.7,
        'BEAR_RECOVERY_LONG': 72.5, 'BEAR_RECOVERY_SHORT': 47.9,
    }

    # 统计实盘 WR (symbol × regime × direction)
    groups = defaultdict(lambda: {'wins': 0, 'n': 0})
    for r in settled:
        sym = r.get('symbol', ''); regime = r.get('regime', ''); dr = r.get('direction', '')
        if not all([sym, regime, dr]): continue
        pnl = r.get('pnl_pct')
        if pnl is None: continue
        key = f'{sym}__{regime}_{dr}'
        groups[key]['n'] += 1
        if pnl > 0: groups[key]['wins'] += 1

    adjustments = []
    for key, v in groups.items():
        if v['n'] < MIN_N: continue
        parts = key.split('__')
        if len(parts) != 2: continue
        sym, combo = parts
        if sym in ('BTCUSDT', 'ETHUSDT'): continue  # BTC/ETH单独维护

        wr_live = v['wins'] / v['n'] * 100
        ref_wr  = BTC_ETH_REF.get(combo)
        if ref_wr is None: continue

        gap = wr_live - ref_wr
        if abs(gap) < min(abs(ADJUST_DOWN_THRESHOLD), abs(ADJUST_UP_THRESHOLD)):
            continue  # 差距不显著，不调整

        regime = combo.rsplit('_', 1)[0]
        dr     = combo.rsplit('_', 1)[1]
        direction = 'LONG' if dr == 'LONG' else 'SHORT'
        adjustments.append({
            'sym': sym, 'regime': regime, 'direction': direction,
            'combo': combo, 'wr_live': round(wr_live, 1),
            'ref_wr': ref_wr, 'gap': round(gap, 1), 'n': v['n'],
            'action': 'DOWN' if gap < 0 else 'UP',
        })

    if adjustments:
        print(f'\n  [{Y}乘数回流{W}] 发现 {len(adjustments)} 项需调整:')
        for a in adjustments:
            icon = '⬇️' if a['action']=='DOWN' else '⬆️'
            print(f'    {icon} {a["sym"]:12s} {a["combo"]:<28} 实盘WR={a["wr_live"]}%  ref={a["ref_wr"]}%  gap={a["gap"]:+.1f}pp  n={a["n"]}')
            print(f'         → 建议调整乘数（见 brahma_core.py _REGIME_MULT_ALTCOIN）')

        # 写入调整建议文件（不直接修改brahma_core，避免自动修改核心文件风险）
        suggest_path = DATA / 'mult_adjust_suggestions.json'
        existing = []
        if suggest_path.exists():
            try: existing = json.load(open(suggest_path))
            except: pass
        from datetime import datetime, timezone
        entry = {'ts': datetime.now(timezone.utc).isoformat(), 'suggestions': adjustments}
        existing.append(entry)
        json.dump(existing[-10:], open(suggest_path, 'w'), indent=2, ensure_ascii=False)
        print(f'    ✅ 调整建议已写入 data/mult_adjust_suggestions.json')
    else:
        print(f'  [乘数回流] 当前无需调整（n≥30的组合偏差均在阈值内）')

    return adjustments
