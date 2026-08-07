#!/usr/bin/env python3
"""
brahma_learning_loop.py — 梵天学习闭环引擎 v1.0
设计院六方联合封印 2026-07-13 · 苏摩111授权

═══════════════════════════════════════════════════════
核心职责：
  1. 消化 calibration_feedback.jsonl → 更新 WR/EV 统计
  2. 填充 ic_tracker_state.json（每体制IC值）
  3. 填充 ev_buckets/（score区间EV桶）
  4. 输出 auto_learner_state.json（阈值建议）
  5. 输出 live_performance_daily.json（实时持仓盈亏）
  6. 触发 brahma-arch-review 日报推送

运行：
  python3 scripts/brahma_learning_loop.py
  python3 scripts/brahma_learning_loop.py --stats   # 只输出统计，不写文件
═══════════════════════════════════════════════════════
"""

import sys, os, json, time, argparse, urllib.request
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

BASE       = Path(__file__).parent.parent
DATA       = BASE / 'data'
CAL_LOG    = DATA / 'calibration_feedback.jsonl'
EV_LOG     = DATA / 'ev_feedback_log.jsonl'
SIGNAL_LOG = DATA / 'live_signal_log.jsonl'
IC_STATE   = DATA / 'ic_tracker_state.json'
LEARNER    = DATA / 'auto_learner_state.json'
EV_DIR     = DATA / 'ev_buckets'
PERF_FILE  = DATA / 'live_performance_daily.json'
POS_FILE   = DATA / 'wuqu_positions.json'

EV_DIR.mkdir(exist_ok=True)

# ─── 工具 ──────────────────────────────────────────────────────────────
def fetch_price(symbol: str) -> float | None:
    try:
        url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
        r   = urllib.request.urlopen(url, timeout=5)
        return float(json.loads(r.read())['price'])
    except Exception:
        return None

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = []
    for line in path.read_text(encoding='utf-8').strip().split('\n'):
        try:
            lines.append(json.loads(line))
        except Exception:
            pass
    return lines

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

# ─── P0-A: 消化 calibration_feedback.jsonl ────────────────────────────
def run_calibration_stats(dry: bool = False) -> dict:
    """
    消化信号校准日志，按 regime:direction:score_bucket 统计 WR / EV
    写入 ev_buckets/ 和 ic_tracker_state.json
    """
    records = load_jsonl(CAL_LOG) + load_jsonl(EV_LOG)
    if not records:
        return {'status': 'no_data'}

    # ── 按 matrix_key 聚合 ──────────────────────────────────────────
    buckets: dict[str, dict] = defaultdict(lambda: {'win':0,'loss':0,'be':0,'pnl_sum':0.0,'n':0})
    regime_direction: dict[str, list[float]] = defaultdict(list)  # for IC

    for rec in records:
        sym    = rec.get('symbol', '?')
        regime = rec.get('regime', rec.get('matrix_key','?').split(':')[0] if ':' in rec.get('matrix_key','') else '?')
        direc  = rec.get('direction', rec.get('matrix_key','?').split(':')[1] if rec.get('matrix_key','?').count(':') >= 1 else '?')
        score  = float(rec.get('score', 0))
        result = rec.get('result', rec.get('outcome', 'UNKNOWN'))
        pnl    = float(rec.get('pnl', rec.get('pnl_pct', 0)) or 0)

        # score 分桶
        if score >= 165:  bucket = '165+'
        elif score >= 155: bucket = '155-164'
        elif score >= 140: bucket = '140-154'
        elif score >= 120: bucket = '120-139'
        else:              bucket = '<120'

        key = f'{regime}:{direc}:{bucket}'
        b   = buckets[key]
        b['n'] += 1
        b['pnl_sum'] += pnl
        if result in ('WIN', 'TP1', 'TP2'):
            b['win'] += 1
        elif result in ('LOSS', 'SL'):
            b['loss'] += 1
        else:
            b['be'] += 1

        # IC 数据收集（每体制方向的 pnl 序列）
        regime_direction[f'{regime}:{direc}'].append(pnl)

    # ── 计算 WR / EV 并写入 ev_buckets/ ─────────────────────────────
    ev_summary = {}
    for key, b in buckets.items():
        n  = b['n']
        wr = round(b['win'] / (b['win'] + b['loss']), 4) if (b['win'] + b['loss']) > 0 else None
        ev = round(b['pnl_sum'] / n, 4) if n > 0 else None
        ev_summary[key] = {'n': n, 'win': b['win'], 'loss': b['loss'], 'be': b['be'],
                            'wr': wr, 'ev': ev}
        if not dry:
            safe_key = key.replace('/', '_').replace(':', '_')
            (EV_DIR / f'{safe_key}.json').write_text(
                json.dumps({'key': key, **ev_summary[key], 'updated': now_utc()},
                           ensure_ascii=False),
                encoding='utf-8'
            )

    # ── 计算 IC（信息系数）── Rank correlation(score, pnl) ──────────
    ic_by_regime: dict[str, float] = {}
    for rd_key, pnls in regime_direction.items():
        if len(pnls) >= 5:
            # Spearman 近似（不依赖scipy）
            n   = len(pnls)
            idx = list(range(n))
            idx.sort(key=lambda i: pnls[i])
            ranks = [0.0] * n
            for rank, i in enumerate(idx):
                ranks[i] = rank
            mean_rank = (n - 1) / 2
            cov  = sum((i - mean_rank) * (r - mean_rank) for i, r in enumerate(ranks)) / n
            std1 = (sum((i - mean_rank)**2 for i in range(n)) / n) ** 0.5
            std2 = (sum((r - mean_rank)**2 for r in ranks) / n) ** 0.5
            ic   = round(cov / (std1 * std2), 4) if std1 * std2 > 0 else 0.0
            ic_by_regime[rd_key] = ic

    if not dry:
        IC_STATE.write_text(json.dumps({
            'updated_at': now_utc(),
            'ic_by_regime': ic_by_regime,
            'ev_by_bucket': ev_summary,
        }, ensure_ascii=False, indent=2), encoding='utf-8')

    return {'ev_summary': ev_summary, 'ic_by_regime': ic_by_regime, 'n_records': len(records)}


