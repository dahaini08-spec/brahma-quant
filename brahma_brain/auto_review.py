#!/usr/bin/env python3
"""
梵天信号复盘引擎 v1.0 (I7反省层)
功能：
  1. 从 trade_records.jsonl 读取真实实盘平仓记录
  2. 生成结构化复盘报告写入 data/review_queue.jsonl
  3. 按维度统计：体制/评分/出场原因/时间窗口
  4. 供 dharma_ci.py 每日拉取分析

调用时机：每笔平仓后由 ws_guardian 触发，或 CI 每日批量分析
"""
import json, os, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BASE = Path(__file__).parent.parent
TRADE_RECORDS = BASE / "data" / "trade_records.jsonl"
REVIEW_QUEUE  = BASE / "data" / "review_queue.jsonl"
REVIEW_STATS  = BASE / "data" / "review_stats.json"

REAL_SOURCES = {'ws_guardian_sl', 'ws_guardian_tp', 'hunter_v2', 'arjuna', 'lana'}


def load_real_trades(days: int = 30) -> list:
    """加载真实实盘记录（过去N天）"""
    cutoff = time.time() - days * 86400
    records = []
    try:
        with open(TRADE_RECORDS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    src = r.get('source', '')
                    ts_str = r.get('close_ts', '') or r.get('open_ts', '')
                    if src in REAL_SOURCES:
                        records.append(r)
                except:
                    pass
    except FileNotFoundError:
        pass
    return records


def generate_review(record: dict) -> dict:
    """为单笔交易生成复盘报告"""
    sym    = record.get('symbol', '?')
    score  = record.get('score', 0)
    regime = record.get('regime', 'UNKNOWN')
    result = record.get('result', '?')
    pnl    = record.get('pnl_pct', 0)
    reason = record.get('close_reason', '?')
    entry  = record.get('entry_price', 0)
    close  = record.get('close_price', 0)
    direction = record.get('direction', '?')
    tf_aligned = record.get('tf_aligned', 0)
    open_ts  = record.get('open_ts', '')
    close_ts = record.get('close_ts', '')

    # 持仓时间计算
    hold_hours = 0
    try:
        t0 = datetime.fromisoformat(open_ts.replace('Z', '+00:00')) if open_ts else None
        t1 = datetime.fromisoformat(close_ts.replace('Z', '+00:00')) if close_ts else None
        if t0 and t1:
            hold_hours = round((t1 - t0).total_seconds() / 3600, 1)
    except:
        pass

    # 复盘诊断
    diagnostics = []

    # 评分 vs 结果
    if score >= 155 and result in ('LOSS',):
        diagnostics.append({'tag': 'HIGH_SCORE_LOSS', 'msg': f'score={score}≥155但亏损，检查体制/时机'})
    if score < 120 and result in ('WIN', 'WIN_T1', 'WIN_T2'):
        diagnostics.append({'tag': 'LOW_SCORE_WIN', 'msg': f'score={score}<120但盈利，偶发性强'})

    # 体制 vs 方向
    if 'BEAR' in regime and direction in ('LONG', '做多'):
        diagnostics.append({'tag': 'REGIME_MISMATCH', 'msg': f'BEAR体制做多，逆势风险'})
    if 'CHOP' in regime:
        diagnostics.append({'tag': 'CHOP_ENTRY', 'msg': 'CHOP体制入场，信噪比低'})

    # 出场原因分析
    if reason == 'WS_STOP_LOSS' and pnl > -0.3:
        diagnostics.append({'tag': 'TIGHT_SL', 'msg': f'止损触发但亏损仅{pnl:.2f}%，SL可能过紧'})
    if reason in ('TP1触达_50%减仓', 'TP2触达_全平') and pnl < 1.0:
        diagnostics.append({'tag': 'SMALL_TP', 'msg': f'TP触达但收益仅{pnl:.2f}%，检查R:R'})

    # 持仓时间异常
    if hold_hours > 20 and reason == 'WS_STOP_LOSS':
        diagnostics.append({'tag': 'LONG_HOLD_LOSS', 'msg': f'持仓{hold_hours}h后止损，考虑时间止损'})

    # 评级
    if result in ('WIN_T2',):
        grade = 'A+'
    elif result in ('WIN_T1', 'WIN'):
        grade = 'A' if pnl >= 1.0 else 'B'
    elif result == 'BREAK_EVEN':
        grade = 'C'
    else:
        grade = 'D' if pnl > -1.0 else 'F'

    return {
        'signal_id': record.get('signal_id', ''),
        'symbol': sym,
        'direction': direction,
        'regime': regime,
        'score': score,
        'tf_aligned': tf_aligned,
        'entry_price': entry,
        'close_price': close,
        'pnl_pct': pnl,
        'pnl_usdt': record.get('pnl_usdt', 0),
        'result': result,
        'close_reason': reason,
        'hold_hours': hold_hours,
        'grade': grade,
        'diagnostics': diagnostics,
        'source': record.get('source', ''),
        'reviewed_at': datetime.now(timezone.utc).isoformat(),
    }


def run_batch_review(days: int = 30) -> dict:
    """批量复盘最近N天所有真实实盘"""
    trades = load_real_trades(days)
    if not trades:
        return {'total': 0, 'reviewed': 0, 'msg': '无真实实盘记录'}

    reviews = []
    for t in trades:
        if t.get('entry_price', 0) > 0 and t.get('close_price', 0) > 0:
            reviews.append(generate_review(t))

    # 写入review_queue.jsonl（增量追加，去重）
    existing_ids = set()
    try:
        with open(REVIEW_QUEUE) as f:
            for line in f:
                try: existing_ids.add(json.loads(line).get('signal_id', ''))
                except: pass
    except FileNotFoundError:
        pass

    new_count = 0
    with open(REVIEW_QUEUE, 'a') as f:
        for rv in reviews:
            sid = rv.get('signal_id', '')
            if sid and sid not in existing_ids:
                f.write(json.dumps(rv, ensure_ascii=False) + '\n')
                new_count += 1

    # 统计分析
    stats = compute_stats(reviews)
    with open(REVIEW_STATS, 'w') as f:
        json.dump({**stats, 'updated_at': datetime.now(timezone.utc).isoformat()}, f,
                  ensure_ascii=False, indent=2)

    return {'total': len(trades), 'reviewed': len(reviews), 'new': new_count, 'stats': stats}


def compute_stats(reviews: list) -> dict:
    """按多维度统计复盘结果"""
    if not reviews: return {}

    # 基础
    wins = [r for r in reviews if r['result'] in ('WIN','WIN_T1','WIN_T2')]
    losses = [r for r in reviews if r['result'] == 'LOSS']
    pnls = [r['pnl_pct'] for r in reviews if r['pnl_pct'] != 0]

    # 按体制
    by_regime = defaultdict(lambda: {'n':0,'wins':0,'pnl_sum':0.0})
    for r in reviews:
        rg = r.get('regime','?')
        by_regime[rg]['n'] += 1
        if r['result'] in ('WIN','WIN_T1','WIN_T2'): by_regime[rg]['wins'] += 1
        by_regime[rg]['pnl_sum'] += r['pnl_pct']

    # 按评分段
    score_bands = {'<120':[],'120-139':[],'140-149':[],'150-154':[],'155+': []}
    for r in reviews:
        s = r.get('score', 0)
        band = '155+' if s>=155 else ('150-154' if s>=150 else ('140-149' if s>=140 else ('120-139' if s>=120 else '<120')))
        score_bands[band].append(r['pnl_pct'])

    # 诊断标签分布
    diag_tags = defaultdict(int)
    for r in reviews:
        for d in r.get('diagnostics', []):
            diag_tags[d['tag']] += 1

    return {
        'n_total': len(reviews),
        'n_wins': len(wins),
        'n_losses': len(losses),
        'win_rate': round(len(wins)/len(reviews), 3) if reviews else 0,
        'avg_pnl': round(sum(pnls)/len(pnls), 3) if pnls else 0,
        'total_pnl_pct': round(sum(pnls), 2) if pnls else 0,
        'by_regime': {k: {'n':v['n'],'wr':round(v['wins']/v['n'],2),'avg_pnl':round(v['pnl_sum']/v['n'],2)} for k,v in by_regime.items()},
        'by_score_band': {k: {'n':len(v),'avg_pnl':round(sum(v)/len(v),2)} if v else {'n':0,'avg_pnl':0} for k,v in score_bands.items()},
        'top_diagnostics': dict(sorted(diag_tags.items(), key=lambda x:-x[1])[:5]),
    }


def review_single(record: dict) -> dict:
    """单笔平仓即时复盘（由ws_guardian触发）"""
    rv = generate_review(record)
    # 追加到review_queue
    try:
        with open(REVIEW_QUEUE, 'a') as f:
            f.write(json.dumps(rv, ensure_ascii=False) + '\n')
    except Exception as e:
        pass
    return rv


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=30)
    p.add_argument('--stats', action='store_true')
    args = p.parse_args()

    result = run_batch_review(args.days)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ══ [设计院 2026-06-30 措施2] 孤儿模块自动巡检 ══

