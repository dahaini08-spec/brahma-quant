#!/usr/bin/env python3
"""
brahma_experience_engine.py — 梵天经验引擎 v1.0
设计院 2026-06-16 · 对标 MAKIMA 经验库RAG系统

核心功能：
  1. 决策快照记录（每次信号广播时自动保存）
  2. 24H自动复盘（比较预测vs实际，标记对错）
  3. 经验归档（带标签的结构化经验条目）
  4. 场景检索（当前分析时注入最相关历史经验）

与MAKIMA的差异：
  MAKIMA: AI自然语言复盘（灵活但消耗Token）
  梵天:   统计+规则驱动复盘（快速、零Token消耗、可量化）
"""
import json, time, os, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

BASE            = Path(__file__).parent.parent
EXP_DIR         = BASE / 'data' / 'experience'
SNAPSHOT_DIR    = BASE / 'data' / 'analysis_snapshots'
EXP_LOG         = EXP_DIR / 'experience_library.jsonl'
REVIEW_QUEUE    = EXP_DIR / 'review_queue.jsonl'
EXP_INDEX       = EXP_DIR / 'experience_index.json'

EXP_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════════
# 决策快照（每次信号广播时调用）
# ══════════════════════════════════════════════════════════════════

def save_decision_snapshot(signal: dict, market_ctx: dict = None) -> str:
    """
    保存决策快照，加入复盘队列
    signal: 梵天信号字典（来自live_signal_log）
    market_ctx: 市场上下文（F&G、GEX、RSI等）
    返回: snapshot_id
    """
    ts = time.time()
    snap_id = hashlib.md5(f"{signal.get('symbol')}_{ts}".encode()).hexdigest()[:12]

    snapshot = {
        'snap_id':      snap_id,
        'ts':           ts,
        'dt':           datetime.utcnow().isoformat(),
        'review_at':    ts + 86400,  # 24小时后复盘

        # 信号核心
        'symbol':       signal.get('symbol'),
        'direction':    signal.get('direction', signal.get('signal_dir')),
        'score':        signal.get('score'),
        'grade':        signal.get('grade'),
        'regime':       signal.get('regime'),
        'entry_lo':     signal.get('entry_lo'),
        'entry_hi':     signal.get('entry_hi'),
        'stop_loss':    signal.get('stop_loss'),
        'tp1':          signal.get('tp1'),
        'rr1':          signal.get('rr1'),

        # 市场上下文
        'fg_index':     (market_ctx or {}).get('fg_index'),
        'rsi_1h':       (market_ctx or {}).get('rsi_1h'),
        'gex':          (market_ctx or {}).get('gex'),
        'oi_change':    (market_ctx or {}).get('oi_change'),
        'fr':           (market_ctx or {}).get('fr'),
        'lsr':          (market_ctx or {}).get('lsr'),

        # 复盘状态
        'status':       'pending_review',
        'actual_pnl':   None,
        'verdict':      None,  # 'correct' | 'wrong' | 'timeout'
        'review_done':  False,
    }

    # 写入快照文件
    snap_file = SNAPSHOT_DIR / f'{snap_id}.json'
    snap_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))

    # 加入复盘队列
    with open(REVIEW_QUEUE, 'a') as f:
        f.write(json.dumps({'snap_id': snap_id, 'review_at': snapshot['review_at']}) + '\n')

    pass  # [静默]
    return snap_id


# ══════════════════════════════════════════════════════════════════
# 24H自动复盘（定时任务调用）
# ══════════════════════════════════════════════════════════════════

