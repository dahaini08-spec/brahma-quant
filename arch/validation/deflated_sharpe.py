#!/usr/bin/env python3
"""
Deflated Sharpe Ratio (DSR) — Bailey & López de Prado
======================================================
Phase B · 2026-09-13 三方联合深度评估

DSR公式:
  DSR = [SR * sqrt(N - 1) - Z_alpha * sqrt(var(SR))]
        / sqrt(N - 1 - Z_alpha^2 * var(SR))

其中:
  SR = 样本夏普比
  N = 真实试验次数（不是1，是仓库里改过的规则次数）
  var(SR) = 夏普比的方差（用Mertens' asymptotic variance）
  Z_alpha = 标准正态的alpha分位数（单侧95% → 1.645）

如果 DSR > 0 → 策略在多重比较后仍有统计显著性
如果 DSR < 0 → 策略的夏普可能是试了很多次碰巧得到的

参考:
- Bailey, D. & López de Prado, M. (2014) "The Deflated Sharpe Ratio"
- López de Prado, M. (2018) "Advances in Financial Machine Learning" Ch.8

Usage:
    python3 arch/validation/deflated_sharpe.py --signals data/live_signal_log.jsonl
"""
import json, math, argparse, sys
from pathlib import Path
from datetime import datetime, timezone

