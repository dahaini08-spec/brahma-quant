"""
portfolio_optimizer.py — 梵天投资组合层优化器 v1.0
════════════════════════════════════════════════════
设计院 封印 2026-07-01

使命：
  扩展现有 conflict_resolver（只做BTC/ETH对检查），
  实现 portfolio-level 相关性优化，当多个候选信号同时
  出现时，选出分散度最优的子集，避免1.85x隐性BTC风险敞口。

现有模块关系：
  conflict_resolver.py  → 单信号维度冲突检查（保持不变）
  capital_allocator.py  → 单信号资金分配（保持不变）
  portfolio_optimizer.py → NEW: 多信号组合优化层

设计原则（risk parity简化版）：
  1. 计算候选信号间的30天滚动相关性
  2. 贪心算法选出相关性<阈值的最优子集（max 3个）
  3. 按EV期望值加权排序
  4. 输出：推荐执行的信号列表 + 被过滤的信号列表

达摩院约束：
  - 相关性计算基于历史parquet数据（30天滚动）
  - 相关性阈值=0.75（经达摩院V7验证）
  - BTC/ETH同时有效时，允许持有但标记风险×1.85
"""

# ── STATUS: ACTIVE ────────────────────────────────────────────
# 多信号组合优化，capital_allocator的上游过滤器
# LAST_REVIEW: 2026-07-01 | 设计院初次封印
# ─────────────────────────────────────────────────────────────

from __future__ import annotations
import json, time, logging
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("portfolio_optimizer")

BASE     = Path(__file__).parent.parent
DATA_DIR = BASE / 'dharma' / 'data'

# ── 参数 ─────────────────────────────────────────────────────
CORR_THRESHOLD   = 0.75   # 相关性>0.75 → 视为同向风险
MAX_POSITIONS    = 3      # 最大同时持仓
BTC_ETH_RISK_MULT = 1.85  # BTC+ETH同时持有的实际风险乘数（达摩院实测）
CORR_WINDOW      = 720    # 30天 × 24H = 720根1H K线
CORR_CACHE_TTL   = 3600   # 相关性矩阵缓存1小时

# ── 已知高相关组（兜底规则，无需计算）────────────────────────
KNOWN_HIGH_CORR_PAIRS = {
    ('BTCUSDT', 'ETHUSDT'):   0.85,  # 长期高相关
    ('BTCUSDT', 'BNBUSDT'):   0.80,
    ('ETHUSDT', 'BNBUSDT'):   0.78,
    ('SOLUSDT', 'AVAXUSDT'):  0.76,
    ('SOLUSDT', 'NEARUSDT'):  0.73,
    ('DOTUSDT', 'ATOMUSDT'):  0.74,
    ('ADAUSDT', 'DOTUSDT'):   0.72,
}

# ── 缓存 ─────────────────────────────────────────────────────
_corr_cache: Dict[str, Tuple[float, float]] = {}  # (sym1,sym2): (ts, corr)


# ════════════════════════════════════════════════════════════════
# 1. 相关性计算
# ════════════════════════════════════════════════════════════════

def _load_returns(symbol: str, window: int = CORR_WINDOW) -> Optional[pd.Series]:
    """
    加载品种最近 window 根1H K线的收益率序列

    优先从 dharma/data/ 的 parquet 文件读取
    """
    fname = symbol.lower().replace('usdt', 'usdt') + '_1h_2018_2026.parquet'
    fpath = DATA_DIR / fname

    if not fpath.exists():
        logger.debug(f"无历史数据: {fname}")
        return None

    try:
        df = pd.read_parquet(fpath, columns=['close'])
        df = df.sort_index().tail(window + 10)
        returns = df['close'].pct_change().dropna().tail(window)
        if len(returns) < 100:  # 数据不足
            return None
        return returns
    except Exception as e:
        logger.debug(f"读取失败 {fname}: {e}")
        return None


def get_pair_correlation(sym1: str, sym2: str) -> float:
    """
    计算两个品种的30天滚动相关性

    优先顺序：
    1. 内存缓存（1小时有效）
    2. 已知高相关对规则
    3. 从历史数据计算
    4. 默认值（同类资产保守估计）
    """
    # 规范化键（顺序无关）
    pair = tuple(sorted([sym1, sym2]))
    cache_key = f"{pair[0]}:{pair[1]}"
    now = time.time()

    # 内存缓存
    if cache_key in _corr_cache:
        ts, corr = _corr_cache[cache_key]
        if now - ts < CORR_CACHE_TTL:
            return corr

    # 已知规则
    corr = KNOWN_HIGH_CORR_PAIRS.get(pair, None)
    if corr is not None:
        _corr_cache[cache_key] = (now, corr)
        return corr

    # 从数据计算
    r1 = _load_returns(sym1)
    r2 = _load_returns(sym2)

    if r1 is not None and r2 is not None:
        # 对齐时间序列
        combined = pd.concat([r1.rename('r1'), r2.rename('r2')], axis=1).dropna()
        if len(combined) >= 100:
            corr = float(combined['r1'].corr(combined['r2']))
            corr = max(-1.0, min(1.0, corr))
            _corr_cache[cache_key] = (now, corr)
            return corr

    # 默认：crypto同类资产默认中高相关
    default_corr = 0.65
    _corr_cache[cache_key] = (now, default_corr)
    return default_corr