import os, re, sys
from pathlib import Path

def check_orphan_modules():
    BASE  = Path(__file__).parent.parent
    BRAIN = Path(__file__).parent
    
    # 读brahma_core
    core_file = BRAIN / 'brahma_core.py'
    orch_file = BRAIN / 'brahma_orchestrator.py'
    if not core_file.exists():
        return
    
    core_text = core_file.read_text()
    orch_text = orch_file.read_text() if orch_file.exists() else ''
    combined  = core_text + orch_text
    
    # 所有功能模块（排除备份/工具）
    skip = {
            # 系统核心（不需被自引用）
            'brahma_core', 'brahma_orchestrator', '__init__', 'auto_review',
            # 梵天360独立入口（通过cron调用，不被brahma_core引用）
            'brahma_360',
            'gex_scanner',   # GEX扫描器（独立cron，不被brahma_core静态引用）
            'oi_surge_scanner',  # OI猎手（独立cron + brahma_core动态import）
            # B类：离线工具，不接入实盘流程
            'offline_adapters', 'formatter', 'tardis_liq_layer',
            # C类：独立入口/被brahma_bus封装/间接使用
            'realtime_fetch', 'brahma_parallel_engine', 'external_signal',
            's7_liq_config',
            # D类：已归档模块（s24归档，设计院2026-06-26封印）
            'trading_agents_bridge',  # s24已归档，由s25替代，保留文件供回滚
            # E类：独立入口模块（AI直接调用 / 被非brahma_core模块调用）
            'brahma_analysis_runner', # 设计院唯一分析入口，AI/CLI直接调用，不被brahma_core静态引用
            # ── [设计院 v18 AutoReview修复 2026-07-02] ──────────────────
            # 以下模块已接入 brahma_analysis_runner（orchestrator层），非孤儿
            'timing_filter',          # 接入runner.run_analysis timing层
            'analysis_snapshot',      # 接入runner.run_analysis 快照缓存
            'brainlog',               # 接入runner.run_batch 日志层
            'portfolio_optimizer',    # 接入runner.run_batch + auto_executor 相关性过滤
            'brahma_health',          # 接入runner.run_batch 健康GC
            'market_structure_scanner', # 接入runner.run_analysis score≥130 SMC补充扫描
            'llm_council_bridge',     # 接入runner.run_analysis score≥140 LLM二次审查
            # ────────────────────────────────────────────────────────────
            'ic_tracker',             # IC信息系数追踪，被live_signal_settler调用，独立数据生命周期
            # F类：结算闭环链路（live_signal_settler → ev_feedback → dharma_online_learner）
            'ev_feedback',            # 结算回调，被live_signal_settler动态import，不被brahma_core静态引用
            'dharma_online_learner',  # 在线学习，被ev_feedback每10笔触发，设计院方案B/C级落地
        }
    
    orphans = []
    for f in BRAIN.glob('*.py'):
        if f.suffix != '.py' or '.bak' in f.name:
            continue
        mod = f.stem
        if mod in skip or mod.startswith('_'):
            continue
        if mod not in combined:
            orphans.append(mod)
    
    if orphans:
        print(f"[AutoReview] 🔴 发现孤儿模块 {len(orphans)}个: {orphans}")
        print(f"[AutoReview] 提示: 以上模块存在于brahma_brain/但未被brahma_core/orchestrator引用")
        return orphans
    else:
        print(f"[AutoReview] ✅ 架构债务巡检通过，无孤儿模块")
        return []


