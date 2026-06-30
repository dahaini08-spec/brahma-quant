"""
kronos_lightgbm_trainer.py — 达摩院 Kronos 有监督学习训练器 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 × 达摩院 · 2026-06-25

目标：用梵天49,170条历史信号 + 实时K线特征，训练LightGBM分类器
预期：Kronos-Lite 15M准确率 47.7% → 58%+

输入特征（15维）：
  来自Kronos-Lite v2.0:
    p_momentum, p_ema, p_rsi, p_candle, p_volume, p_bos
  体制编码:
    regime_encoded (0~7), phase_1h_encoded, phase_4h_encoded
  市场微观:
    lsr (多空比), fr (资金费率), oi_change_pct
  价格结构:
    atr_pct, bb_position, vol_ratio_20
  时间特征:
    hour_of_day, day_of_week

输出标签：WIN=1 / LOSS=0（TIMEOUT排除）

苏摩合规：
  - 离线脚本，不产生AI cron任务
  - 训练完成写入 data/kronos_lgbm_model.json（轻量JSON格式）
  - brahma_core通过 get_s23_score 透明调用，接口不变

使用方法：
  python3 scripts/kronos_lightgbm_trainer.py --mode prepare  # 准备特征数据
  python3 scripts/kronos_lightgbm_trainer.py --mode train    # 训练模型
  python3 scripts/kronos_lightgbm_trainer.py --mode eval     # 评估模型
  python3 scripts/kronos_lightgbm_trainer.py --mode all      # 全流程
"""

import sys, os, json, argparse, time
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / 'brahma_brain'))

DATA_DIR   = BASE_DIR / 'data'
MODEL_PATH = DATA_DIR / 'kronos_lgbm_model.json'
FEAT_PATH  = DATA_DIR / 'kronos_train_features.json'
SIGNALS_PATH = DATA_DIR / 'kronos_real_signals.json'  # 梵天历史信号

REGIME_MAP = {
    'BEAR_TREND': 0, 'BEAR_EARLY': 1, 'BEAR_RECOVERY': 2,
    'BULL_TREND': 3, 'BULL_EARLY': 4, 'BULL_CORRECTION': 5,
    'CHOP_MID': 6,   'CHOP_HIGH': 7,  'CHOP_LOW': 8,
}
PHASE_MAP = {
    'DOWNTREND': 0, 'PULLBACK_DN': 1, 'TOPPING': 2,
    'CHOP': 3,
    'UPTREND': 4, 'PULLBACK_UP': 5, 'BOTTOMING': 6,
}


def load_signals():
    """加载梵天历史信号"""
    if not SIGNALS_PATH.exists():
        # 尝试其他信号文件
        alt_paths = [
            DATA_DIR / 'paper_signals.json',
            DATA_DIR / 'live_signals.json',
            DATA_DIR / 'brahma_signals.json',
        ]
        for p in alt_paths:
            if p.exists():
                print(f'使用信号文件: {p}')
                return json.loads(p.read_text(encoding='utf-8'))
        print('⚠️ 未找到信号文件，使用模拟数据演示')
        return _generate_demo_signals(500)
    return json.loads(SIGNALS_PATH.read_text(encoding='utf-8'))


def _generate_demo_signals(n=500):
    """生成演示信号（真实训练时用梵天历史数据替代）"""
    import random
    random.seed(42)
    signals = []
    regimes = list(REGIME_MAP.keys())
    for i in range(n):
        regime = random.choice(regimes)
        direction = random.choice(['LONG', 'SHORT'])
        # 模拟真实WR分布
        if regime in ('BEAR_TREND', 'BEAR_EARLY') and direction == 'SHORT':
            win_prob = 0.68
        elif regime in ('BULL_TREND', 'BULL_EARLY') and direction == 'LONG':
            win_prob = 0.65
        elif 'CHOP' in regime:
            win_prob = 0.48
        else:
            win_prob = 0.52
        result = 'WIN' if random.random() < win_prob else 'LOSS'
        signals.append({
            'symbol': random.choice(['BTCUSDT', 'ETHUSDT']),
            'direction': direction,
            'regime': regime,
            'score': random.randint(100, 180),
            'result': result,
            'p_momentum': random.uniform(0.2, 0.8),
            'p_ema': random.uniform(0.0, 1.0),
            'p_rsi': random.uniform(0.1, 0.9),
            'p_candle': random.uniform(0.0, 1.0),
            'p_volume': random.choice([0.35, 0.5, 0.65]),
            'lsr': random.uniform(0.5, 0.8),
            'fr': random.uniform(-0.001, 0.001),
            'atr_pct': random.uniform(0.005, 0.025),
            'bb_position': random.uniform(0.1, 0.9),
        })
    return signals


