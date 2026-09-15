#!/usr/bin/env python3
"""
wr_matrix_3d.py — 三维WR矩阵（体制×方向×score分桶）
接入位置：scripts/ic_feedback_engine.py（每周一次）或手动调用
2026-09-07 设计院封印（苏摩111）

目的：
  IC单值掩盖了体制内的异质性。
  三维矩阵让我们看到：BEAR_EARLY:SHORT:>=155 vs CHOP_MID:LONG:<80 的WR差异。
  这是仓位分层的统计基础，也是IC管道的下一步。
"""
import json
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'


def _score_bucket(score: float) -> str:
    if score < 80:   return "<80"
    if score < 130:  return "80-130"
    if score < 145:  return "130-145"
    if score < 160:  return "145-160"
    if score < 175:  return "160-175"
    return ">=175"


def build_3d_matrix(min_n: int = 3) -> dict:
    """
    读取 wuqu_paper_settled.jsonl，构建 regime×direction×score_bucket 三维WR矩阵。
    只输出样本量 >= min_n 的单元格。
    """
    p = DATA / 'wuqu_paper_settled.jsonl'
    if not p.exists():
        return {}

    cells = defaultdict(lambda: {'win': 0, 'loss': 0, 'pnls': []})

    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue

        outcome = d.get('outcome', '')
        if outcome not in ('TP1', 'TP2', 'SL', 'TIMEOUT'):
            continue

        regime    = str(d.get('regime', 'UNKNOWN') or 'UNKNOWN').upper()
        direction = str(d.get('signal_dir', '') or '').upper()
        if direction in ('BUY', '做多'): direction = 'LONG'
        if direction in ('SELL', '做空'): direction = 'SHORT'
        score     = float(d.get('score', 0) or 0)
        pnl_pct   = float(d.get('pnl_pct', 0) or 0)
        bucket    = _score_bucket(score)

        key = f"{regime}:{direction}:{bucket}"
        if outcome in ('TP1', 'TP2'):
            cells[key]['win'] += 1
        else:
            cells[key]['loss'] += 1
        cells[key]['pnls'].append(pnl_pct)
        # 标记数据来源时间（检测历史污染）
        source = d.get('_data_quality', '')
        if source:
            cells[key].setdefault('sources', set()).add(source)

    matrix = {}
    for key, v in cells.items():
        n = v['win'] + v['loss']
        if n < min_n:
            continue
        wr = v['win'] / n
        avg_pnl = sum(v['pnls']) / len(v['pnls']) if v['pnls'] else 0
        ev = wr * avg_pnl - (1 - wr) * abs(avg_pnl) if avg_pnl != 0 else 0
        matrix[key] = {
            'n': n, 'wr': round(wr, 4),
            'avg_pnl': round(avg_pnl, 4),
            'ev': round(ev, 4),
        }

    return matrix


def print_matrix(matrix: dict):
    """按EV降序打印矩阵，高亮值得开的和应该避免的"""
    if not matrix:
        print("  （无足够样本的矩阵单元格，需要更多数据）")
        return

    # 数据质量警告：历史污染检测
    polluted = {k for k,v in all_data.items() if 'bridge-v1' in v.get('sources',set())}
    if polluted:
        print(f"  ⚠️  含bridge-v1数据（paper_orders桥接）的单元格: {len(polluted)}个")
        print(f"     这些WR包含门控上线前的历史单，可能虚高")
        print()

    rows = sorted(matrix.items(), key=lambda x: x[1]['ev'], reverse=True)
    total = len(rows)

    print(f"  {'体制:方向:分桶':<35} {'n':>5} {'WR':>7} {'avgPnL':>8} {'EV':>7}")
    print("  " + "-" * 66)
    for key, v in rows:
        wr_bar = "█" * int(v['wr'] * 20)
        flag = "🟢" if v['ev'] > 0.5 else ("🔴" if v['ev'] < -0.3 else "  ")
        print(f"  {flag} {key:<33} {v['n']:>5} {v['wr']:>6.1%} {v['avg_pnl']:>8.2f}% {v['ev']:>7.3f}")


def save_matrix(matrix: dict):
    out = DATA / 'wr_matrix_3d.json'
    out.write_text(json.dumps(matrix, indent=2, ensure_ascii=False))
    return out


def main():
    print("="*60)
    print("  🏛️ 梵天三维WR矩阵  （体制×方向×score）")
    print("="*60)
    print()

    matrix = build_3d_matrix(min_n=3)
    total_cells = len(matrix)
    all_data = build_3d_matrix(min_n=1)
    total_n = sum(v['n'] for v in all_data.values())

    print(f"  数据总量: {total_n}条  有效单元格(n≥3): {total_cells}个")
    print()

    # 分类输出
    high_ev = {k: v for k, v in matrix.items() if v['ev'] > 0.5}
    avoid   = {k: v for k, v in matrix.items() if v['ev'] < -0.3}
    neutral = {k: v for k, v in matrix.items() if -0.3 <= v['ev'] <= 0.5}

    if high_ev:
        print(f"🟢 高EV单元格（EV>0.5，共{len(high_ev)}个）：")
        print_matrix(high_ev)
        print()

    if avoid:
        print(f"🔴 应避免单元格（EV<-0.3，共{len(avoid)}个）：")
        print_matrix(avoid)
        print()

    if neutral:
        print(f"⚪ 中性单元格（共{len(neutral)}个）：")
        print_matrix(neutral)
        print()

    # 保存
    out = save_matrix(matrix)
    print(f"  ✅ 已保存: {out}")
    print()

    # IC补充分析：score是否单调递增
    bucket_wr = defaultdict(lambda: {'win': 0, 'n': 0})
    for key, v in all_data.items():
        bucket = key.split(':')[2]
        bucket_wr[bucket]['win'] += round(v['wr'] * v['n'])
        bucket_wr[bucket]['n'] += v['n']

    print("  Score单调性检验（不分体制/方向）：")
    order = ['<80', '80-130', '130-145', '145-160', '160-175', '>=175']
    prev_wr = None
    monotonic = True
    for b in order:
        if b not in bucket_wr: continue
        bv = bucket_wr[b]
        wr = bv['win'] / bv['n'] if bv['n'] else 0
        flag = "↑" if prev_wr is None or wr >= prev_wr else "↓⚠️"
        if prev_wr is not None and wr < prev_wr:
            monotonic = False
        print(f"    {b:<10} n={bv['n']:>4}  WR={wr:.1%}  {flag}")
        prev_wr = wr

    print()
    print(f"  IC判断: WR{'单调递增✅' if monotonic else '非单调❌ — 高分不等于高胜率，禁止用score当仓位油门'}")
    print("="*60)

    return matrix


if __name__ == '__main__':
    main()