# ── Z分布 ─────────────────────────────────────────────────
def norm_ppf(p):
    """标准正态的分位数函数（近似）"""
    # Beasley-Springer-Moro算法
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104693690e+02,
         1.383577526561256e+02, -3.066479806167829e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p <= phigh:
        q = p - 0.5
        r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    else:
        q = math.sqrt(-2 * math.log(1-p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)

# ── DSR计算 ───────────────────────────────────────────────
def compute_sharpe(pnls: list[float], annualization_factor: int = 156) -> tuple:
    """计算夏普比及其方差"""
    n = len(pnls)
    if n < 2:
        return 0, 0, 0
    
    mean = sum(pnls) / n
    var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    std = math.sqrt(var)
    
    if std == 0:
        return 0, 0, n
    
    sr = mean / std * math.sqrt(annualization_factor)
    
    # Mertens' asymptotic variance of SR:
    # var(SR) = (1 + 0.5 * SR^2) / N
    var_sr = (1 + 0.5 * sr ** 2) / n
    
    return sr, var_sr, n

def deflated_sharpe_ratio(
    pnls: list[float],
    n_trials: int = 1,
    alpha: float = 0.05,
    annualization_factor: int = 156,
) -> dict:
    """
    计算Deflated Sharpe Ratio
    
    参数:
    - pnls: 逐笔收益率列表（百分比）
    - n_trials: 真实试验次数（仓库里改过多少次规则/参数）
    - alpha: 显著性水平（默认5%）
    - annualization_factor: 年化因子（每周3笔→年156笔）
    
    返回:
    - sr: 样本夏普比
    - dsr: deflated夏普比
    - is_significant: DSR > 0
    """
    sr, var_sr, n = compute_sharpe(pnls, annualization_factor)
    
    if n < 2 or var_sr == 0:
        return {
            'sr': round(sr, 4),
            'dsr': 0,
            'n': n,
            'n_trials': n_trials,
            'is_significant': False,
            'error': '样本不足',
        }
    
    # Z_alpha（单侧检验）
    z_alpha = norm_ppf(1 - alpha)  # 95% → 1.645
    
    # DSR公式
    # DSR = SR * sqrt(N-1) - Z_alpha * sqrt(var(SR))
    #       / sqrt(N-1 - Z_alpha^2 * var(SR))
    # 
    # 但原始公式中N是试验次数，用n_trials
    # 更正: 使用 López de Prado (2014) 的原始定义:
    # SR_0 = E[max(SR_k)] for k=1..N (预期最大夏普，在N次试验下)
    # SR_0 ≈ Z_alpha * sqrt(var(SR))  (Bonferroni近似)
    # DSR = (SR - SR_0) / sqrt(var(SR))
    
    # 预期最大夏普（N次试验下的最高夏普期望）
    if n_trials > 1:
        sr_0 = norm_ppf(1 - 1.0 / n_trials) * math.sqrt(var_sr)
    else:
        sr_0 = 0
    
    # Deflated Sharpe
    if var_sr > 0:
        dsr = (sr - sr_0) / math.sqrt(var_sr)
    else:
        dsr = 0
    
    # 传统DSR公式（Bailey-LdP 2014 exact）
    # SR_deflated = [SR * sqrt(N-1) - Z_alpha * sqrt(var_SR)]
    #              / sqrt(N-1 - Z_alpha^2 * var_SR)
    # 其中N = n (样本量), 不是n_trials
    sqrt_n1 = math.sqrt(n - 1)
    denom = n - 1 - z_alpha ** 2 * var_sr
    
    if denom > 0:
        sr_deflated_classic = (sr * sqrt_n1 - z_alpha * math.sqrt(var_sr)) / math.sqrt(denom)
    else:
        sr_deflated_classic = 0
    
    return {
        'sr': round(sr, 4),
        'sr_0': round(sr_0, 4),
        'dsr': round(dsr, 4),
        'dsr_classic': round(sr_deflated_classic, 4),
        'var_sr': round(var_sr, 6),
        'n': n,
        'n_trials': n_trials,
        'z_alpha': round(z_alpha, 4),
        'alpha': alpha,
        'is_significant': dsr > 0,
        'threshold': 1.0,  # 行业标准: DSR > 1.0 为强显著
    }

# ── 估算真实试验次数 ────────────────────────────────────────
def estimate_n_trials(repo_path: str = '.') -> int:
    """
    估算真实试验次数:
    1. git log中包含"规则"/"参数"/"阈值"/"策略"的commit数
    2. scoring_config.json的变更次数
    3. MEMORY.md中"封印"次数
    """
    n = 1  # 基线
    
    # Git commit数（粗估）
    import subprocess
    try:
        result = subprocess.run(
            ['git', 'log', '--oneline', '--all'],
            capture_output=True, text=True, cwd=repo_path
        )
        commits = result.stdout.strip().split('\n')
        # 筛选策略相关commit
        strategy_keywords = ['规则', '参数', '阈值', '策略', '封印', 'regime', 'score', 'weight', 'SL', 'RR', 'WR']
        strategy_commits = [c for c in commits if any(kw.lower() in c.lower() for kw in strategy_keywords)]
        n += len(strategy_commits)
    except:
        pass
    
    # MEMORY.md封印数
    try:
        memory = Path(repo_path) / 'MEMORY.md'
        if memory.exists():
            content = memory.read_text()
            n += content.count('封印')
    except:
        pass
    
    # scoring_config变更
    try:
        result = subprocess.run(
            ['git', 'log', '--oneline', '--', 'data/scoring_config.json'],
            capture_output=True, text=True, cwd=repo_path
        )
        n += len(result.stdout.strip().split('\n'))
    except:
        pass
    
    return max(n, 1)

# ── 数据加载 ──────────────────────────────────────────────
def load_pnls(path: str) -> list[float]:
    """从live_signal_log加载成本后逐笔收益率"""
    pnls = []
    with open(path) as f:
        for line in f:
            try:
                s = json.loads(line)
            except:
                continue
            if not s.get('settled') and s.get('status') not in ('TP1','TP2','SL','TIMEOUT','EXPIRED','EXPIRED_NO_TOUCH'):
                continue
            pnl = s.get('pnl_pct')
            if pnl is None:
                outcome = s.get('outcome', '')
                sl = s.get('sl_pct', 2.0)
                rr = s.get('rr1', 1.0)
                if outcome == 'TP1':
                    pnl = rr * sl
                elif outcome == 'TP2':
                    pnl = rr * sl * 2
                elif outcome == 'SL':
                    pnl = -sl
                else:
                    continue
            lev = s.get('leverage_used', 5.0)
            cost = 0.0005 + 0.0002  # taker + slippage
            # 成本后收益: pnl% × leverage - 2×cost × leverage × 100
            net_pnl = pnl * lev - 2 * cost * 100 * lev
            pnls.append(net_pnl)
    return pnls

# ── 主入口 ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Deflated Sharpe Ratio Calculator')
    parser.add_argument('--signals', default='data/live_signal_log.jsonl')
    parser.add_argument('--trials', type=int, default=0, help='手动指定试验次数')
    parser.add_argument('--report', action='store_true')
    args = parser.parse_args()
    
    pnls = load_pnls(args.signals)
    print(f"加载 {len(pnls)} 笔成本后收益")
    
    if len(pnls) < 10:
        print("⚠️ 样本不足10笔")
    
    n_trials = args.trials if args.trials > 0 else estimate_n_trials()
    print(f"估计试验次数 N={n_trials}")
    
    result = deflated_sharpe_ratio(pnls, n_trials=n_trials)
    
    print(f"\n{'='*50}")
    print(f"Deflated Sharpe Ratio Report")
    print(f"{'='*50}")
    print(f"  样本量:       {result['n']}")
    print(f"  试验次数 N:   {result['n_trials']}")
    print(f"  样本夏比 SR:  {result['sr']}")
    print(f"  期望最大SR:   {result['sr_0']} (N次试验下的Bonferroni上限)")
    print(f"  SR方差:       {result['var_sr']}")
    print(f"  Z_alpha:      {result['z_alpha']} (单侧{1-result['alpha']:.0%})")
    print(f"  DSR (LdP):    {result['dsr']}")
    print(f"  DSR (Classic):{result['dsr_classic']}")
    print(f"  阈值:         {result['threshold']} (行业标准 > 1.0)")
    print(f"  判定:         {'✅ 显著' if result['is_significant'] else '❌ 不显著'}")
    
    if args.report:
        out = Path('docs/phase_b_dsr_report.md')
        report = f"# DSR Report\n\n```json\n{json.dumps(result, indent=2)}\n```"
        out.write_text(report)
    
    out = Path('data/phase_b_dsr.json')
    out.write_text(json.dumps(result, indent=2))
    print(f"\n保存: {out}")

if __name__ == '__main__':
    main()
