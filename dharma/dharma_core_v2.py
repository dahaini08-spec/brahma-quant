#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║         梵天子系统 · 达摩院 Dharma Research Lab v2.0              ║
║         万次测试框架 · 专业级统计引擎                               ║
║                                                                  ║
║  升级内容 v2.0：                                                   ║
║    · Bootstrap 默认 10,000 次重采样（原1000次）                     ║
║    · Monte Carlo 置换检验（p值 + 零假设）                           ║
║    · Walk-Forward 分段样本外验证                                    ║
║    · UniversalScorer 通用评分（胜率+PF+稳定性+样本量综合）           ║
║    · 多周期支持（1H/4H/1D节点分离）                                 ║
║    · 跨币种泛化系数                                                 ║
║    · 结果可信度分级（S/A/B/C/D）                                    ║
║                                                                  ║
║  设计原则：Zero-API · 纯本地 · 可重现 · 可扩展                     ║
╚══════════════════════════════════════════════════════════════════╝
"""

import json, os, math, random, statistics, time, hashlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────
#  路径配置
# ─────────────────────────────────────────────────────────────────
DHARMA_DIR  = os.path.dirname(os.path.abspath(__file__))
SOUL_DIR    = os.path.join(DHARMA_DIR, '..', 'lana', 'soul_db')
RESULTS_DIR = os.path.join(DHARMA_DIR, 'results')
os.makedirs(RESULTS_DIR, exist_ok=True)

SYMBOLS_20 = [
    "btcusdt","ethusdt","bnbusdt","solusdt","xrpusdt","adausdt",
    "dogeusdt","avaxusdt","dotusdt","linkusdt","ltcusdt","atomusdt",
    "nearusdt","injusdt","aptusdt","arbusdt","opusdt","suiusdt",
    "maticusdt","tiausdt",
]

COIN_META = {
    "btcusdt":  {"type":"trend",   "tier":0, "name":"BTC"},
    "ethusdt":  {"type":"trend",   "tier":0, "name":"ETH"},
    "adausdt":  {"type":"trend",   "tier":0, "name":"ADA"},
    "linkusdt": {"type":"trend",   "tier":0, "name":"LINK"},
    "dogeusdt": {"type":"revert",  "tier":1, "name":"DOGE"},
    "atomusdt": {"type":"revert",  "tier":1, "name":"ATOM"},
    "tiausdt":  {"type":"revert",  "tier":1, "name":"TIA"},
    "ltcusdt":  {"type":"revert",  "tier":1, "name":"LTC"},
    "dotusdt":  {"type":"revert",  "tier":1, "name":"DOT"},
    "injusdt":  {"type":"revert",  "tier":1, "name":"INJ"},
    "solusdt":  {"type":"hybrid",  "tier":2, "name":"SOL"},
    "bnbusdt":  {"type":"hybrid",  "tier":2, "name":"BNB"},
    "xrpusdt":  {"type":"hybrid",  "tier":2, "name":"XRP"},
    "suiusdt":  {"type":"hybrid",  "tier":2, "name":"SUI"},
    "arbusdt":  {"type":"hybrid",  "tier":2, "name":"ARB"},
    "opusdt":   {"type":"hybrid",  "tier":2, "name":"OP"},
    "nearusdt": {"type":"hybrid",  "tier":2, "name":"NEAR"},
    "aptusdt":  {"type":"hybrid",  "tier":2, "name":"APT"},
    "avaxusdt": {"type":"hybrid",  "tier":2, "name":"AVAX"},
    "maticusdt":{"type":"hybrid",  "tier":2, "name":"MATIC"},
}

YEAR_REGIME = {
    '2018': 'BEAR_TREND',
    '2019': 'BEAR_TRANSITION',
    '2020': 'BULL_EARLY',
    '2021': 'BULL_PEAK',
    '2022': 'BEAR_CRASH',
    '2023': 'RECOVERY',
    '2024': 'BULL_ETF',
    '2025': 'CHOP_HIGH',
    '2026': 'CHOP_HIGH',
}
REGIME_LABEL = {
    'BEAR_TREND':    '🐻熊市趋势',
    'BEAR_TRANSITION':'🔄熊转震荡',
    'BULL_EARLY':    '🐂牛市启动',
    'BULL_PEAK':     '🐂牛市巅峰',
    'BEAR_CRASH':    '💥FTX崩塌',
    'RECOVERY':      '🔄底部复苏',
    'BULL_ETF':      '🐂ETF入场',
    'CHOP_HIGH':     '🔀高位震荡',
}

# 结果可信度分级标准
CONFIDENCE_GRADE = {
    'S': {'ci_min': 0.44, 'n_min': 200,  'p_max': 0.01,  'label': '⭐S级 核心规则', 'color': 'GOLD'},
    'A': {'ci_min': 0.40, 'n_min': 100,  'p_max': 0.05,  'label': '🟢A级 可信有效', 'color': 'GREEN'},
    'B': {'ci_min': 0.36, 'n_min': 80,   'p_max': 0.10,  'label': '✅B级 参考有效', 'color': 'CYAN'},
    'C': {'ci_min': 0.32, 'n_min': 50,   'p_max': 0.20,  'label': '🟡C级 弱信号',  'color': 'YELLOW'},
    'D': {'ci_min': 0.0,  'n_min': 0,    'p_max': 1.0,   'label': '🔴D级 无效',    'color': 'RED'},
}


# ─────────────────────────────────────────────────────────────────
#  NodeDB v2 — 全量节点 + 多周期标注
# ─────────────────────────────────────────────────────────────────

class NodeDB:
    """全量节点数据库 · 单例 · 懒加载 · v2.0"""
    _instance = None
    _nodes: List[Dict] = []
    _loaded = False

    @classmethod
    def get(cls) -> 'NodeDB':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def load(self, symbols=None, verbose=True):
        if self._loaded: return self
        syms = symbols or SYMBOLS_20
        t0 = time.time()
        for sym in syms:
            path = os.path.join(SOUL_DIR, f'节点_{sym}.jsonl')
            if not os.path.exists(path): continue
            with open(path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        n = json.loads(line)
                        n['_sym'] = sym
                        n['_meta'] = COIN_META.get(sym, {})
                        yr = n.get('时间', '')[:4]
                        mo = n.get('时间', '')[5:7]
                        n['_year'] = yr
                        n['_month'] = f"{yr}-{mo}"
                        n['_regime'] = YEAR_REGIME.get(yr, 'UNKNOWN')
                        # 综合评分归一化
                        n['_score'] = n.get('特征', {}).get('综合评分', 0)
                        # 入场时间特征
                        n['_hour'] = n.get('特征', {}).get('UTC时', -1)
                        n['_weekday'] = n.get('特征', {}).get('星期', -1)
                        self._nodes.append(n)
                    except: pass
        self._loaded = True
        if verbose:
            print(f"  📦 NodeDB v2: {len(self._nodes):,} 节点 · {len(syms)} 币种 · 耗时{time.time()-t0:.1f}s")
        return self

    @property
    def nodes(self) -> List[Dict]:
        return self._nodes

    def filter(self, **kwargs) -> List[Dict]:
        result = self._nodes
        if 'sym' in kwargs:
            syms = [kwargs['sym']] if isinstance(kwargs['sym'], str) else kwargs['sym']
            result = [n for n in result if n['_sym'] in syms]
        if 'year' in kwargs:
            yrs = [str(kwargs['year'])] if not isinstance(kwargs['year'], list) else [str(y) for y in kwargs['year']]
            result = [n for n in result if n['_year'] in yrs]
        if 'regime' in kwargs:
            regs = [kwargs['regime']] if isinstance(kwargs['regime'], str) else kwargs['regime']
            result = [n for n in result if n['_regime'] in regs]
        if 'direction' in kwargs:
            d = kwargs['direction']
            result = [n for n in result if d in n.get('方向', '')]
        if 'coin_type' in kwargs:
            t = kwargs['coin_type']
            result = [n for n in result if n['_meta'].get('type') == t]
        if 'year_from' in kwargs:
            result = [n for n in result if n['_year'] >= str(kwargs['year_from'])]
        if 'score_min' in kwargs:
            result = [n for n in result if n.get('_score', 0) >= kwargs['score_min']]
        if 'score_max' in kwargs:
            result = [n for n in result if n.get('_score', 0) <= kwargs['score_max']]
        return result

    def split_temporal(self, train_ratio: float = 0.7) -> Tuple[List[Dict], List[Dict]]:
        """按时间顺序分割 IS(样本内) / OOS(样本外)"""
        sorted_nodes = sorted(self._nodes, key=lambda n: n.get('时间', ''))
        cut = int(len(sorted_nodes) * train_ratio)
        return sorted_nodes[:cut], sorted_nodes[cut:]

    def walk_forward_splits(self, n_folds: int = 5) -> List[Tuple[List[Dict], List[Dict]]]:
        """Walk-Forward 分段：返回 (train, test) 对列表"""
        sorted_nodes = sorted(self._nodes, key=lambda n: n.get('时间', ''))
        fold_size = len(sorted_nodes) // (n_folds + 1)
        splits = []
        for i in range(1, n_folds + 1):
            train = sorted_nodes[:i * fold_size]
            test  = sorted_nodes[i * fold_size: (i + 1) * fold_size]
            if len(test) > 0:
                splits.append((train, test))
        return splits


# ─────────────────────────────────────────────────────────────────
#  FeatureKit v2 — 特征工程
# ─────────────────────────────────────────────────────────────────

class FeatureKit:
    @staticmethod
    def get(node: Dict, key: str) -> Optional[float]:
        v = node.get('特征', {}).get(key)
        return float(v) if v is not None else None

    @staticmethod
    def get_result(node: Dict, cfg: str = '紧止损') -> str:
        j = node.get('结局', {}).get(cfg, {})
        return j.get('结局', '?') if j else '?'

    @staticmethod
    def get_pnl(node: Dict, horizon: str = '24根后收益%', cfg: str = '标准') -> Optional[float]:
        j = node.get('结局', {}).get(cfg, {})
        v = j.get(horizon) if j else None
        return float(v) if v is not None else None

    @staticmethod
    def get_win(node: Dict, cfg: str = '紧止损') -> Optional[int]:
        res = FeatureKit.get_result(node, cfg)
        if res == '目标1': return 1
        if res == '止损': return 0
        return None

    @staticmethod
    def get_win_multi(node: Dict) -> Dict[str, Optional[int]]:
        """同时返回三种止损配置的胜负"""
        return {
            '紧止损': FeatureKit.get_win(node, '紧止损'),
            '标准':   FeatureKit.get_win(node, '标准'),
            '宽止损': FeatureKit.get_win(node, '宽止损'),
        }

    @staticmethod
    def build_atr_rank(nodes: List[Dict]) -> Dict[str, List[float]]:
        atr_by_sym = defaultdict(list)
        for n in nodes:
            a = FeatureKit.get(n, 'ATR百分比')
            if a: atr_by_sym[n['_sym']].append(a)
        return {sym: sorted(vals) for sym, vals in atr_by_sym.items()}

    @staticmethod
    def atr_rank(node: Dict, atr_index: Dict) -> float:
        atr = FeatureKit.get(node, 'ATR百分比')
        if atr is None: return 0.5
        sym_vals = atr_index.get(node['_sym'], [])
        if not sym_vals: return 0.5
        return sum(1 for x in sym_vals if x <= atr) / len(sym_vals)

    @staticmethod
    def extract_vector(node: Dict, atr_index: Dict) -> Optional[Dict]:
        r1   = FeatureKit.get(node, 'RSI_1H')
        r4   = FeatureKit.get(node, 'RSI_4H')
        bb   = FeatureKit.get(node, 'BB位置')
        bbw  = FeatureKit.get(node, 'BB宽度')
        atr  = FeatureKit.get(node, 'ATR百分比')
        e20  = FeatureKit.get(node, 'EMA20偏离%') or 0.0
        macd = FeatureKit.get(node, 'MACD柱') or 0.0
        vol  = FeatureKit.get(node, '量比') or 1.0
        score= node.get('_score', 0)
        if any(x is None for x in [r1, r4, bb, atr]): return None
        ar = FeatureKit.atr_rank(node, atr_index)
        return {
            'rsi1h': r1, 'rsi4h': r4,
            'bb': bb, 'bb_width': bbw or 0.0,
            'atr_pct': atr, 'atr_rank': ar,
            'ema20_dev': e20, 'macd': macd,
            'volume_ratio': vol,
            'score': score,
            'hour': node.get('_hour', -1),
            'weekday': node.get('_weekday', -1),
            'dir': node.get('方向', ''),
            'sym': node['_sym'],
            'regime': node['_regime'],
            'year': node['_year'],
            'month': node.get('_month', ''),
            'coin_type': node['_meta'].get('type', ''),
        }

    @staticmethod
    def information_gain(labels: List[int], splits: Dict[str, List[int]]) -> float:
        n = len(labels)
        if n == 0: return 0.0
        p = sum(labels) / n
        def H(p): return -p*math.log2(p)-(1-p)*math.log2(1-p) if 0<p<1 else 0.0
        base_h = H(p)
        weighted = sum((len(v)/n) * H(sum(v)/len(v)) for v in splits.values() if v)
        return base_h - weighted

    @staticmethod
    def generalization_score(results_by_sym: Dict[str, Dict]) -> float:
        """
        跨币种泛化系数 [0~1]
        = 有效币种数 / 总测试币种数  × 一致性系数
        一致性系数 = 1 - std(各币胜率) / mean(各币胜率)
        """
        valid = [(sym, d) for sym, d in results_by_sym.items()
                 if d.get('n', 0) >= 30 and d.get('ci_low', 0) > 0.32]
        total = len([s for s, d in results_by_sym.items() if d.get('n', 0) >= 30])
        if total == 0: return 0.0
        coverage = len(valid) / total
        if len(valid) < 2: return coverage * 0.5
        wrs = [d['mean'] for _, d in valid]
        m = statistics.mean(wrs)
        s = statistics.stdev(wrs)
        consistency = max(0.0, 1.0 - s / m) if m > 0 else 0.0
        return round(coverage * consistency, 4)


# ─────────────────────────────────────────────────────────────────
#  StatEngine v2 — 专业统计引擎
# ─────────────────────────────────────────────────────────────────

class StatEngine:
    """
    v2.0 统计引擎
    · Bootstrap 默认 10,000 次
    · Monte Carlo 置换检验
    · Walk-Forward 稳定性分析
    · UniversalScorer 综合评分
    """

    # ── Bootstrap ──────────────────────────────────────────────

    @staticmethod
    def bootstrap_wr(samples: List[int], n_iter: int = 10000,
                     frac: float = 0.7, seed: int = 42) -> Dict:
        """
        Bootstrap胜率估计 · v2.0 默认10,000次
        返回：{mean, ci_low, ci_high, std, n, grade, p_value}
        """
        random.seed(seed)
        n = len(samples)
        if n == 0: return {'mean':0,'ci_low':0,'ci_high':0,'std':0,'n':0,'grade':'D','p_value':1.0}
        k = max(10, int(n * frac))
        boot = sorted(sum(random.choices(samples, k=k))/k for _ in range(n_iter))
        mean = statistics.mean(boot)
        std  = statistics.stdev(boot) if len(boot) > 1 else 0
        ci_l = boot[int(n_iter*0.025)]
        ci_h = boot[int(n_iter*0.975)]
        # p值：在null=0.5下，观察到此胜率的概率
        p_val = sum(1 for b in boot if b >= 0.5) / n_iter
        p_val = min(p_val, 1 - p_val) * 2  # two-tailed
        grade = StatEngine._grade_v2(ci_l, n, p_val)
        return {
            'mean': round(mean, 4),
            'ci_low': round(ci_l, 4),
            'ci_high': round(ci_h, 4),
            'std': round(std, 4),
            'n': n,
            'grade': grade,
            'p_value': round(p_val, 4),
        }

    @staticmethod
    def bootstrap_mean(values: List[float], n_iter: int = 10000,
                       frac: float = 0.7, seed: int = 42) -> Dict:
        """Bootstrap均值估计（PnL等连续变量）"""
        random.seed(seed)
        n = len(values)
        if n == 0: return {'mean':0,'ci_low':0,'ci_high':0,'std':0,'n':0}
        k = max(10, int(n * frac))
        boot = sorted(statistics.mean(random.choices(values, k=k)) for _ in range(n_iter))
        return {
            'mean': round(statistics.mean(boot), 4),
            'ci_low': round(boot[int(n_iter*0.025)], 4),
            'ci_high': round(boot[int(n_iter*0.975)], 4),
            'std': round(statistics.stdev(boot) if len(boot) > 1 else 0, 4),
            'n': n,
        }

    # ── Monte Carlo 置换检验 ────────────────────────────────────

    @staticmethod
    def permutation_test(samples: List[int], n_iter: int = 10000,
                         seed: int = 42) -> Dict:
        """
        置换检验：原假设 H0: 胜率 = 随机水平(50%)
        返回 p_value（越小越显著）
        """
        random.seed(seed)
        n = len(samples)
        if n < 20:
            return {'p_value': 1.0, 'observed_wr': 0, 'significant': False}
        observed_wr = sum(samples) / n
        # 生成零分布：随机打乱标签
        null_dist = []
        for _ in range(n_iter):
            shuffled = random.choices([0, 1], k=n)
            null_dist.append(sum(shuffled) / n)
        # 双侧p值
        extreme = sum(1 for x in null_dist if abs(x - 0.5) >= abs(observed_wr - 0.5))
        p_val = extreme / n_iter
        return {
            'p_value': round(p_val, 5),
            'observed_wr': round(observed_wr, 4),
            'null_mean': round(statistics.mean(null_dist), 4),
            'null_std': round(statistics.stdev(null_dist), 5),
            'significant': p_val < 0.05,
            'significance_level': '***' if p_val < 0.001 else '**' if p_val < 0.01 else '*' if p_val < 0.05 else 'ns',
        }

    # ── Walk-Forward 稳定性 ─────────────────────────────────────

    @staticmethod
    def walk_forward_stability(fold_results: List[Dict]) -> Dict:
        """
        分析Walk-Forward各折结果的稳定性
        fold_results: [{'wr': 0.38, 'n': 200, 'pf': 1.5}, ...]
        """
        wrs = [f['wr'] for f in fold_results if f.get('n', 0) >= 30]
        if len(wrs) < 2:
            return {'stable': False, 'reason': '样本折数不足'}
        mean_wr = statistics.mean(wrs)
        std_wr  = statistics.stdev(wrs)
        cv = std_wr / mean_wr if mean_wr > 0 else 1.0  # 变异系数
        # 趋势检测：胜率是否在时间上稳定/上升/下降
        trend = 'stable'
        if len(wrs) >= 3:
            first_half = statistics.mean(wrs[:len(wrs)//2])
            second_half = statistics.mean(wrs[len(wrs)//2:])
            if second_half - first_half > 0.03: trend = 'improving'
            elif first_half - second_half > 0.03: trend = 'decaying'
        # 一致性：所有折胜率 > 基准线
        all_positive = all(w > 0.33 for w in wrs)
        stability_score = max(0.0, 1.0 - cv * 2) * (1.0 if all_positive else 0.7)
        return {
            'mean_wr': round(mean_wr, 4),
            'std_wr': round(std_wr, 4),
            'cv': round(cv, 4),
            'trend': trend,
            'all_positive': all_positive,
            'stability_score': round(stability_score, 4),
            'stable': cv < 0.15 and all_positive,
            'fold_wrs': [round(w, 4) for w in wrs],
        }

    # ── 利润因子 & Kelly ────────────────────────────────────────

    @staticmethod
    def profit_factor(wins: List[float], losses: List[float]) -> float:
        total_win  = sum(w for w in wins if w > 0)
        total_loss = abs(sum(l for l in losses if l < 0))
        return round(total_win / total_loss, 4) if total_loss > 0 else 999.0

    @staticmethod
    def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
        if avg_loss <= 0 or win_rate <= 0: return 0.0
        rr = abs(avg_win / avg_loss)
        kelly = win_rate - (1 - win_rate) / rr
        return round(max(0.0, kelly / 4), 4)  # Quarter Kelly

    @staticmethod
    def sharpe_like(pnl_list: List[float]) -> float:
        """类Sharpe比（PnL序列稳定性）"""
        if len(pnl_list) < 5: return 0.0
        m = statistics.mean(pnl_list)
        s = statistics.stdev(pnl_list)
        return round(m / s * math.sqrt(len(pnl_list)), 4) if s > 0 else 0.0

    # ── UniversalScorer 综合评分 ────────────────────────────────

    @staticmethod
    def universal_score(bootstrap_result: Dict,
                        perm_result: Optional[Dict] = None,
                        wf_result: Optional[Dict] = None,
                        generalization: float = 0.5) -> Dict:
        """
        通用综合评分 [0~100]
        组成：
          · 胜率分  (0~40)  = CI下界转换
          · 样本分  (0~20)  = 样本量对数得分
          · 显著性分(0~20)  = p值得分
          · 稳定性分(0~10)  = WF稳定系数
          · 泛化分  (0~10)  = 跨币种泛化系数
        """
        ci_low = bootstrap_result.get('ci_low', 0)
        n      = bootstrap_result.get('n', 0)

        # 胜率分（CI下界从30%~50%线性映射到0~40分）
        wr_score = max(0.0, min(40.0, (ci_low - 0.30) / 0.20 * 40))

        # 样本分（log1p缩放，300样本≈满分）
        n_score = min(20.0, math.log1p(n) / math.log1p(300) * 20)

        # 显著性分
        if perm_result:
            p = perm_result.get('p_value', 1.0)
            if p < 0.001:   sig_score = 20.0
            elif p < 0.01:  sig_score = 15.0
            elif p < 0.05:  sig_score = 10.0
            elif p < 0.10:  sig_score = 5.0
            else:           sig_score = 0.0
        else:
            # 无置换检验时用bootstrap p值估算
            p_boot = bootstrap_result.get('p_value', 1.0)
            sig_score = max(0.0, (1.0 - p_boot * 10) * 20)

        # 稳定性分
        stab_score = (wf_result.get('stability_score', 0.5) * 10) if wf_result else 5.0

        # 泛化分
        gen_score = generalization * 10

        total = wr_score + n_score + sig_score + stab_score + gen_score

        # 等级
        if total >= 80: level = 'S'
        elif total >= 65: level = 'A'
        elif total >= 50: level = 'B'
        elif total >= 35: level = 'C'
        else: level = 'D'

        return {
            'total': round(total, 1),
            'level': level,
            'label': CONFIDENCE_GRADE[level]['label'],
            'breakdown': {
                'wr_score': round(wr_score, 1),
                'n_score': round(n_score, 1),
                'sig_score': round(sig_score, 1),
                'stab_score': round(stab_score, 1),
                'gen_score': round(gen_score, 1),
            }
        }

    # ── 内部工具 ────────────────────────────────────────────────

    @staticmethod
    def _grade_v2(ci_low: float, n: int, p_value: float) -> str:
        if ci_low > 0.44 and n >= 200 and p_value < 0.01:  return "⭐S级"
        if ci_low > 0.40 and n >= 100:  return "🟢A级"
        if ci_low > 0.36 and n >= 80:   return "✅B级"
        if ci_low > 0.32 and n >= 50:   return "🟡C级"
        if ci_low > 0.28:               return "🟠参考"
        return "🔴无效"


# ─────────────────────────────────────────────────────────────────
#  DharmaExperiment v2 — 实验基类
# ─────────────────────────────────────────────────────────────────

class DharmaExperiment:
    """
    v2.0 实验基类
    · 自动持久化
    · 运行时间统计
    · 注册表更新
    · 标准化报告输出
    """
    name: str = "base_experiment"
    description: str = "基础实验"
    version: str = "2.0"
    N_BOOTSTRAP: int = 10000   # 全局Bootstrap次数

    def __init__(self):
        self.db   = NodeDB.get()
        self.feat = FeatureKit()
        self.stat = StatEngine()
        self._results: Dict[str, Any] = {}
        self._atr_index: Optional[Dict] = None
        self._t0 = None

    def get_atr_index(self) -> Dict:
        if self._atr_index is None:
            self._atr_index = FeatureKit.build_atr_rank(self.db.nodes)
        return self._atr_index

    def run(self) -> Dict:
        raise NotImplementedError

    def execute(self, save: bool = True) -> Dict:
        try:
            self._t0 = time.time()
            print(f"\n{'═'*68}")
            print(f"  🧪 [{self.name}]  v{self.version}")
            print(f"  📋 {self.description}")
            print(f"  🔢 Bootstrap: {self.N_BOOTSTRAP:,} 次重采样")
            print(f"{'═'*68}")
            results = self.run()
            elapsed = time.time() - self._t0
            results['_meta'] = {
                'name': self.name,
                'version': self.version,
                'description': self.description,
                'elapsed_s': round(elapsed, 2),
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'node_count': len(self.db.nodes),
                'n_bootstrap': self.N_BOOTSTRAP,
            }
            print(f"\n  ⏱  耗时: {elapsed:.1f}s")
            if save:
                self._save(results)
            return results
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).error(
                f'[Dharma] execute 执行失败: {_e}', exc_info=True)
            return None

    def _save(self, results: Dict):
        fname = f"{self.name}_v{self.version.replace('.','_')}.json"
        path = os.path.join(RESULTS_DIR, fname)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"  💾 保存: results/{fname}")
        _update_registry(results['_meta'])

    def print_wr(self, label: str, stats: Dict, n_min: int = 30, show_p: bool = True):
        """v2.0 统一格式输出（含p值）"""
        if stats['n'] < n_min:
            print(f"  {label:38s} n={stats['n']:4d} ⚠️样本不足")
            return
        p_str = f"p={stats.get('p_value',1.0):.4f}" if show_p else ""
        print(f"  {label:38s} "
              f"{stats['mean']*100:5.1f}%  "
              f"CI[{stats['ci_low']*100:.1f}%~{stats['ci_high']*100:.1f}%]  "
              f"n={stats['n']:5d}  {p_str:12s}  {stats['grade']}")

    def print_section(self, title: str):
        print(f"\n  {'─'*60}")
        print(f"  ▶ {title}")
        print(f"  {'─'*60}")

    def print_score(self, label: str, score: Dict):
        bar = "█" * int(score['total'] / 5)
        print(f"  {label:38s} 综合:{score['total']:5.1f}  {bar}  {score['label']}")


# ─────────────────────────────────────────────────────────────────
#  实验注册表
# ─────────────────────────────────────────────────────────────────

REGISTRY_PATH = os.path.join(RESULTS_DIR, '_registry.json')

def _update_registry(meta: Dict):
    registry = {}
    if os.path.exists(REGISTRY_PATH):
        with open(REGISTRY_PATH, encoding='utf-8') as f:
            try: registry = json.load(f)
            except: pass
    key = f"{meta['name']}_v{meta['version']}"
    registry[key] = {
        'name': meta['name'],
        'version': meta['version'],
        'description': meta['description'],
        'last_run': meta['timestamp'],
        'elapsed_s': meta['elapsed_s'],
        'n_bootstrap': meta.get('n_bootstrap', 1000),
    }
    with open(REGISTRY_PATH, 'w', encoding='utf-8') as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)

def show_registry():
    if not os.path.exists(REGISTRY_PATH):
        print("  (注册表为空)")
        return
    with open(REGISTRY_PATH, encoding='utf-8') as f:
        reg = json.load(f)
    print(f"\n  {'实验名':40s} {'版本':5s} {'Bootstrap':10s} {'最后运行':22s} {'耗时'}")
    print("  " + "─"*85)
    for key, v in sorted(reg.items()):
        nb = v.get('n_bootstrap', '?')
        print(f"  {v['name']:40s} v{v['version']:4s} {str(nb):10s} {v['last_run'][:19]:22s} {v['elapsed_s']:.1f}s")