def check_standby_violations() -> list:
    """
    扫描 STATUS: STANDBY 模块是否被非白名单文件引用
    设计院 2026-07-01
    """
    import ast
    from pathlib import Path

    brain = Path(__file__).parent
    standby_mods = set()
    for f in brain.glob('*.py'):
        if 'STATUS: STANDBY' in f.read_text():
            standby_mods.add(f.stem)

    if not standby_mods:
        return []

    # 扫描哪些活跃模块引用了STANDBY模块
    violations = []
    skip_self = standby_mods | {'__init__', 'auto_review'}
    for f in brain.glob('*.py'):
        if f.stem in skip_self:
            continue
        try:
            src = f.read_text()
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in getattr(node, 'names', [])]
                    module = getattr(node, 'module', '') or ''
                    for name in names + [module.split('.')[-1]]:
                        if name in standby_mods:
                            violations.append(f'{f.stem} → {name}(STANDBY)')
        except Exception:
            pass

    if violations:
        print(f'[AutoReview] ⚠️ STANDBY模块被引用 {len(violations)}处: {violations[:5]}')
    return violations


if __name__ == '__main__':
    result = check_orphan_modules()
    standby_v = check_standby_violations()
    # 自推送：发现孤儿或STANDBY违规才推送，正常静默
    issues = []
    if result:
        issues.append(f'🔴 发现孤儿模块 {len(result)}个\n' + '\n'.join(f'  - {m}' for m in result))
    if standby_v:
        issues.append(f'⚠️ STANDBY模块被引用 {len(standby_v)}处\n' + '\n'.join(f'  - {v}' for v in standby_v[:5]))
    if issues:
        import subprocess as _sp
        msg = '🏛️ 架构债务巡检 (' + ', '.join(['孤儿' if result else '', 'STANDBY违规' if standby_v else '']).strip(', ') + ')\n\n' + '\n\n'.join(issues)
        _sp.run(
            ['openclaw', 'message', 'send',
             '--channel', 'jarvis',
             '--target', '73295708:thread:019f181f-e4d1-7576-85ca-77f4a7fa8075',
             '--message', msg],
            capture_output=True, timeout=15
        )
    # 正常(无问题) → 完全静默
