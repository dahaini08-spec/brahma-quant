#!/usr/bin/env python3
"""
Sequential Bootstrap for Financial Labels
López de Prado《AFML》第4章 完整实现

原理：
  金融标签窗口（如 Triple-Barrier）相互重叠
  普通 Bootstrap 会重复采样重叠区间 → 虚假精度
  Sequential Bootstrap：只采样与已选样本独立（或低重叠）的标签
  → 更保守、更真实的统计估计

梵天数据检测结果：
  重叠率=64.6% / avg_uniqueness=0.11 → 严重重叠，普通Bootstrap严重高估精度

用法:
  python3 arch/validation/sequential_bootstrap.py
  from arch.validation.sequential_bootstrap import sequential_bootstrap, get_ind_matrix
"""
import numpy as np
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional

ROOT = Path(__file__).parent.parent.parent


# ─────────────────────────────────────────────────────────────────
# 核心函数
# ─────────────────────────────────────────────────────────────────

def get_ind_matrix(t0_list: List[datetime],
                   t1_list: List[datetime]) -> np.ndarray:
    """
    构建指示矩阵 Φ
    Φ[i,j] = 1 当且仅当 标签j 的窗口 [t0_j, t1_j] 包含时间点 t_i
    行 = 时间点（所有标签的open_ts）
    列 = 标签
    """
    # 时间轴：所有标签的开始时间（去重排序）
    time_axis = sorted(set(t0_list))
    n_times   = len(time_axis)
    n_labels  = len(t0_list)

    phi = np.zeros((n_times, n_labels), dtype=np.float32)
    time_idx = {t: i for i, t in enumerate(time_axis)}

    for j in range(n_labels):
        for i, t in enumerate(time_axis):
            if t0_list[j] <= t <= t1_list[j]:
                phi[i, j] = 1.0

    return phi, time_axis


def get_avg_uniqueness(phi: np.ndarray) -> np.ndarray:
    """
    计算每个标签的平均唯一性
    c_t = phi[:,j].sum(axis=1)  每个时间点被多少标签覆盖
    u_tj = phi[t,j] / c_t       标签j在时间t的唯一性
    avg_u[j] = mean(u_tj for t where phi[t,j]==1)
    """
    c_t = phi.sum(axis=1)           # (n_times,)  每时刻并发标签数
    c_t = np.where(c_t == 0, 1, c_t)  # 防除零

    # 唯一性矩阵
    u = phi / c_t[:, np.newaxis]    # (n_times, n_labels)

    avg_u = np.zeros(phi.shape[1])
    for j in range(phi.shape[1]):
        mask = phi[:, j] > 0
        avg_u[j] = u[mask, j].mean() if mask.any() else 1.0

    return avg_u


def sequential_bootstrap(phi:     np.ndarray,
                          n_draws: int = None) -> np.ndarray:
    """
    Sequential Bootstrap（López de Prado AFML 算法4.1）

    每次从剩余标签中按唯一性权重采样：
      1. 计算当前已选集合的 avg_uniqueness（不含当前候选）
      2. 权重 ∝ avg_uniqueness（越独立越容易被选中）
      3. 放回采样

    返回: 采样索引数组 shape=(n_draws,)
    """
    n_labels = phi.shape[1]
    if n_draws is None:
        n_draws = n_labels

    selected = []
    for _ in range(n_draws):
        # 已选标签构成的子矩阵
        if selected:
            phi_sel = phi[:, selected]
            c_t_sel = phi_sel.sum(axis=1)  # 已选集合的并发数
        else:
            c_t_sel = np.zeros(phi.shape[0])

        # 计算每个候选标签加入后的唯一性
        avg_u = np.zeros(n_labels)
        for j in range(n_labels):
            c_t_j = c_t_sel + phi[:, j]   # 加入j后的并发数
            c_t_j = np.where(c_t_j == 0, 1, c_t_j)
            mask = phi[:, j] > 0
            if mask.any():
                avg_u[j] = (phi[mask, j] / c_t_j[mask]).mean()
            else:
                avg_u[j] = 1.0

        # 权重归一化（softmax-like）
        weights = avg_u / avg_u.sum()
        idx = np.random.choice(n_labels, p=weights)
        selected.append(idx)

    return np.array(selected)