def build_corr_matrix(symbols: List[str]) -> pd.DataFrame:
    """构建 N×N 相关性矩阵"""
    n = len(symbols)
    matrix = np.eye(n)

    for i in range(n):
        for j in range(i + 1, n):
            corr = get_pair_correlation(symbols[i], symbols[j])
            matrix[i][j] = corr
            matrix[j][i] = corr

    return pd.DataFrame(matrix, index=symbols, columns=symbols)


# ════════════════════════════════════════════════════════════════
# 2. 贪心最优子集选择
# ════════════════════════════════════════════════════════════════

def _calc_ev(signal: Dict) -> float:
    """
    从信号字典中提取期望EV（用于排序优先级）

    使用score作为proxy：score越高EV越大
    后续可接入实际WR矩阵计算真实EV
    """
    score = float(signal.get('confluence', {}).get('score', 0)
                  or signal.get('score', 0))
    regime    = signal.get('regime', 'UNKNOWN').upper()
    direction = signal.get('direction', 'LONG').upper()

    # 体制EV调整系数（来自MEMORY.md封印数据）
    regime_ev_mult = {
        'BEAR_TREND:SHORT':    1.6,
        'BEAR_TREND:LONG':     0.1,  # 死穴
        'BEAR_RECOVERY:LONG':  1.2,
        'BULL_TREND:LONG':     1.6,
        'BULL_TREND:SHORT':    0.15,
        'CHOP_MID:LONG':       0.5,
        'CHOP_MID:SHORT':      0.88,
        'BEAR_EARLY:SHORT':    1.2,
        'BEAR_EARLY:LONG':     0.35,
    }

    key  = f"{regime}:{direction}"
    mult = regime_ev_mult.get(key, 0.7)
    return score * mult


def select_optimal_portfolio(
    candidates: List[Dict],
    max_positions: int = MAX_POSITIONS,
    corr_threshold: float = CORR_THRESHOLD,
    verbose: bool = False
) -> Dict:
    """
    从候选信号中贪心选出分散度最优的子集

    算法：
    1. 按EV降序排列候选信号
    2. 贪心：依次尝试添加信号，检查与已选信号的相关性
    3. 若与已选中任意信号相关性 > corr_threshold，跳过（但记录原因）
    4. 直到达到 max_positions 或候选耗尽

    Args:
        candidates: 信号字典列表，每个需包含 symbol, direction, score/confluence
        max_positions: 最大组合仓位数
        corr_threshold: 相关性拒绝阈值
        verbose: 打印选择过程

    Returns:
        {
          'selected':     [信号dict, ...],       # 推荐执行
          'filtered':     [{'signal': dict, 'reason': str}, ...],  # 被过滤
          'corr_matrix':  pd.DataFrame,          # 所有候选相关性
          'portfolio_risk_mult': float,           # 组合风险乘数
          'diversity_score': float,              # 分散度评分 (0-1)
          'warning':      str | None,            # 特殊风险提示
        }
    """
    if not candidates:
        return {
            'selected': [], 'filtered': [],
            'corr_matrix': None, 'portfolio_risk_mult': 1.0,
            'diversity_score': 1.0, 'warning': None
        }

    # 只取一个信号直接返回
    if len(candidates) == 1:
        return {
            'selected': candidates, 'filtered': [],
            'corr_matrix': None, 'portfolio_risk_mult': 1.0,
            'diversity_score': 1.0, 'warning': None
        }

    # 按EV排序
    sorted_candidates = sorted(candidates, key=_calc_ev, reverse=True)
    symbols = [s.get('symbol', 'UNK') for s in sorted_candidates]

    # 构建相关性矩阵
    corr_mat = build_corr_matrix(symbols)

    # 贪心选择
    selected_syms: List[str] = []
    selected: List[Dict]     = []
    filtered: List[Dict]     = []

    for sig in sorted_candidates:
        sym = sig.get('symbol', 'UNK')
        ev  = _calc_ev(sig)

        if len(selected) >= max_positions:
            filtered.append({
                'signal': sig,
                'reason': f'超过最大仓位数 {max_positions}',
                'ev':     round(ev, 1)
            })
            continue

        # 检查与已选中信号的相关性
        rejected = False
        reject_reason = ''

        for sel_sym in selected_syms:
            corr = corr_mat.loc[sym, sel_sym] if sym in corr_mat.index and sel_sym in corr_mat.index \
                   else get_pair_correlation(sym, sel_sym)

            if abs(corr) >= corr_threshold:
                rejected     = True
                reject_reason = f'与{sel_sym}相关性={corr:.2f}>{corr_threshold}'
                break

        if rejected:
            filtered.append({
                'signal': sig,
                'reason': reject_reason,
                'ev':     round(ev, 1)
            })
            if verbose:
                logger.info(f"  ❌ {sym} 过滤: {reject_reason}")
        else:
            selected.append(sig)
            selected_syms.append(sym)
            if verbose:
                logger.info(f"  ✅ {sym} 选入 (EV={ev:.1f})")

    # 计算组合风险乘数
    portfolio_risk_mult = _calc_portfolio_risk_mult(selected_syms)

    # 计算分散度评分
    diversity_score = _calc_diversity(selected_syms, corr_mat)

    # 特殊风险提示
    warning = None
    sel_set = set(selected_syms)
    if 'BTCUSDT' in sel_set and 'ETHUSDT' in sel_set:
        warning = f'BTC+ETH同时持有，实际风险敞口={BTC_ETH_RISK_MULT}x（达摩院实测）'

    return {
        'selected':            selected,
        'filtered':            filtered,
        'corr_matrix':         corr_mat,
        'selected_symbols':    selected_syms,
        'portfolio_risk_mult': round(portfolio_risk_mult, 3),
        'diversity_score':     round(diversity_score, 3),
        'warning':             warning,
        'ts':                  datetime.now(timezone.utc).isoformat(),
    }


