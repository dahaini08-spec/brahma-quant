#!/usr/bin/env python3
"""
Deflated Sharpe Ratio + P-Value
López de Prado《Advances in Financial Machine Learning》方法
梵天进化协议·验证层

用法:
  from arch.validation.deflated_sharpe import DeflatedSharpeRatio, run_brahma_audit
  结果 = DeflatedSharpeRatio.calculate(observed_sr=2.5, n_trials=63)
"""
import numpy as np
from scipy.stats import norm
from typing import Dict
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent.parent


class DeflatedSharpeRatio:
    """
    Deflated Sharpe Ratio + P-Value
    核心公式：DSR = Φ(z)，z = (SR_obs - SR_bench) / σ_SR
    σ_SR 考虑收益率非正态性（偏度 + 峰度）
    """

    @staticmethod
    def calculate(
        observed_sr:   float,
        n_trials:      int,
        sr_benchmark:  float = 0.0,
        skewness:      float = 0.0,
        kurtosis:      float = 3.0,
        n_obs:         int   = 252,   # 用于σ_SR计算的观测数量
        min_trials:    int   = 2,
    ) -> Dict:
        """
        参数
        ----
        observed_sr   : 观测到的年化 Sharpe Ratio
        n_trials      : 已测试的策略/参数变体数量（越多惩罚越重）
        sr_benchmark  : 基准 Sharpe（通常=0，即"跑赢随机"）
        skewness      : 收益率偏度（正=右偏）
        kurtosis      : 收益率峰度（正态=3，厚尾>3）
        n_obs         : 样本量（交易笔数或交易日数）
        """
        n_trials = max(n_trials, min_trials)
        n_obs    = max(n_obs, 2)

        # ── σ²(SR)：考虑非正态性的 Sharpe 方差 ──────────────
        # 公式来自 Lo (2002) + López de Prado (2018)
        # σ²(ŜR) = (1 - γ₃·SR + (γ₄-1)/4 · SR²) / (T-1)
        # 简化（SR量级通常<3）：
        var_sr = (1.0 - skewness * observed_sr + (kurtosis - 1.0) / 4.0 * observed_sr**2) / (n_obs - 1.0)
        var_sr = max(var_sr, 1e-8)

        # ── Z-score（多重测试校正：Bonferroni近似） ──────────
        # SR_bench* = Φ⁻¹(1 - 1/n_trials) 作为期望最大值
        expected_max_sr = norm.ppf(1.0 - 1.0 / n_trials) if n_trials > 1 else 0.0
        sr_bench_adj    = max(sr_benchmark, expected_max_sr * np.sqrt(var_sr))

        z_score = (observed_sr - sr_bench_adj) / np.sqrt(var_sr)

        # ── DSR = Φ(z) ──────────────────────────────────────
        dsr     = float(norm.cdf(z_score))
        p_value = 1.0 - dsr

        # ── 置信度分级 ────────────────────────────────────────
        if dsr >= 0.99:
            confidence   = "极高置信 (99%+)"
            significance = "★★★★★"
            action       = "✅ 允许进入影子验证"
        elif dsr >= 0.95:
            confidence   = "高置信 (95%+)"
            significance = "★★★★☆"
            action       = "✅ 允许进入影子验证"
        elif dsr >= 0.90:
            confidence   = "中高置信 (90%+)"
            significance = "★★★☆☆"
            action       = "⚠️ 加强影子验证，增加样本"
        elif dsr >= 0.75:
            confidence   = "中等置信 (75%+)"
            significance = "★★☆☆☆"
            action       = "⚠️ 仅做观察，不合并进化"
        else:
            confidence   = "低置信（高概率为数据挖掘偏差）"
            significance = "★☆☆☆☆"
            action       = "❌ 拒绝该进化"

        interpretation = (
            "通过多重测试校正，策略有真实技能"
            if p_value < 0.05 else
            "⚠️ 可能为数据挖掘偏差，建议增加样本或减少测试量"
        )

        return {
            "observed_sr":     round(observed_sr, 4),
            "deflated_sr":     round(dsr, 6),
            "p_value":         round(p_value, 6),
            "p_value_pct":     f"{p_value * 100:.3f}%",
            "n_trials":        int(n_trials),
            "n_obs":           int(n_obs),
            "z_score":         round(z_score, 4),
            "sr_bench_adj":    round(sr_bench_adj, 4),
            "confidence":      confidence,
            "significance":    significance,
            "action":          action,
            "interpretation":  interpretation,
        }

    @staticmethod
    def compare(sr_old: float, sr_new: float,
                n_trials: int, n_obs: int = 63,
                skewness: float = 0.3, kurtosis: float = 3.5) -> Dict:
        """
        新旧策略对比：新逻辑是否统计显著优于旧逻辑
        sr_old: 对照组 Sharpe
        sr_new: 实验组 Sharpe（新进化）
        """
        old = DeflatedSharpeRatio.calculate(sr_old, n_trials=1, n_obs=n_obs,
                                             skewness=skewness, kurtosis=kurtosis)
        new = DeflatedSharpeRatio.calculate(sr_new, n_trials=n_trials, n_obs=n_obs,
                                             skewness=skewness, kurtosis=kurtosis)
        uplift    = sr_new - sr_old
        rel_gain  = uplift / max(abs(sr_old), 1e-6)
        approved  = new['deflated_sr'] >= 0.95 and uplift > 0

        return {
            "old": old,
            "new": new,
            "sr_uplift":   round(uplift, 4),
            "rel_gain_pct": f"{rel_gain*100:.1f}%",
            "evolution_approved": approved,
            "verdict": "✅ 进化通过" if approved else "❌ 进化被拒",
        }


