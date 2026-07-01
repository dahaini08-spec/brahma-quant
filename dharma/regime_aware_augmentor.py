"""
regime_aware_augmentor.py — 达摩院合成数据扩充器 v1.0
═══════════════════════════════════════════════════════
设计院·达摩院 封印 2026-07-01

使命：
  解决XGBoost训练样本瓶颈（现有n≈500，目标n≥5000）
  方法：基于228k行真实特征数据 + WR矩阵校正，生成
       体制平衡的合成训练集，消除bucket稀缺偏差

现有数据资产（已确认在库）：
  xgb_train_v2_features.parquet: 228,067 rows ✅
  xgb_dharma_v3_gte4.parquet:      6,229 rows ✅
  btcusdt_1h_2018_2026.parquet:   74,090 rows ✅

方法论：
  1. 从真实数据按 regime×direction×score_tier 分层采样
  2. 在每个bucket内加 ±noise（保持分布，增加多样性）
  3. 基于 wr_matrix_v7 给合成样本打标签（0/1）
  4. 输出：用于 XGB 重训的标准特征文件

达摩院红线：
  - 合成数据标签必须基于统计WR，不得手工调整
  - 合成数据比例上限：不超过真实数据的 4x（防过合成）
  - 输出前必须运行 sanity_check()
"""

# ── STATUS: ACTIVE ────────────────────────────────────────────
# P0级模块，XGB重训前必须调用
# LAST_REVIEW: 2026-07-01 | 设计院初次封印
# ─────────────────────────────────────────────────────────────

from __future__ import annotations
import json, random, math
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

BASE      = Path(__file__).parent.parent
DATA_DIR  = BASE / 'dharma' / 'data'
MODEL_DIR = BASE / 'dharma' / 'models'


# ════════════════════════════════════════════════════════════════
# 1. WR矩阵加载
# ════════════════════════════════════════════════════════════════

# 达摩院V7 WR矩阵（来自MEMORY.md封印数据）
# wr_matrix_v7 关键节点：
#   BEAR_TREND × SHORT × 新鲜P3(age≤2) × BTC → WR=75.6%
#   RSI>60 × BEAR_TREND × SHORT → WR=68.1%
#   BEAR_TREND × LONG → WR=45%（死穴，封禁）
BUILTIN_WR_MATRIX = {
    'BEAR_TREND:SHORT:HIGH':   0.718,   # n=2413
    'BEAR_TREND:SHORT:MID':    0.645,
    'BEAR_TREND:SHORT:LOW':    0.512,
    'BEAR_TREND:LONG:ANY':     0.450,   # 死穴
    'BEAR_RECOVERY:LONG:HIGH': 0.725,   # n=验证通过
    'BEAR_RECOVERY:LONG:MID':  0.631,
    'BEAR_RECOVERY:SHORT:ANY': 0.380,   # 封禁
    'BULL_TREND:LONG:HIGH':    0.682,
    'BULL_TREND:LONG:MID':     0.601,
    'BULL_TREND:SHORT:ANY':    0.410,
    'CHOP_MID:LONG:ANY':       0.435,
    'CHOP_MID:SHORT:ANY':      0.420,
    'BEAR_EARLY:SHORT:HIGH':   0.641,
    'BEAR_EARLY:SHORT:MID':    0.573,
    'BEAR_EARLY:LONG:ANY':     0.489,
}


def load_wr_matrix() -> dict:
    """加载WR矩阵（优先使用磁盘版，回退到内建版）"""
    for path in [
        BASE / 'data' / 'wr_matrix_v7.json',
        DATA_DIR / 'dharma_master_params.json',
        DATA_DIR / 'dharma_regime_params.json',
    ]:
        if path.exists():
            try:
                d = json.loads(path.read_text())
                # 尝试从不同结构中提取
                if 'wr_matrix' in d:
                    return d['wr_matrix']
                if 'regime_wr' in d:
                    return d['regime_wr']
            except Exception:
                pass

    # 使用内建矩阵
    return BUILTIN_WR_MATRIX


def lookup_wr(wr_matrix: dict, regime: str, direction: str, score_tier: str) -> float:
    """查询WR矩阵，支持多级回退"""
    keys_to_try = [
        f"{regime}:{direction}:{score_tier}",
        f"{regime}:{direction}:ANY",
        f"UNKNOWN:{direction}:{score_tier}",
        f"UNKNOWN:ANY:ANY",
    ]
    for k in keys_to_try:
        if k in wr_matrix:
            return wr_matrix[k]
    # 兜底：返回历史均值
    return 0.45