def _calc_portfolio_risk_mult(symbols: List[str]) -> float:
    """
    估算组合实际风险乘数

    BTC+ETH相关性=0.85，实际风险=1.85x（达摩院封印值）
    一般组合：sqrt(n + corr_sum)
    """
    n = len(symbols)
    if n <= 1:
        return 1.0

    # BTC+ETH特殊处理
    s = set(symbols)
    if 'BTCUSDT' in s and 'ETHUSDT' in s:
        base = BTC_ETH_RISK_MULT
        extra = (n - 2) * 0.7  # 每增加一个相对独立品种，风险增加0.7
        return base + extra

    # 通用：估算相关性加和
    total_corr = 0.0
    pairs = 0
    for i, s1 in enumerate(symbols):
        for j, s2 in enumerate(symbols):
            if i >= j:
                continue
            total_corr += abs(get_pair_correlation(s1, s2))
            pairs += 1

    avg_corr = total_corr / (pairs + 1e-9)
    # 风险乘数 = sqrt(n + n*(n-1)*avg_corr)
    risk_mult = (n + n * (n - 1) * avg_corr) ** 0.5
    return risk_mult


def _calc_diversity(symbols: List[str], corr_mat: pd.DataFrame) -> float:
    """
    计算组合分散度（0=完全相关，1=完全独立）
    """
    if len(symbols) <= 1:
        return 1.0

    corr_vals = []
    for i, s1 in enumerate(symbols):
        for j, s2 in enumerate(symbols):
            if i >= j:
                continue
            if s1 in corr_mat.index and s2 in corr_mat.index:
                corr_vals.append(abs(corr_mat.loc[s1, s2]))
            else:
                corr_vals.append(0.65)  # 默认值

    avg_corr = sum(corr_vals) / len(corr_vals) if corr_vals else 0
    return round(1.0 - avg_corr, 3)


# ════════════════════════════════════════════════════════════════
# 3. 快速接口（与 capital_allocator 兼容）
# ════════════════════════════════════════════════════════════════