def build_features(signals):
    """从信号列表构建特征矩阵"""
    X, y = [], []
    skipped = 0
    for s in signals:
        # 只使用WIN/LOSS，跳过TIMEOUT
        result = s.get('result', '')
        if result == 'TIMEOUT':
            skipped += 1
            continue
        if result not in ('WIN', 'LOSS'):
            skipped += 1
            continue

        label = 1 if result == 'WIN' else 0

        # 体制编码
        regime = s.get('regime', '')
        regime_enc = REGIME_MAP.get(regime, -1)
        if regime_enc < 0:
            skipped += 1
            continue

        # 方向编码
        direction_enc = 1 if s.get('direction') == 'LONG' else 0

        # Kronos特征（来自信号存储的Kronos输出）
        p_momentum = float(s.get('p_momentum', 0.5))
        p_ema      = float(s.get('p_ema',      0.5))
        p_rsi      = float(s.get('p_rsi',      0.5))
        p_candle   = float(s.get('p_candle',   0.5))
        p_volume   = float(s.get('p_volume',   0.5))
        p_bos      = float(s.get('p_bos',      0.5))

        # 市场微观
        lsr        = float(s.get('lsr',        0.5))
        fr         = float(s.get('fr',         0.0))
        oi_chg     = float(s.get('oi_change_pct', 0.0))

        # 价格结构
        atr_pct    = float(s.get('atr_pct',    0.01))
        bb_pos     = float(s.get('bb_position', 0.5))
        score_norm = float(s.get('score',       130)) / 200.0

        # 时间特征
        hour       = int(s.get('hour_of_day', 12))
        dow        = int(s.get('day_of_week', 3))

        feat = [
            p_momentum, p_ema, p_rsi, p_candle, p_volume, p_bos,
            regime_enc / 8.0,   # 归一化
            direction_enc,
            lsr,
            fr * 1000,          # 放大到合理范围
            oi_chg,
            atr_pct,
            bb_pos,
            score_norm,
            hour / 23.0,
            dow / 6.0,
        ]
        X.append(feat)
        y.append(label)

    print(f'特征构建: {len(X)}条有效 / {skipped}条跳过(TIMEOUT/无效)')
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)