# ─────────────────────────────────────────────────────────────────
# 梵天武曲Paper数据加载
# ─────────────────────────────────────────────────────────────────

def load_wuqu_intervals() -> Tuple[List[datetime], List[datetime], List[float]]:
    """返回 (t0_list, t1_list, pnl_list)"""
    records = []
    with open(ROOT / 'data/wuqu_paper_settled.jsonl') as f:
        for line in f:
            try: records.append(json.loads(line))
            except: pass

    t0s, t1s, pnls = [], [], []
    for r in records:
        if r.get('outcome') not in ('TP1', 'TP2', 'SL'):
            continue
        ts0_str = r.get('open_ts', '')
        ts1_str = r.get('close_ts', '')
        if not ts0_str:
            continue
        try:
            t0 = datetime.fromisoformat(ts0_str.replace('Z', '+00:00'))
        except:
            continue
        if ts1_str:
            try:
                t1 = datetime.fromisoformat(ts1_str.replace('Z', '+00:00'))
            except:
                t1 = t0 + timedelta(hours=float(r.get('hold_hours', 24)))
        else:
            t1 = t0 + timedelta(hours=float(r.get('hold_hours', 24)))

        pnl = float(r.get('pnl_pct', 0))
        t0s.append(t0)
        t1s.append(t1)
        pnls.append(pnl / 10000 if abs(pnl) > 10 else pnl)

    return t0s, t1s, pnls


# ─────────────────────────────────────────────────────────────────
# 对比：普通Bootstrap vs Sequential Bootstrap
# ─────────────────────────────────────────────────────────────────

def compare_bootstrap_methods(pnls:   np.ndarray,
                               phi:   np.ndarray,
                               n_sims: int = 5000) -> dict:
    """
    对比两种方法估计的 WR 和 Sharpe 分布
    普通Bootstrap：随机有放回
    Sequential Bootstrap：按唯一性权重
    """
    n = len(pnls)

    # 普通 Bootstrap
    std_wrs, std_srs = [], []
    for _ in range(n_sims):
        idx = np.random.choice(n, size=n, replace=True)
        s   = pnls[idx]
        std_wrs.append((s > 0).mean())
        sr  = s.mean() / (s.std() + 1e-9) * np.sqrt(252)
        std_srs.append(sr)

    # Sequential Bootstrap（近似：先算avg_uniqueness作为静态权重）
    avg_u   = get_avg_uniqueness(phi)
    weights = avg_u / avg_u.sum()
    seq_wrs, seq_srs = [], []
    for _ in range(n_sims):
        idx = np.random.choice(n, size=n, replace=True, p=weights)
        s   = pnls[idx]
        seq_wrs.append((s > 0).mean())
        sr  = s.mean() / (s.std() + 1e-9) * np.sqrt(252)
        seq_srs.append(sr)

    def stats(arr):
        a = np.array(arr)
        return {
            'mean': round(float(a.mean()), 4),
            'std':  round(float(a.std()),  4),
            'p5':   round(float(np.percentile(a, 5)), 4),
            'p50':  round(float(np.percentile(a, 50)), 4),
            'p95':  round(float(np.percentile(a, 95)), 4),
        }

    return {
        'standard_bootstrap': {'wr': stats(std_wrs), 'sharpe': stats(std_srs)},
        'sequential_bootstrap': {'wr': stats(seq_wrs), 'sharpe': stats(seq_srs)},
        'avg_uniqueness_mean': round(float(avg_u.mean()), 4),
        'n_labels': n,
    }


# ─────────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────────