def filter_signals(signals: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """
    快速过滤接口（供 brahma_parallel_engine 调用）

    Returns:
        (approved_signals, rejected_signals)
    """
    result = select_optimal_portfolio(signals)
    approved = result['selected']
    rejected = [item['signal'] for item in result['filtered']]
    return approved, rejected


def check_correlation_risk(sym1: str, sym2: str) -> Dict:
    """
    检查两个品种的相关性风险（扩展 conflict_resolver 的 BTC/ETH 检查）

    Returns:
        {'high_corr': bool, 'corr': float, 'risk_mult': float, 'warning': str}
    """
    corr = get_pair_correlation(sym1, sym2)
    high = abs(corr) >= CORR_THRESHOLD

    if sym1 in ('BTCUSDT', 'ETHUSDT') and sym2 in ('BTCUSDT', 'ETHUSDT'):
        risk_mult = BTC_ETH_RISK_MULT
    else:
        risk_mult = 1.0 + abs(corr) * 0.85 if high else 1.0

    return {
        'high_corr':  high,
        'corr':       round(corr, 3),
        'risk_mult':  round(risk_mult, 3),
        'threshold':  CORR_THRESHOLD,
        'warning':    f'{sym1}+{sym2}相关性={corr:.2f}，实际风险={risk_mult:.2f}x' if high else None
    }


def portfolio_summary(active_positions: List[Dict]) -> Dict:
    """
    当前持仓组合风险摘要（供 position_monitor 调用）

    Args:
        active_positions: [{'symbol': str, 'direction': str, 'nav_pct': float}, ...]
    """
    if not active_positions:
        return {'n': 0, 'risk_mult': 1.0, 'diversity': 1.0, 'warnings': []}

    symbols   = [p['symbol'] for p in active_positions]
    corr_mat  = build_corr_matrix(symbols)
    risk_mult = _calc_portfolio_risk_mult(symbols)
    diversity = _calc_diversity(symbols, corr_mat)

    warnings = []
    if 'BTCUSDT' in symbols and 'ETHUSDT' in symbols:
        warnings.append(f'BTC+ETH同时持有，风险={BTC_ETH_RISK_MULT}x')
    if risk_mult > 2.0:
        warnings.append(f'组合风险乘数={risk_mult:.2f}x > 2.0，建议减仓')
    if diversity < 0.3:
        warnings.append(f'组合分散度={diversity:.2f}极低，持仓高度相关')

    return {
        'n':           len(symbols),
        'symbols':     symbols,
        'risk_mult':   round(risk_mult, 3),
        'diversity':   round(diversity, 3),
        'corr_matrix': corr_mat.round(3).to_dict() if corr_mat is not None else {},
        'warnings':    warnings,
        'ts':          datetime.now(timezone.utc).isoformat(),
    }


# ════════════════════════════════════════════════════════════════
# 4. 主入口（测试）
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("🧪 Portfolio Optimizer 测试\n")

    # 测试1：相关性检查
    pairs = [
        ('BTCUSDT', 'ETHUSDT'),
        ('SOLUSDT', 'AVAXUSDT'),
        ('BTCUSDT', 'DOGEUSDT'),
        ('ADAUSDT', 'NEARUSDT'),
    ]
    print("=== 相关性矩阵 ===")
    for s1, s2 in pairs:
        r = check_correlation_risk(s1, s2)
        flag = "⚠️ " if r['high_corr'] else "  "
        print(f"{flag}{s1[:8]} × {s2[:8]}: corr={r['corr']:.2f}  risk×={r['risk_mult']:.2f}")

    # 测试2：多信号组合优化
    mock_signals = [
        {'symbol': 'BTCUSDT', 'direction': 'SHORT', 'regime': 'BEAR_TREND',
         'confluence': {'score': 148}},
        {'symbol': 'ETHUSDT', 'direction': 'SHORT', 'regime': 'BEAR_TREND',
         'confluence': {'score': 142}},
        {'symbol': 'SOLUSDT', 'direction': 'SHORT', 'regime': 'BEAR_EARLY',
         'confluence': {'score': 135}},
        {'symbol': 'DOGEUSDT', 'direction': 'LONG', 'regime': 'BEAR_RECOVERY',
         'confluence': {'score': 131}},
        {'symbol': 'AVAXUSDT', 'direction': 'SHORT', 'regime': 'BEAR_EARLY',
         'confluence': {'score': 128}},
    ]

    print("\n=== 组合优化（5信号→最优3个）===")
    result = select_optimal_portfolio(mock_signals, verbose=True)

    print(f"\n✅ 选入 ({len(result['selected'])}):")
    for s in result['selected']:
        ev = _calc_ev(s)
        print(f"   {s['symbol']:12} {s['direction']:5} score={s['confluence']['score']}  EV={ev:.0f}")

    print(f"\n❌ 过滤 ({len(result['filtered'])}):")
    for f in result['filtered']:
        print(f"   {f['signal']['symbol']:12} → {f['reason']}")

    print(f"\n📊 组合风险: mult={result['portfolio_risk_mult']}x  diversity={result['diversity_score']}")
    if result['warning']:
        print(f"⚠️  {result['warning']}")

    # 测试3：持仓摘要
    print("\n=== 当前持仓摘要 ===")
    active = [
        {'symbol': 'BTCUSDT', 'direction': 'SHORT', 'nav_pct': 0.02},
        {'symbol': 'ETHUSDT', 'direction': 'SHORT', 'nav_pct': 0.02},
    ]
    summary = portfolio_summary(active)
    print(f"风险乘数: {summary['risk_mult']}x")
    print(f"分散度:   {summary['diversity']}")
    for w in summary['warnings']:
        print(f"⚠️  {w}")