# ════════════════════════════════════════════════════════════════
# 2. 核心扩充器
# ════════════════════════════════════════════════════════════════

class RegimeAwareAugmentor:
    """
    体制感知合成数据扩充器

    工作原理：
    1. 将真实特征数据按 regime×direction×score_tier 分组
    2. 对稀缺bucket进行过采样（加噪声）
    3. 基于 WR矩阵 为合成样本分配统计标签
    4. 输出：合并后的训练DataFrame

    关键约束：
    - 合成样本 noise = ±5% Gaussian，保持特征分布
    - 标签基于 Bernoulli(wr) 随机采样，不固定
    - 每个bucket目标样本量 ≥ 200 条
    """

    TARGET_PER_BUCKET = 200    # 每个bucket目标样本量
    NOISE_STD         = 0.05   # 特征噪声标准差（±5%）
    MAX_SYNTH_RATIO   = 4.0    # 合成数据不超过真实数据4倍
    MIN_REAL_PER_BKT  = 3      # bucket内至少3条真实数据才扩充

    def __init__(self, random_seed: int = 42):
        self.seed       = random_seed
        self.wr_matrix  = load_wr_matrix()
        self._rng       = random.Random(random_seed)
        np.random.seed(random_seed)

    def _score_to_tier(self, score: float) -> str:
        if score >= 130:
            return 'HIGH'
        elif score >= 110:
            return 'MID'
        else:
            return 'LOW'

    def _assign_bucket(self, row: pd.Series) -> str:
        """为一条样本分配 bucket 键"""
        regime    = str(row.get('regime', 'UNKNOWN')).upper()
        direction = str(row.get('direction', 'LONG')).upper()
        score     = float(row.get('score', 100))
        tier      = self._score_to_tier(score)
        return f"{regime}:{direction}:{tier}"

    def _generate_synthetic(
        self,
        base_df: pd.DataFrame,
        target_n: int,
        regime: str,
        direction: str,
        score_tier: str,
        feature_cols: list
    ) -> pd.DataFrame:
        """
        对单个bucket生成合成样本

        Args:
            base_df:    该bucket的真实数据
            target_n:   目标合成样本数
            feature_cols: 需要加噪声的特征列
        """
        if len(base_df) < self.MIN_REAL_PER_BKT:
            return pd.DataFrame()

        need_n = target_n - len(base_df)
        if need_n <= 0:
            return pd.DataFrame()

        # 上限检查
        max_synth = int(len(base_df) * self.MAX_SYNTH_RATIO)
        need_n    = min(need_n, max_synth)

        # WR查询 → 用于标签分配
        wr = lookup_wr(self.wr_matrix, regime, direction, score_tier)

        synth_rows = []
        for _ in range(need_n):
            # 从base中随机采样一行
            src = base_df.sample(1, replace=True).iloc[0].copy()

            # 特征加噪声（连续特征）
            for col in feature_cols:
                if col in src and isinstance(src[col], (int, float)):
                    noise = np.random.normal(0, abs(src[col]) * self.NOISE_STD + 1e-6)
                    src[col] = src[col] + noise

            # 基于WR矩阵随机分配标签
            src['label']     = 1 if self._rng.random() < wr else 0
            src['synthetic'] = True
            src['src_wr']    = wr
            src['regime']    = regime
            src['direction'] = direction
            src['score_tier']= score_tier

            synth_rows.append(src)

        return pd.DataFrame(synth_rows)

    def augment(
        self,
        real_df: pd.DataFrame,
        feature_cols: Optional[list] = None,
        target_per_bucket: Optional[int] = None,
        verbose: bool = True
    ) -> pd.DataFrame:
        """
        主入口：对整个数据集进行体制感知扩充

        Args:
            real_df:   原始训练数据（需包含 regime, direction, score 列）
            feature_cols: 要加噪声的特征列（None = 自动检测数值列）
            target_per_bucket: 每个bucket目标量（None = 用默认200）
            verbose: 打印扩充统计

        Returns:
            DataFrame: 合并了真实+合成数据的训练集
        """
        target_n = target_per_bucket or self.TARGET_PER_BUCKET

        # 自动检测特征列
        if feature_cols is None:
            exclude = {'label', 'regime', 'direction', 'score', 'score_tier',
                       'sym', 'ts', 'synthetic', 'src_wr'}
            feature_cols = [
                c for c in real_df.columns
                if c not in exclude and real_df[c].dtype in [float, np.float32, np.float64,
                                                              int, np.int32, np.int64]
            ]

        # 分配bucket
        real_df = real_df.copy()
        real_df['synthetic']  = False
        real_df['score_tier'] = real_df.apply(
            lambda r: self._score_to_tier(r.get('score', 100)), axis=1
        )
        if 'bucket' not in real_df.columns:
            real_df['bucket'] = real_df.apply(self._assign_bucket, axis=1)

        # 按bucket统计
        bucket_counts = real_df.groupby('bucket').size()
        if verbose:
            print(f"📊 原始数据 {len(real_df)} 条，{len(bucket_counts)} 个bucket")
            print(f"   特征列: {len(feature_cols)} 个")

        # 逐bucket扩充
        synth_dfs = []
        total_synth = 0

        for bucket, count in bucket_counts.items():
            parts = bucket.split(':')
            if len(parts) != 3:
                continue
            regime, direction, score_tier = parts
            bucket_df = real_df[real_df['bucket'] == bucket]

            synth_df = self._generate_synthetic(
                base_df    = bucket_df,
                target_n   = target_n,
                regime     = regime,
                direction  = direction,
                score_tier = score_tier,
                feature_cols = feature_cols
            )

            if len(synth_df) > 0:
                synth_dfs.append(synth_df)
                total_synth += len(synth_df)

                if verbose:
                    wr = lookup_wr(self.wr_matrix, regime, direction, score_tier)
                    print(f"   {bucket}: real={count:>4} → synth={len(synth_df):>4} (WR={wr:.1%})")

        # 合并
        all_dfs = [real_df] + synth_dfs
        merged  = pd.concat(all_dfs, ignore_index=True)

        if verbose:
            real_n   = len(real_df)
            synth_n  = total_synth
            total_n  = len(merged)
            ratio    = synth_n / (real_n + 1e-9)
            print(f"\n✅ 扩充完成:")
            print(f"   真实数据: {real_n:>8,} 条")
            print(f"   合成数据: {synth_n:>8,} 条 (×{ratio:.1f})")
            print(f"   合计:     {total_n:>8,} 条")
            if 'label' in merged.columns:
                wr_real  = real_df['label'].mean()  if 'label' in real_df.columns  else 0
                wr_synth = pd.concat(synth_dfs)['label'].mean() if synth_dfs else 0
                print(f"   WR 真实: {wr_real:.3f}  合成: {wr_synth:.3f}")

        return merged

    def sanity_check(self, df: pd.DataFrame) -> dict:
        """
        扩充后数据质量检查（达摩院红线验证）

        Returns:
            {'pass': bool, 'issues': list}
        """
        issues = []

        # 检查1：合成比例不超过4x
        if 'synthetic' in df.columns:
            n_real  = (df['synthetic'] == False).sum()
            n_synth = (df['synthetic'] == True).sum()
            ratio   = n_synth / (n_real + 1e-9)
            if ratio > self.MAX_SYNTH_RATIO:
                issues.append(f"合成比例过高: {ratio:.1f}x > {self.MAX_SYNTH_RATIO}x")

        # 检查2：标签分布合理（0.3-0.8之间）
        if 'label' in df.columns:
            wr = df['label'].mean()
            if wr < 0.3 or wr > 0.8:
                issues.append(f"标签分布异常: WR={wr:.3f}（期望0.3-0.8）")

        # 检查3：特征无NaN
        num_cols = df.select_dtypes(include=[float, int]).columns
        nan_count = df[num_cols].isna().sum().sum()
        if nan_count > 0:
            issues.append(f"存在NaN: {nan_count}个")

        # 检查4：总样本量达标
        if len(df) < 1000:
            issues.append(f"总样本量不足: {len(df)} < 1000")

        return {
            'pass':        len(issues) == 0,
            'n_total':     len(df),
            'n_real':      int((df.get('synthetic', False) == False).sum()),
            'n_synth':     int((df.get('synthetic', True) == True).sum()),
            'label_wr':    float(df['label'].mean()) if 'label' in df.columns else None,
            'issues':      issues,
            'checked_at':  datetime.now(timezone.utc).isoformat(),
        }