# ─────────────────────────────────────────────────────────────────
# 梵天实盘审计：从武曲Paper + CPCV数据生成DSR报告
# ─────────────────────────────────────────────────────────────────
def run_brahma_audit() -> Dict:
    """读取武曲Paper实际数据，计算真实DSR"""
    import math

    # 加载武曲Paper已结算交易
    settled = []
    wuqu_path = ROOT / 'data/wuqu_paper_settled.jsonl'
    with open(wuqu_path) as f:
        for line in f:
            try: settled.append(json.loads(line))
            except: pass

    wins     = [t for t in settled if t.get('outcome') in ('TP1', 'TP2')]
    losses   = [t for t in settled if t.get('outcome') == 'SL']
    timeouts = [t for t in settled if t.get('outcome') == 'TIMEOUT']
    n_eff    = len(wins) + len(losses)

    if n_eff == 0:
        return {'error': '无有效交易'}

    wr = len(wins) / n_eff

    # pnl序列
    pnls = []
    for t in wins + losses:
        v = float(t.get('pnl_pct', 0))
        pnls.append(v / 10000 if abs(v) > 10 else v)

    arr  = np.array(pnls)
    mean = arr.mean()
    std  = arr.std() if arr.std() > 0 else 1e-9
    sr_ann = mean / std * math.sqrt(252)

    # 偏度 / 峰度
    n    = len(arr)
    skew = float(np.mean(((arr - mean) / std) ** 3)) if std > 0 else 0.0
    kurt = float(np.mean(((arr - mean) / std) ** 4)) if std > 0 else 3.0

    # 估算测试了多少次（每个symbol+regime组合视为一次独立测试）
    combos    = {(t.get('symbol',''), t.get('regime','')) for t in settled}
    n_trials  = max(len(combos), 5)

    dsr_result = DeflatedSharpeRatio.calculate(
        observed_sr  = sr_ann,
        n_trials     = n_trials,
        skewness     = skew,
        kurtosis     = kurt,
        n_obs        = n_eff,
    )

    return {
        'ts':          datetime.now(timezone.utc).isoformat(),
        'n_settled':   n_eff,
        'n_timeout':   len(timeouts),
        'wr':          round(wr, 4),
        'sr_raw':      round(sr_ann, 4),
        'skewness':    round(skew, 4),
        'kurtosis':    round(kurt, 4),
        'n_trials':    n_trials,
        'dsr':         dsr_result,
    }


# ─────────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────────
def main():
    print('\n🏯 梵天 Deflated Sharpe Ratio 审计')
    print(f'   {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    print('   López de Prado《AFML》方法论\n')

    # ── 场景1：梵天当前真实数据 ──────────────────────────────
    print('── 场景A：武曲Paper实盘审计 ──')
    audit = run_brahma_audit()
    if 'error' not in audit:
        d = audit['dsr']
        print(f'  交易笔数: {audit["n_settled"]} | TIMEOUT: {audit["n_timeout"]}')
        print(f'  WR={audit["wr"]:.1%} | 原始SR={audit["sr_raw"]:.3f}')
        print(f'  偏度={audit["skewness"]:.3f} | 峰度={audit["kurtosis"]:.3f}')
        print(f'  测试组合数(n_trials)={audit["n_trials"]}')
        print(f'  ─────────────────────────────')
        print(f'  DSR={d["deflated_sr"]} | P-Value={d["p_value_pct"]}')
        print(f'  Z-score={d["z_score"]} | 校正基准SR={d["sr_bench_adj"]}')
        print(f'  置信度: {d["confidence"]} {d["significance"]}')
        print(f'  决策: {d["action"]}')
        print(f'  解读: {d["interpretation"]}')

    # ── 场景2：典型进化决策对比 ──────────────────────────────
    print('\n── 场景B：进化对比（旧逻辑 vs 新维度s20）──')
    cmp = DeflatedSharpeRatio.compare(
        sr_old=1.80, sr_new=2.20,
        n_trials=15,  # CPCV 15条路径
        n_obs=63,
        skewness=0.3, kurtosis=3.5,
    )
    print(f'  旧SR=1.80 → 新SR=2.20 | 提升={cmp["sr_uplift"]:+.2f} ({cmp["rel_gain_pct"]})')
    print(f'  新DSR={cmp["new"]["deflated_sr"]} | P={cmp["new"]["p_value_pct"]}')
    print(f'  {cmp["verdict"]}')

    # ── 场景3：不同n_trials的惩罚演示 ──────────────────────────
    print('\n── 场景C：多重测试惩罚（相同SR=2.5，不同测试次数）──')
    print(f'  {"n_trials":>8}  {"DSR":>8}  {"P-Value":>10}  {"决策"}')
    for nt in [5, 15, 50, 200, 1000]:
        r = DeflatedSharpeRatio.calculate(2.5, n_trials=nt, n_obs=63,
                                           skewness=0.3, kurtosis=3.5)
        print(f'  {nt:>8}  {r["deflated_sr"]:>8.4f}  {r["p_value_pct"]:>10}  {r["action"]}')

    # 保存报告
    out = ROOT / 'data/dsr_report.json'
    out.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str))
    print(f'\n✅ DSR报告已保存: {out}')


if __name__ == '__main__':
    main()