def run_24h_review():
    """
    检查复盘队列，对已到期的快照执行自动复盘
    无需AI，纯统计逻辑
    """
    if not REVIEW_QUEUE.exists():
        return

    now = time.time()
    queue = []
    try:
        queue = [json.loads(l) for l in REVIEW_QUEUE.read_text().strip().split('\n') if l]
    except:
        return

    pending = [q for q in queue if q.get('review_at', 0) <= now]
    if not pending:
        return

    pass  # [静默]

    import urllib.request
    for item in pending:
        snap_id = item['snap_id']
        snap_file = SNAPSHOT_DIR / f'{snap_id}.json'
        if not snap_file.exists():
            continue

        snap = json.loads(snap_file.read_text())
        if snap.get('review_done'):
            continue

        # 获取当前价格
        symbol = snap.get('symbol')
        try:
            url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
            with urllib.request.urlopen(url, timeout=5) as r:
                current_price = float(json.loads(r.read())['price'])
        except:
            continue

        entry_mid = (snap['entry_lo'] + snap['entry_hi']) / 2 if snap['entry_lo'] and snap['entry_hi'] else None
        if not entry_mid:
            continue

        direction = snap.get('direction', '')
        tp1       = snap.get('tp1')
        sl        = snap.get('stop_loss')

        # 判断结果
        if 'LONG' in direction.upper() or '多' in direction:
            if tp1 and current_price >= tp1:
                verdict = 'correct'
                actual_pnl = (current_price - entry_mid) / entry_mid * 100
            elif sl and current_price <= sl:
                verdict = 'wrong'
                actual_pnl = (current_price - entry_mid) / entry_mid * 100
            else:
                verdict = 'in_progress'
                actual_pnl = (current_price - entry_mid) / entry_mid * 100
        else:
            if tp1 and current_price <= tp1:
                verdict = 'correct'
                actual_pnl = (entry_mid - current_price) / entry_mid * 100
            elif sl and current_price >= sl:
                verdict = 'wrong'
                actual_pnl = (entry_mid - current_price) / entry_mid * 100
            else:
                verdict = 'in_progress'
                actual_pnl = (entry_mid - current_price) / entry_mid * 100

        snap['actual_price_24h'] = current_price
        snap['actual_pnl']       = round(actual_pnl, 4)
        snap['verdict']          = verdict
        snap['review_done']      = True
        snap['reviewed_at']      = datetime.utcnow().isoformat()

        snap_file.write_text(json.dumps(snap, ensure_ascii=False, indent=2))

        # 归档到经验库
        if verdict in ('correct', 'wrong'):
            _archive_experience(snap)

        icon = '✅' if verdict == 'correct' else '❌' if verdict == 'wrong' else '⏳'
        print(f'  [{icon}] {symbol} {direction} → 实际PnL={actual_pnl:+.2f}% 判决={verdict}')

    # 清理已复盘的队列
    remaining = [q for q in queue if q['snap_id'] not in {p['snap_id'] for p in pending}]
    REVIEW_QUEUE.write_text('\n'.join(json.dumps(q) for q in remaining) + '\n' if remaining else '')


# ══════════════════════════════════════════════════════════════════
# 经验归档（结构化，带标签）
# ══════════════════════════════════════════════════════════════════

def _rsi_bucket(rsi) -> str:
    if rsi is None: return 'unknown'
    if rsi < 30: return 'oversold'
    if rsi < 45: return 'weak'
    if rsi < 55: return 'neutral'
    if rsi < 70: return 'strong'
    return 'overbought'

def _fg_bucket(fg) -> str:
    if fg is None: return 'unknown'
    if fg < 25: return 'extreme_fear'
    if fg < 45: return 'fear'
    if fg < 55: return 'neutral'
    if fg < 75: return 'greed'
    return 'extreme_greed'

def _archive_experience(snap: dict):
    """将复盘结果归档为结构化经验条目"""
    exp = {
        'exp_id':       snap['snap_id'],
        'archived_at':  datetime.utcnow().isoformat(),

        # 场景标签（用于相似度检索）
        'symbol':       snap.get('symbol'),
        'direction':    snap.get('direction'),
        'regime':       snap.get('regime'),
        'rsi_bucket':   _rsi_bucket(snap.get('rsi_1h')),
        'fg_bucket':    _fg_bucket(snap.get('fg_index')),
        'score_tier':   'S' if (snap.get('score') or 0) >= 160 else
                        'A' if (snap.get('score') or 0) >= 145 else
                        'B' if (snap.get('score') or 0) >= 130 else 'C',
        'grade_tier':   'A' if (snap.get('grade') or 0) >= 70 else 'B',
        'gex_sign':     'positive' if (snap.get('gex') or 0) > 0 else 'negative',

        # 结果
        'verdict':      snap.get('verdict'),
        'actual_pnl':   snap.get('actual_pnl'),
        'entry_mid':    (snap.get('entry_lo', 0) + snap.get('entry_hi', 0)) / 2,

        # 完整快照引用
        'snap_ref':     snap['snap_id'],
    }

    with open(EXP_LOG, 'a') as f:
        f.write(json.dumps(exp, ensure_ascii=False) + '\n')

    _update_index(exp)
    pass  # [静默]


def _update_index(exp: dict):
    """更新经验索引（场景→经验列表的倒排索引）"""
    try:
        index = json.loads(EXP_INDEX.read_text()) if EXP_INDEX.exists() else {}
    except:
        index = {}

    # 按关键标签建立索引
    keys = [
        f"{exp['symbol']}_{exp['direction']}",
        f"{exp['regime']}_{exp['direction']}",
        f"{exp['rsi_bucket']}_{exp['direction']}",
        f"{exp['regime']}_{exp['rsi_bucket']}_{exp['direction']}",
    ]
    for key in keys:
        index.setdefault(key, [])
        index[key].append(exp['exp_id'])
        index[key] = index[key][-50:]  # 每个key最多保留50条

    EXP_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2))