def main():
    print(f'\n🏯 梵天 Sequential Bootstrap 验证')
    print(f'   {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    print(f'   López de Prado《AFML》第4章\n')

    # 加载数据
    t0s, t1s, pnls = load_wuqu_intervals()
    pnls_arr = np.array(pnls)
    n = len(pnls)
    print(f'武曲Paper有效交易: {n}条')

    # 构建指示矩阵
    print('构建标签重叠矩阵...')
    phi, time_axis = get_ind_matrix(t0s, t1s)
    print(f'时间轴点数: {len(time_axis)} | 矩阵: {phi.shape}')

    # 平均唯一性
    avg_u = get_avg_uniqueness(phi)
    print(f'\n平均唯一性: {avg_u.mean():.4f}')
    print(f'  最低唯一性: {avg_u.min():.4f} (最严重重叠)')
    print(f'  最高唯一性: {avg_u.max():.4f}')

    ov_count = sum(
        1 for i in range(n) for j in range(i+1, n)
        if t0s[j] <= t1s[i] and t0s[i] <= t1s[j]
    )
    total_pairs = n * (n-1) / 2
    print(f'重叠标签对: {ov_count}/{int(total_pairs)} ({ov_count/total_pairs*100:.1f}%)')

    # 对比两种方法
    print('\n计算 Standard vs Sequential Bootstrap 差异（5,000次模拟）...')
    result = compare_bootstrap_methods(pnls_arr, phi, n_sims=5000)

    sb = result['standard_bootstrap']
    sq = result['sequential_bootstrap']

    print(f'\n{"":10} {"Standard Bootstrap":>22} {"Sequential Bootstrap":>22}')
    print(f'{"─"*58}')
    print(f'{"WR mean":10} {sb["wr"]["mean"]:>22.4f} {sq["wr"]["mean"]:>22.4f}')
    print(f'{"WR std":10} {sb["wr"]["std"]:>22.4f} {sq["wr"]["std"]:>22.4f}  ← 修正后更真实')
    print(f'{"WR P5":10} {sb["wr"]["p5"]:>22.4f} {sq["wr"]["p5"]:>22.4f}')
    print(f'{"WR P95":10} {sb["wr"]["p95"]:>22.4f} {sq["wr"]["p95"]:>22.4f}')
    print(f'{"SR mean":10} {sb["sharpe"]["mean"]:>22.2f} {sq["sharpe"]["mean"]:>22.2f}')
    print(f'{"SR std":10} {sb["sharpe"]["std"]:>22.2f} {sq["sharpe"]["std"]:>22.2f}')

    # 解读
    wr_diff = abs(sb['wr']['mean'] - sq['wr']['mean'])
    sr_diff = abs(sb['sharpe']['mean'] - sq['sharpe']['mean'])
    std_inflation = sb['wr']['std'] / max(sq['wr']['std'], 1e-6)

    print(f'\n📊 修正效果:')
    print(f'  WR偏差: {wr_diff*100:.2f}%（普通Bootstrap WR偏高/偏低）')
    print(f'  SR偏差: {sr_diff:.3f}')
    print(f'  精度虚胀倍数: {std_inflation:.2f}x（普通Bootstrap的置信区间被收窄了{std_inflation:.1f}倍）')

    if std_inflation > 1.5:
        print(f'  ⚠️  普通Bootstrap严重高估精度（虚胀{std_inflation:.1f}x），后续MC报告切换Sequential')
    else:
        print(f'  ✅  两种方法差异较小，当前样本量下影响可控')

    # 保存结果
    import json as _json
    out = ROOT / 'data/seq_bootstrap_report.json'
    out.write_text(_json.dumps({
        'ts': datetime.now(timezone.utc).isoformat(),
        'n_labels': n,
        'avg_uniqueness': round(float(avg_u.mean()), 4),
        'overlap_rate': round(ov_count / total_pairs, 4),
        'comparison': result,
    }, ensure_ascii=False, indent=2))
    print(f'\n✅ 报告已保存: {out}')


if __name__ == '__main__':
    main()