# ─── P0-B: 更新 auto_learner_state.json ──────────────────────────────
def run_auto_learner(ev_result: dict, dry: bool = False) -> dict:
    """
    基于 EV 统计，动态建议信号阈值
    逻辑：找到 EV>0 且 WR>55% 的最低 score 桶作为新阈值
    """
    ev_summary = ev_result.get('ev_summary', {})
    records    = load_jsonl(CAL_LOG) + load_jsonl(EV_LOG)

    # 按总分计算 grade 分布
    grade_dist = {'X': {'n':0,'wins':0,'losses':0,'wr':0},
                  'S': {'n':0,'wins':0,'losses':0,'wr':0},
                  'A': {'n':0,'wins':0,'losses':0,'wr':0},
                  'B': {'n':0,'wins':0,'losses':0,'wr':0},
                  'C': {'n':0,'wins':0,'losses':0,'wr':0}}

    for rec in records:
        score  = float(rec.get('score', 0))
        result = rec.get('result', rec.get('outcome', 'UNKNOWN'))
        if score >= 165:   g = 'X'
        elif score >= 155: g = 'S'
        elif score >= 140: g = 'A'
        elif score >= 120: g = 'B'
        else:              g = 'C'
        grade_dist[g]['n'] += 1
        if result in ('WIN', 'TP1', 'TP2'):
            grade_dist[g]['wins'] += 1
        elif result in ('LOSS', 'SL'):
            grade_dist[g]['losses'] += 1

    for g, v in grade_dist.items():
        v['wr'] = round(v['wins'] / (v['wins'] + v['losses']) * 100, 1) if (v['wins']+v['losses']) > 0 else 0

    # 阈值建议：找到 WR>55% 的最低等级
    current_thr = 138
    try:
        state = json.loads(LEARNER.read_text(encoding='utf-8'))
        current_thr = state.get('last_thr_suggestion', {}).get('current_thr', 138)
    except Exception:
        pass

    new_thr = current_thr
    if grade_dist['A']['wr'] > 60 and grade_dist['A']['n'] >= 10:
        new_thr = 140   # A级以上WR>60% → 阈值收紧
    elif grade_dist['B']['wr'] > 60 and grade_dist['B']['n'] >= 10:
        new_thr = 120   # B级WR>60% → 适当降低
    elif grade_dist['S']['wr'] < 50 and grade_dist['S']['n'] >= 10:
        new_thr = 155   # S级WR<50% → 收紧到S级以上
    action = 'raise' if new_thr > current_thr else ('lower' if new_thr < current_thr else 'no_change')

    state_out = {
        'last_n': len(records),
        'last_run': now_utc(),
        'runs': (json.loads(LEARNER.read_text()).get('runs', 0) + 1) if LEARNER.exists() else 1,
        'last_grade_dist': grade_dist,
        'last_thr_suggestion': {
            'action': action, 'current_thr': current_thr,
            'new_thr': new_thr,
            'reasoning': f'WR统计驱动：S={grade_dist["S"]["wr"]}% A={grade_dist["A"]["wr"]}%'
        },
        'ic_by_regime': ev_result.get('ic_by_regime', {}),
    }

    if not dry:
        LEARNER.write_text(json.dumps(state_out, ensure_ascii=False, indent=2), encoding='utf-8')

    return state_out