# ════════════════════════════════════════════════════════════════
# 3. 快速扩充入口（接入现有xgb_train_v2_features.parquet）
# ════════════════════════════════════════════════════════════════

def augment_from_existing_data(
    target_total: int = 5000,
    output_path: Optional[Path] = None,
    verbose: bool = True
) -> pd.DataFrame:
    """
    利用现有 xgb_train_v2_features.parquet (228k行) 生成扩充训练集

    返回可直接用于XGB重训的DataFrame
    """
    # 加载最大的现有特征文件
    src_file = DATA_DIR / 'xgb_train_v2_features.parquet'
    if not src_file.exists():
        # fallback
        src_file = DATA_DIR / 'xgb_train_features.parquet'

    if not src_file.exists():
        raise FileNotFoundError(f"未找到训练数据: {src_file}")

    print(f"📂 加载: {src_file.name}")
    df = pd.read_parquet(src_file)
    print(f"   原始行数: {len(df):,}")

    # 补充必要列（如不存在）
    if 'regime' not in df.columns:
        # xgb_train_v2 使用 trend_score 列，优先使用
        if 'trend_score' in df.columns:
            df['regime'] = df['trend_score'].map(
                lambda t: 'BEAR_TREND' if t < -0.3 else
                          ('BULL_TREND' if t > 0.3 else 'CHOP_MID')
            )
        elif 'e21_55_gap' in df.columns:
            df['regime'] = df['e21_55_gap'].map(
                lambda t: 'BEAR_TREND' if t < -0.02 else
                          ('BULL_TREND' if t > 0.02 else 'CHOP_MID')
            )
        else:
            df['regime'] = 'UNKNOWN'

    if 'direction' not in df.columns:
        # 用RSI判断方向倾向
        if 'rsi14' in df.columns:
            df['direction'] = np.where(df['rsi14'] < 50, 'SHORT', 'LONG')
        else:
            df['direction'] = np.where(np.arange(len(df)) % 2 == 0, 'LONG', 'SHORT')

    if 'score' not in df.columns:
        # 用RSI偏离度估算score范围
        if 'rsi14' in df.columns:
            df['score'] = (df['rsi14'] - 50).abs() * 2 + 80  # 粗估
        else:
            df['score'] = 110.0

    if 'label' not in df.columns:
        # 使用 future_12h 作为标签基础（如有）
        if 'future_12h' in df.columns:
            df['label'] = ((df['direction'] == 'LONG') & (df['future_12h'] > 0.5) |
                           (df['direction'] == 'SHORT') & (df['future_12h'] < -0.5)).astype(int)
        else:
            df['label'] = 0

    # 计算每bucket目标量
    buckets_expected = 5 * 2 * 3  # 5体制 × 2方向 × 3分层 = 30 buckets
    target_per_bucket = max(200, target_total // buckets_expected)

    augmentor = RegimeAwareAugmentor()
    augmented_df = augmentor.augment(
        real_df           = df,
        target_per_bucket = target_per_bucket,
        verbose           = verbose
    )

    # 质量检查
    check = augmentor.sanity_check(augmented_df)
    if not check['pass']:
        print(f"⚠️  质量检查未通过: {check['issues']}")
    else:
        print(f"✅ 质量检查通过: n={check['n_total']:,} WR={check['label_wr']:.3f}")

    # 保存
    if output_path is None:
        output_path = DATA_DIR / f'xgb_augmented_{datetime.now(timezone.utc).strftime("%Y%m%d")}.parquet'

    augmented_df.to_parquet(output_path, index=False)
    print(f"💾 已保存: {output_path}")

    return augmented_df


# ════════════════════════════════════════════════════════════════
# 4. 主入口
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='达摩院合成数据扩充器')
    parser.add_argument('--target', type=int, default=5000, help='目标总样本量')
    parser.add_argument('--output', type=str, default=None, help='输出路径')
    parser.add_argument('--test', action='store_true', help='单元测试模式')
    args = parser.parse_args()

    if args.test:
        print("🧪 单元测试模式\n")
        # 生成100条mock数据测试
        mock = pd.DataFrame({
            'rsi14':     np.random.uniform(20, 80, 100),
            'rsi7':      np.random.uniform(20, 80, 100),
            'bb_pos':    np.random.uniform(0, 1, 100),
            'vol_ratio': np.random.uniform(0.5, 3, 100),
            'regime':    np.random.choice(['BEAR_TREND','BULL_TREND','CHOP_MID'], 100),
            'direction': np.random.choice(['LONG','SHORT'], 100),
            'score':     np.random.uniform(90, 150, 100),
            'label':     np.random.choice([0, 1], 100),
        })

        aug = RegimeAwareAugmentor()
        result = aug.augment(mock, target_per_bucket=50, verbose=True)
        check  = aug.sanity_check(result)
        print(f"\n质量检查: {'✅ PASS' if check['pass'] else '❌ FAIL'}")
        for issue in check.get('issues', []):
            print(f"  ⚠️  {issue}")
    else:
        output = Path(args.output) if args.output else None
        augment_from_existing_data(target_total=args.target, output_path=output)
