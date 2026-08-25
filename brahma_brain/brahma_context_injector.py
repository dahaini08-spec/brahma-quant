"""
brahma_context_injector.py — 梵天AI记忆注射器
══════════════════════════════════════════════
设计院 2026-08-25 苏摩111 Phase2封印

使命：
  把梵天47标的完整历史知识，压缩成AI每次调用的「记忆注射」
  让通用AI（Claude/Qwen）拥有梵天专属思维

核心功能：
  inject_brahma_context(symbol, regime, signal_dir, ms)
  → 返回结构化上下文字符串，注入AI议会提示词前缀

包含4层记忆：
  层1: 当前标的方仓历史摘要（跨周期WR/EV/n）
  层2: 梵天铁律（体制封禁/死穴/WR矩阵）
  层3: 最相似历史案例Top3
  层4: 极端事件类比（当前市场特征 vs 历史极端）
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone

_BASE = Path(__file__).parent
_DATA = _BASE.parent / 'data'

sys.path.insert(0, str(_BASE))

# ── 梵天铁律字典（从MEMORY.md封印规则提取）────────────────────────
BRAHMA_IRON_RULES = {
    'BEAR_TREND': {
        'LONG':  '❌ 封禁 WR=45% 严禁做多（精英解锁: score≥155 AND RSI_1H<20 → 0.5%NAV）',
        'SHORT': '✅ 优先 WR=62% SL=2.0% RR=1.0 EV=+0.578%/笔',
    },
    'BULL_TREND': {
        'LONG':  '✅ 优先 WR=72% SL=2.0% RR=1.0 最佳入场: RSI回踩+EMA20支撑',
        'SHORT': '❌ 封禁 死穴 严禁做空（逆势风险极高）',
    },
    'CHOP_MID': {
        'LONG':  '❌ 死穴 CHOP体制做多 WR不足 严禁',
        'SHORT': '⚠️  条件触发 min_score=120 WR=65% SL=2.5% EV=+0.811%/笔',
    },
    'BEAR_RECOVERY': {
        'LONG':  '⚠️  谨慎 仅多单 SIZE=5% LEV=5x 严禁空单',
        'SHORT': '❌ 封禁 BEAR_RECOVERY严禁做空',
    },
    'BULL_EARLY': {
        'LONG':  '✅ 可做 新牛市初期 SIZE适中',
        'SHORT': '❌ 不推荐',
    },
}

# ── 全周期WR矩阵缓存（从经验蒸馏文件读取）────────────────────────
_WR_MATRIX_CACHE = {}
_WR_MATRIX_LOADED = False

# ── 方仓JSON文件缓存（进程级，避免重复IO）───────────────────────
_FANGCANG_FILE_CACHE: dict = {}


def _load_wr_matrix():
    global _WR_MATRIX_CACHE, _WR_MATRIX_LOADED
    if _WR_MATRIX_LOADED:
        return
    try:
        fp = _DATA / 'brahma_experience_matrix.json'
        if fp.exists():
            _WR_MATRIX_CACHE = json.loads(fp.read_text())
    except Exception:
        pass
    _WR_MATRIX_LOADED = True


# ── 层1: 当前标的方仓历史摘要 ────────────────────────────────────
def _warm_fangcang_cache(symbol: str) -> None:
    """预热指定标的的所有周期文件到内存缓存"""
    sym_key = symbol.replace('USDT', '').lower()
    for tf in ['15m', '1h', '4h', '1d', '1w', '1M']:
        for fname in [f'fangcang_{sym_key}_{tf}.json', f'fangcang_cases_{sym_key}.json']:
            fpath = _DATA / fname
            if fpath.exists() and str(fpath) not in _FANGCANG_FILE_CACHE:
                try:
                    _FANGCANG_FILE_CACHE[str(fpath)] = json.loads(fpath.read_text())
                except Exception:
                    pass


def get_fangcang_summary(symbol: str, regime: str, signal_dir: str) -> dict:
    """
    获取该标的在当前体制+方向下的历史表现摘要。
    跨所有可用周期汇总。
    """
    sym_key = symbol.replace('USDT', '').lower()
    summary = {'total_n': 0, 'by_tf': {}, 'best_tf': None, 'best_wr': 0}
    _warm_fangcang_cache(symbol)  # 预热该标的所有文件

    for tf in ['15m', '1h', '4h', '1d', '1w']:
        fpath = _DATA / f'fangcang_{sym_key}_{tf}.json'
        if not fpath.exists():
            # 4H可能用旧路径
            if tf == '4h':
                fpath = _DATA / f'fangcang_cases_{sym_key}.json'
            if not fpath.exists():
                continue
        try:
            if str(fpath) not in _FANGCANG_FILE_CACHE:
                _FANGCANG_FILE_CACHE[str(fpath)] = json.loads(fpath.read_text())
            cases = _FANGCANG_FILE_CACHE[str(fpath)]
            if not isinstance(cases, list):
                continue

            # 过滤当前方向案例
            dir_up = signal_dir == 'LONG'
            dir_cases = [c for c in cases if
                         str(c.get('direction', c.get('breakout_direction', ''))).upper()
                         in (['UP', 'LONG'] if dir_up else ['DOWN', 'SHORT'])]

            if not dir_cases:
                continue

            # 计算胜率（未来收益>0为胜）
            ret_field = next((k for k in ['future_return', 'future_return_24h', 'future_ret']
                              if k in dir_cases[0]), None)
            if not ret_field:
                continue

            rets = [float(c.get(ret_field, 0) or 0) for c in dir_cases]
            wins = sum(1 for r in rets if (r > 0 if dir_up else r < 0))
            wr   = wins / len(rets) if rets else 0
            avg_ret = sum(rets) / len(rets) if rets else 0

            # 近1年案例
            recent_n = 0
            cutoff   = time.time() - 365 * 86400
            for c in dir_cases:
                ts_str = c.get('ts_burst', c.get('ts_squeeze_start', ''))
                try:
                    if isinstance(ts_str, str) and ts_str:
                        ts = datetime.fromisoformat(ts_str.replace('+00:00', '')).timestamp()
                        if ts > cutoff:
                            recent_n += 1
                except Exception:
                    pass

            summary['by_tf'][tf] = {
                'n': len(dir_cases), 'wr': round(wr, 3),
                'avg_ret': round(avg_ret, 2), 'recent_1y': recent_n,
            }
            summary['total_n'] += len(dir_cases)

            if wr > summary['best_wr'] and len(dir_cases) >= 8:
                summary['best_wr']  = wr
                summary['best_tf']  = tf

        except Exception:
            continue

    return summary


# ── 层2: 梵天铁律 ────────────────────────────────────────────────
def get_brahma_rules(regime: str, signal_dir: str) -> str:
    """获取当前体制+方向的梵天铁律文本"""
    rules = BRAHMA_IRON_RULES.get(regime, {})
    rule  = rules.get(signal_dir, '⚪ 无特定规则，参考通用评分')
    return f'[{regime} × {signal_dir}] {rule}'


# ── 层3: 最相似历史案例Top3 ──────────────────────────────────────
def get_top3_similar(symbol: str, ms: dict) -> str:
    """从方仓库找最相似的Top3历史案例"""
    try:
        from fangcang_hcme_bridge import fangcang_context_match
        bbw = float(ms.get('bb_width', ms.get('bbw', 0.01)) or 0.01)
        rsi = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)
        regime = ms.get('regime', 'UNKNOWN')
        signal_dir = ms.get('signal_dir', 'LONG')

        result = fangcang_context_match(symbol, bbw, rsi, regime, signal_dir)
        top3   = (result.get('top_similar') or [])[:3]

        if not top3:
            return '暂无相似历史案例'

        lines = []
        for i, c in enumerate(top3):
            ts_str = c.get('ts_burst', c.get('ts_squeeze_start', '?'))
            if isinstance(ts_str, str) and len(ts_str) > 10:
                ts_str = ts_str[:10]
            ret     = c.get('future_return', c.get('future_return_24h', 0)) or 0
            direct  = c.get('direction', c.get('breakout_direction', '?'))
            genuine = '真实突破' if c.get('is_genuine_breakout') else '假突破'
            sim     = c.get('_sim_score', 0)
            lines.append(
                f'  案例{i+1}: {ts_str} 方向={direct} 24H收益={ret:+.1f}% '
                f'相似度={sim:.3f} {genuine}'
            )
        return '\n'.join(lines)
    except Exception as e:
        return f'案例检索失败: {e}'


# ── 层4: 极端事件类比 ─────────────────────────────────────────────
def get_extreme_analog(ms: dict) -> str:
    """把当前市场特征与历史极端事件对比"""
    try:
        from brahma_longmem import get_extreme_event_warning, EXTREME_EVENTS
        symbol = ms.get('symbol', 'BTCUSDT')
        ew     = get_extreme_event_warning(symbol)
        level  = ew.get('warning_level', 'NONE')
        event  = ew.get('matched_event', '')
        sim    = ew.get('similarity', 0)
        adj    = ew.get('score_adj', 0)

        if level == 'NONE':
            return '当前市场特征与历史极端事件无明显相似（正常区间）'

        icon = {'CRITICAL': '🚨', 'HIGH': '⚠️', 'OPPORTUNITY': '💡'}.get(level, '⚪')
        return (f'{icon} {level}: 当前市场与「{event}」相似度={sim:.2f}\n'
                f'  历史应对: {ew.get("action","NORMAL")} | score_adj={adj:+d}')
    except Exception as e:
        return f'极端事件检索失败: {e}'


# ── 主入口：注射梵天上下文 ─────────────────────────────────────────
def inject_brahma_context(
    symbol:     str,
    regime:     str,
    signal_dir: str,
    ms:         dict,
    include_cases:   bool = True,
    include_extreme: bool = True,
    max_chars:       int  = 1200,
) -> str:
    """
    把梵天专有知识压缩成AI可读的系统提示词前缀。
    每次AI议会调用前注入，让AI拥有梵天专属思维。

    参数:
      symbol:     交易对
      regime:     当前体制
      signal_dir: LONG/SHORT
      ms:         market_state（含rsi/bbw/fg等）
      max_chars:  最大字符数（控制token消耗）
    """
    lines = [
        '═══ 梵天专属知识库（优先于通用金融知识）═══',
        '',
        f'【标的】{symbol} | 【体制】{regime} | 【方向】{signal_dir}',
        '',
    ]

    # 层2: 铁律（最重要，放最前）
    lines += [
        '【梵天铁律】',
        get_brahma_rules(regime, signal_dir),
        '',
    ]

    # 层1: 方仓历史摘要
    fc_summary = get_fangcang_summary(symbol, regime, signal_dir)
    total_n    = fc_summary.get('total_n', 0)
    best_tf    = fc_summary.get('best_tf')
    best_wr    = fc_summary.get('best_wr', 0)

    if total_n > 0:
        lines.append('【方仓历史】')
        by_tf = fc_summary.get('by_tf', {})
        for tf in ['1d', '4h', '1h', '15m']:
            if tf not in by_tf:
                continue
            d = by_tf[tf]
            lines.append(
                f'  {tf}: n={d["n"]} WR={d["wr"]:.0%} '
                f'avg={d["avg_ret"]:+.1f}% 近1年={d["recent_1y"]}条'
            )
        if best_tf:
            lines.append(f'  ★ 最优周期: {best_tf} WR={best_wr:.0%}')
        lines.append('')

    # 层3: 相似案例
    if include_cases and total_n > 0:
        lines += ['【最相似历史案例 Top3】']
        ms_with_context = {**ms, 'regime': regime, 'signal_dir': signal_dir}
        lines.append(get_top3_similar(symbol, ms_with_context))
        lines.append('')

    # 层4: 极端事件
    if include_extreme:
        lines += ['【极端事件类比】', get_extreme_analog({**ms, 'symbol': symbol}), '']

    lines.append('基于以上梵天专有数据（不是通用金融知识）给出你的裁决：')
    lines.append('═══════════════════════════════════════')

    result = '\n'.join(lines)

    # 控制长度
    if len(result) > max_chars:
        result = result[:max_chars] + '\n...(截断)'

    return result


# ── CLI 测试 ──────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    dr  = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    rgm = sys.argv[3] if len(sys.argv) > 3 else 'CHOP_MID'
    ms  = {'bb_width': 0.008, 'rsi_1h': 57.0, 'rsi_4h': 68.0, 'fg': 74}

    print(inject_brahma_context(sym, rgm, dr, ms))