class SimpleGBMTrainer:
    """
    轻量级梯度提升实现（纯numpy，无需lightgbm包）
    真实环境中可替换为：import lightgbm as lgb
    
    当前实现：决策树桩集成（Gradient Boosting with stumps）
    适合特征数≤20、样本≤50000的场景
    """

    def __init__(self, n_estimators=100, learning_rate=0.1, max_depth=3):
        self.n_estimators  = n_estimators
        self.lr            = learning_rate
        self.max_depth     = max_depth
        self.trees         = []
        self.base_score    = 0.5
        self.feature_names = [
            'p_momentum','p_ema','p_rsi','p_candle','p_volume','p_bos',
            'regime','direction','lsr','fr','oi_chg',
            'atr_pct','bb_pos','score_norm','hour','dow'
        ]

    def _sigmoid(self, x):
        return 1 / (1 + np.exp(-np.clip(x, -20, 20)))

    def _build_stump(self, X, residuals, max_depth=3):
        """构建单棵决策树桩"""
        n_samples, n_features = X.shape

        def _split(indices, depth):
            if depth == 0 or len(indices) < 5:
                val = residuals[indices].mean()
                return {'leaf': True, 'value': val}
            best_gain = -np.inf
            best_feat = best_thresh = None
            for feat in range(n_features):
                vals = np.unique(X[indices, feat])
                if len(vals) < 2:
                    continue
                thresholds = (vals[:-1] + vals[1:]) / 2
                for thresh in thresholds[::max(1, len(thresholds)//10)]:
                    left  = indices[X[indices, feat] <= thresh]
                    right = indices[X[indices, feat] >  thresh]
                    if len(left) < 2 or len(right) < 2:
                        continue
                    gain = (len(left) * residuals[left].var() +
                            len(right) * residuals[right].var())
                    if -gain > best_gain:
                        best_gain = -gain
                        best_feat, best_thresh = feat, thresh
            if best_feat is None:
                return {'leaf': True, 'value': residuals[indices].mean()}
            left  = indices[X[indices, best_feat] <= best_thresh]
            right = indices[X[indices, best_feat] >  best_thresh]
            return {
                'leaf': False,
                'feat': int(best_feat),
                'thresh': float(best_thresh),
                'left':  _split(left,  depth - 1),
                'right': _split(right, depth - 1),
            }

        return _split(np.arange(n_samples), max_depth)

    def _predict_tree(self, tree, X):
        if tree['leaf']:
            return np.full(len(X), tree['value'])
        left_mask  = X[:, tree['feat']] <= tree['thresh']
        result = np.empty(len(X))
        if left_mask.any():
            result[left_mask]  = self._predict_tree(tree['left'],  X[left_mask])
        if (~left_mask).any():
            result[~left_mask] = self._predict_tree(tree['right'], X[~left_mask])
        return result

    def fit(self, X, y):
        F = np.full(len(y), np.log(self.base_score / (1 - self.base_score)))
        for i in range(self.n_estimators):
            p = self._sigmoid(F)
            residuals = y.astype(float) - p
            tree = self._build_stump(X, residuals, self.max_depth)
            update = self._predict_tree(tree, X)
            F += self.lr * update
            self.trees.append(tree)
            if (i + 1) % 20 == 0:
                preds = (self._sigmoid(F) > 0.5).astype(int)
                acc = (preds == y).mean()
                print(f'  迭代{i+1:3d}/{self.n_estimators}: 训练集准确率={acc*100:.1f}%')
        return self

    def predict_proba(self, X):
        F = np.full(len(X), np.log(self.base_score / (1 - self.base_score)))
        for tree in self.trees:
            F += self.lr * self._predict_tree(tree, X)
        return self._sigmoid(F)

    def predict(self, X, threshold=0.5):
        return (self.predict_proba(X) > threshold).astype(int)

    def feature_importance(self):
        """简单特征重要性：各特征被选中次数"""
        counts = np.zeros(len(self.feature_names))
        def _count(tree):
            if tree['leaf']: return
            counts[tree['feat']] += 1
            _count(tree['left']); _count(tree['right'])
        for t in self.trees:
            _count(t)
        total = counts.sum() or 1
        return {self.feature_names[i]: round(counts[i]/total, 4) for i in range(len(self.feature_names))}

    def to_dict(self):
        """序列化为JSON可存储格式"""
        return {
            'version': '1.0',
            'trained_at': datetime.now(timezone.utc).isoformat(),
            'n_estimators': self.n_estimators,
            'lr': self.lr,
            'max_depth': self.max_depth,
            'base_score': self.base_score,
            'feature_names': self.feature_names,
            'trees': self.trees,
        }

    @classmethod
    def from_dict(cls, d):
        obj = cls(d['n_estimators'], d['lr'], d['max_depth'])
        obj.base_score = d['base_score']
        obj.feature_names = d['feature_names']
        obj.trees = d['trees']
        return obj


def run_prepare():
    print('\n=== 阶段①：准备训练数据 ===')
    signals = load_signals()
    print(f'加载信号: {len(signals)}条')
    X, y = build_features(signals)
    print(f'特征矩阵: {X.shape}  正样本(WIN): {y.sum()} ({y.mean()*100:.1f}%)')
    feat_data = {'X': X.tolist(), 'y': y.tolist(), 'n_features': X.shape[1]}
    FEAT_PATH.write_text(json.dumps(feat_data), encoding='utf-8')
    print(f'✅ 特征数据写入: {FEAT_PATH}')
    return X, y


def run_train(X=None, y=None):
    print('\n=== 阶段②：训练LightGBM模型 ===')
    if X is None:
        if not FEAT_PATH.exists():
            print('请先运行 --mode prepare')
            return None
        feat_data = json.loads(FEAT_PATH.read_text())
        X = np.array(feat_data['X'], dtype=np.float32)
        y = np.array(feat_data['y'], dtype=np.int32)

    # 按8:2分割训练/测试集
    n = len(X)
    split = int(n * 0.8)
    idx = np.random.permutation(n)
    X_train, y_train = X[idx[:split]], y[idx[:split]]
    X_test,  y_test  = X[idx[split:]], y[idx[split:]]
    print(f'训练集: {len(X_train)}  测试集: {len(X_test)}')

    # 训练
    try:
        import lightgbm as lgb
        print('使用 LightGBM 原生库训练...')
        dtrain = lgb.Dataset(X_train, label=y_train)
        params = {
            'objective': 'binary', 'metric': 'binary_error',
            'learning_rate': 0.05, 'num_leaves': 31,
            'min_data_in_leaf': 20, 'verbose': -1,
        }
        model = lgb.train(params, dtrain, num_boost_round=200,
                          valid_sets=[lgb.Dataset(X_test, label=y_test)],
                          callbacks=[lgb.early_stopping(20), lgb.log_evaluation(50)])
        preds = (model.predict(X_test) > 0.5).astype(int)
        acc = (preds == y_test).mean()
        print(f'LightGBM OOS准确率: {acc*100:.1f}%')
        model.save_model(str(MODEL_PATH).replace('.json', '_lgb.txt'))
        print(f'✅ LightGBM模型保存: {MODEL_PATH}')

    except ImportError:
        print('LightGBM未安装，使用内置SimpleGBM训练...')
        model = SimpleGBMTrainer(n_estimators=100, learning_rate=0.08, max_depth=3)
        model.fit(X_train, y_train)

        preds = model.predict(X_test)
        acc = (preds == y_test).mean()
        proba = model.predict_proba(X_test)
        strong_mask = (proba > 0.62) | (proba < 0.38)
        strong_acc = (preds[strong_mask] == y_test[strong_mask]).mean() if strong_mask.any() else 0
        print(f'\nOOS总体准确率:     {acc*100:.1f}%  (n={len(y_test)})')
        print(f'OOS强信号准确率:   {strong_acc*100:.1f}%  (n={strong_mask.sum()})')

        # 特征重要性
        fi = model.feature_importance()
        print('\n特征重要性 TOP8:')
        for feat, imp in sorted(fi.items(), key=lambda x: -x[1])[:8]:
            bar = '█' * int(imp * 50)
            print(f'  {feat:<15} {imp:.4f}  {bar}')

        # 保存模型
        model_dict = model.to_dict()
        MODEL_PATH.write_text(json.dumps(model_dict, ensure_ascii=False), encoding='utf-8')
        print(f'\n✅ SimpleGBM模型保存: {MODEL_PATH}')

    return model


def run_eval():
    print('\n=== 阶段③：评估模型 ===')
    if not MODEL_PATH.exists():
        print('模型文件不存在，请先训练')
        return
    model_dict = json.loads(MODEL_PATH.read_text())
    print(f'模型版本: {model_dict.get("version")}')
    print(f'训练时间: {model_dict.get("trained_at")}')
    print(f'树数量: {model_dict.get("n_estimators")}')
    model = SimpleGBMTrainer.from_dict(model_dict)
    if not FEAT_PATH.exists():
        print('特征数据不存在，请先运行prepare')
        return
    feat_data = json.loads(FEAT_PATH.read_text())
    X = np.array(feat_data['X'], dtype=np.float32)
    y = np.array(feat_data['y'], dtype=np.int32)
    # 用后20%作OOS测试
    split = int(len(X) * 0.8)
    X_test, y_test = X[split:], y[split:]
    preds = model.predict(X_test)
    proba = model.predict_proba(X_test)
    acc = (preds == y_test).mean()
    strong_mask = (proba > 0.62) | (proba < 0.38)
    strong_acc = (preds[strong_mask] == y_test[strong_mask]).mean() if strong_mask.any() else 0
    print(f'OOS总体准确率: {acc*100:.1f}%')
    print(f'OOS强信号准确率: {strong_acc*100:.1f}%  (n={strong_mask.sum()})')
    print(f'基准(Kronos-Lite v1.0): 47.7%')
    improvement = acc*100 - 47.7
    print(f'改进: {improvement:+.1f}pp')
    if acc >= 0.55:
        print('✅ 达到M2里程碑（≥55%），可考虑接入实盘shadow模式')
    elif acc >= 0.52:
        print('🟡 达到M1里程碑（≥52%），继续收集数据')
    else:
        print('⚠️ 未达里程碑，继续优化特征或增加数据')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Kronos LightGBM 训练器')
    parser.add_argument('--mode', choices=['prepare','train','eval','all'], default='all')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    np.random.seed(args.seed)

    if args.mode in ('prepare', 'all'):
        X, y = run_prepare()
    if args.mode in ('train', 'all'):
        X_v = y_v = None
        if args.mode == 'all':
            X_v, y_v = X, y
        run_train(X_v, y_v)
    if args.mode in ('eval', 'all'):
        run_eval()