# ══════════════════════════════════════════════════════════════════
# 场景检索（分析时注入历史经验，对标MAKIMA的RAG）
# ══════════════════════════════════════════════════════════════════

def retrieve_relevant_experiences(symbol: str, direction: str, regime: str,
                                   rsi_1h: float = None, top_k: int = 5) -> list:
    """
    检索最相关的历史经验，注入到梵天分析上下文
    返回: [经验条目列表]，按相关度排序
    """
    if not EXP_LOG.exists() or not EXP_INDEX.exists():
        return []

    try:
        index = json.loads(EXP_INDEX.read_text())
    except:
        return []

    rsi_b = _rsi_bucket(rsi_1h)

    # 构建查询键（优先级从高到低）
    query_keys = [
        f"{symbol}_{direction}",                         # 精确匹配
        f"{regime}_{rsi_b}_{direction}",                 # 体制+RSI+方向
        f"{regime}_{direction}",                          # 体制+方向
        f"{rsi_b}_{direction}",                           # RSI+方向
    ]

    candidate_ids = []
    seen = set()
    for key in query_keys:
        for exp_id in index.get(key, [])[-20:]:
            if exp_id not in seen:
                candidate_ids.append(exp_id)
                seen.add(exp_id)

    if not candidate_ids:
        return []

    # 加载经验条目
    exp_map = {}
    try:
        for line in EXP_LOG.read_text().strip().split('\n'):
            if not line: continue
            exp = json.loads(line)
            exp_map[exp['exp_id']] = exp
    except:
        return []

    experiences = [exp_map[eid] for eid in candidate_ids if eid in exp_map]

    # 按相关度排序（精确匹配优先，correct优先）
    def score_exp(e):
        s = 0
        if e.get('symbol') == symbol: s += 4
        if e.get('regime') == regime: s += 3
        if e.get('rsi_bucket') == rsi_b: s += 2
        if e.get('verdict') == 'correct': s += 1
        return s

    experiences.sort(key=score_exp, reverse=True)
    return experiences[:top_k]


def format_experience_context(experiences: list) -> str:
    """将经验列表格式化为梵天分析上下文注入文本"""
    if not experiences:
        return ''

    lines = [f'[经验库] 检索到 {len(experiences)} 条相关历史经验：']
    for i, exp in enumerate(experiences, 1):
        verdict_icon = '✅' if exp['verdict'] == 'correct' else '❌'
        lines.append(
            f'  经验{i} ({exp.get("archived_at","")[:10]}) '
            f'{verdict_icon} {exp["symbol"]} {exp["direction"]} '
            f'体制={exp["regime"]} RSI={exp["rsi_bucket"]} '
            f'评分级={exp["score_tier"]} → PnL={exp.get("actual_pnl",0):+.2f}%'
        )
    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════
# 统计报告（对标MAKIMA面板）
# ══════════════════════════════════════════════════════════════════

def get_experience_stats() -> dict:
    """返回经验库统计，用于面板展示"""
    if not EXP_LOG.exists():
        return {'total': 0, 'correct': 0, 'wrong': 0, 'wr': 0}

    exps = []
    try:
        for line in EXP_LOG.read_text().strip().split('\n'):
            if line: exps.append(json.loads(line))
    except:
        return {}

    correct = sum(1 for e in exps if e.get('verdict') == 'correct')
    wrong   = sum(1 for e in exps if e.get('verdict') == 'wrong')
    total   = correct + wrong

    # 按体制分组
    regime_stats = {}
    for e in exps:
        r = e.get('regime', 'UNKNOWN')
        regime_stats.setdefault(r, {'correct': 0, 'wrong': 0})
        if e.get('verdict') == 'correct':
            regime_stats[r]['correct'] += 1
        elif e.get('verdict') == 'wrong':
            regime_stats[r]['wrong'] += 1

    return {
        'total':        len(exps),
        'correct':      correct,
        'wrong':        wrong,
        'wr':           correct / total if total > 0 else 0,
        'pending':      sum(1 for e in exps if e.get('verdict') == 'in_progress'),
        'regime_stats': regime_stats,
        'last_updated': datetime.utcnow().isoformat(),
    }


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--review', action='store_true', help='执行24H复盘')
    ap.add_argument('--stats', action='store_true', help='显示经验库统计')
    ap.add_argument('--query', nargs=3, metavar=('SYMBOL','DIR','REGIME'), help='检索相关经验')
    args = ap.parse_args()

    if args.review:
        run_24h_review()
    elif args.stats:
        stats = get_experience_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    elif args.query:
        exps = retrieve_relevant_experiences(args.query[0], args.query[1], args.query[2])
        print(format_experience_context(exps))
    else:
        ap.print_help()