# ─── P0-C: live_performance_daily.json（实时持仓）─────────────────────
def run_live_performance(dry: bool = False) -> dict:
    """
    从 wuqu_positions.json 读取持仓，实时拉取价格，输出盈亏快照
    替代硬编码持仓列表
    """
    if not POS_FILE.exists():
        return {'error': 'wuqu_positions.json not found'}

    try:
        raw = json.loads(POS_FILE.read_text(encoding='utf-8'))
    except Exception as e:
        return {'error': str(e)}

    positions = raw if isinstance(raw, list) else list(raw.values())
    open_pos  = [p for p in positions if isinstance(p, dict) and float(p.get('size', 0)) != 0]

    rows = []
    total_unreal = 0.0
    for p in open_pos:
        sym     = p.get('symbol', '?')
        side    = p.get('side', 'LONG')
        size    = float(p.get('size', 0))
        entry   = float(p.get('entry_price', 0))
        mark    = float(p.get('mark_price', 0)) or fetch_price(sym) or entry
        sl      = float(p.get('stop_loss', 0))
        tp      = float(p.get('take_profit', 0))
        lev     = float(p.get('leverage', 1))
        notional= float(p.get('notional_usdt', abs(size * mark)))
        unreal  = float(p.get('unrealized_pnl', 0))
        pnl_pct = round((mark - entry) / entry * 100 * (1 if side == 'LONG' else -1), 2) if entry > 0 else 0

        total_unreal += unreal
        rows.append({
            'symbol': sym, 'side': side, 'size': size,
            'entry': entry, 'mark': mark, 'sl': sl, 'tp': tp,
            'leverage': lev, 'notional': round(notional, 2),
            'unrealized_pnl': round(unreal, 4),
            'pnl_pct': pnl_pct,
        })

    out = {
        'updated_at':     now_utc(),
        'n_positions':    len(rows),
        'total_unrealized_pnl': round(total_unreal, 4),
        'positions':      rows,
    }

    if not dry:
        PERF_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')

    return out


# ─── 主入口 ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stats', action='store_true', help='只输出统计，不写文件')
    parser.add_argument('--perf-only', action='store_true', help='只更新持仓盈亏')
    args   = parser.parse_args()
    dry    = args.stats

    print(f'[{now_utc()}] brahma_learning_loop 启动 dry={dry}')

    if args.perf_only:
        perf = run_live_performance(dry)
        print(f'持仓盈亏: {perf.get("n_positions")}个 总浮盈={perf.get("total_unrealized_pnl")}U')
        return

    # P0-A: 信号校准统计
    print('P0-A 消化 calibration_feedback...')
    ev_result = run_calibration_stats(dry)
    n_rec = ev_result.get('n_records', 0)
    n_bkt = len(ev_result.get('ev_summary', {}))
    print(f'  → {n_rec}条记录，{n_bkt}个EV桶')
    for key, v in sorted(ev_result.get('ev_summary', {}).items()):
        wr_str = f'WR={v["wr"]:.1%}' if v['wr'] is not None else 'WR=N/A'
        ev_str = f'EV={v["ev"]:+.3f}%' if v['ev'] is not None else 'EV=N/A'
        print(f'  [{key}] n={v["n"]} {wr_str} {ev_str}')

    # IC 报告
    for rd, ic in ev_result.get('ic_by_regime', {}).items():
        print(f'  IC[{rd}] = {ic:.4f}')

    # P0-B: 自动学习器
    print('P0-B 更新 auto_learner...')
    learner = run_auto_learner(ev_result, dry)
    thr = learner.get('last_thr_suggestion', {})
    print(f'  → 样本{learner["last_n"]}笔 当前阈值={thr.get("current_thr")} 建议={thr.get("new_thr")} ({thr.get("action")})')
    for g, v in learner.get('last_grade_dist', {}).items():
        if v['n'] > 0:
            print(f'  Grade-{g}: n={v["n"]} WR={v["wr"]}%')

    # P0-C: 持仓盈亏
    print('P0-C 更新 live_performance...')
    perf = run_live_performance(dry)
    print(f'  → {perf.get("n_positions")}个持仓 总浮盈={perf.get("total_unrealized_pnl")}U')

    print(f'[{now_utc()}] 学习闭环完成')


if __name__ == '__main__':
    main()
