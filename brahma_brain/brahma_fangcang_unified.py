"""
brahma_fangcang_unified.py — 梵天统一方仓查询层 v1.0
══════════════════════════════════════════════════════
设计院 2026-08-25 苏摩111 封印

使命：合并两套方仓系统，让4627条案例库真正发挥价值

根因：
  系统1 fangcang_engine    — K线滑窗扫描14467根，brahma_core已接入
  系统2 fangcang_hcme_bridge — 案例JSON 4627条，几乎没用（hcme_wr_adj=0.5微弱）
  两套系统结论矛盾，没有合并机制

解决方案：
  unified_fangcang(symbol, ms, signal_dir, regime) → unified_adj
  ├─ 系统1结果: fangcang_engine (宏观K线结构相似度) → adj1
  ├─ 系统2结果: fangcang_hcme_bridge (案例库WR匹配) → adj2
  └─ 加权合并: unified_adj = adj1×0.4 + adj2×0.6（案例库权重更高）

输出 unified_adj 替代原来的 hcme_wr_adj=0.5
"""

import os
import sys
import time
import logging
from pathlib import Path

_BASE = Path(__file__).parent
_log  = logging.getLogger('brahma.fangcang_unified')

sys.path.insert(0, str(_BASE))

# ── 系统1权重 / 系统2权重 ────────────────────────────────────────────
W1 = 0.4   # fangcang_engine K线结构权重
W2 = 0.6   # fangcang_hcme_bridge 案例库权重（质量更高）

# 最大调整幅度（防止极端值）
MAX_ADJ = 15.0
MIN_ADJ = -15.0

# 案例库字段标准化映射
_DIR_MAP = {
    'UP': 'LONG', 'DOWN': 'SHORT', 'LONG': 'LONG', 'SHORT': 'SHORT',
    'BULL': 'LONG', 'BEAR': 'SHORT', 'FLAT': 'NEUTRAL', 'CHOP': 'NEUTRAL',
}


# ── 系统1：fangcang_engine 结果解析 ──────────────────────────────────
def _get_engine_adj(symbol: str, regime: str, signal_dir: str) -> tuple:
    """
    从 fangcang_engine 的输出解析方向信号，转换为 adj 分数。
    返回 (adj: float, confidence: str, n: int)
    """
    try:
        from fangcang_engine import get_fangcang_context
        result = get_fangcang_context(symbol, current_regime=regime)
        if not result or result.get('status') == 'unavailable':
            return 0.0, 'unavailable', 0

        hint = result.get('signal_hint', 'NEUTRAL')
        pm   = result.get('prob_matrix', {})
        p_up = float(pm.get('p_up', 0.33))
        p_dn = float(pm.get('p_down', 0.33))
        n    = int(pm.get('n', 0))

        if n < 5:
            return 0.0, 'insufficient', n

        # 方向一致性得分
        if signal_dir == 'LONG':
            direction_score = p_up - p_dn   # 正=利多，负=不利
        elif signal_dir == 'SHORT':
            direction_score = p_dn - p_up   # 正=利空，负=不利
        else:
            direction_score = 0.0

        # hint强化
        hint_bonus = 0.0
        if signal_dir == 'LONG'  and hint == 'LONG_BIAS':  hint_bonus = 0.1
        if signal_dir == 'SHORT' and hint == 'SHORT_BIAS': hint_bonus = 0.1
        if signal_dir == 'LONG'  and hint == 'SHORT_BIAS': hint_bonus = -0.1
        if signal_dir == 'SHORT' and hint == 'LONG_BIAS':  hint_bonus = -0.1

        raw = (direction_score + hint_bonus) * 12.0  # 放大到[-12, +12]
        adj = max(MIN_ADJ, min(MAX_ADJ, raw))

        confidence = 'HIGH' if n >= 15 else ('MEDIUM' if n >= 8 else 'LOW')
        return round(adj, 2), confidence, n

    except Exception as e:
        _log.debug(f'[unified·s1] {e}')
        return 0.0, 'error', 0


# ── 系统2：fangcang_hcme_bridge 案例库结果解析 ────────────────────────
def _get_cases_adj(symbol: str, ms: dict, signal_dir: str, regime: str) -> tuple:
    """
    从 fangcang_hcme_bridge 案例库匹配结果，转换为 adj 分数。
    同时查询 L3 TradFi 库（贝叶斯融合），扩充小样本置信度。

    L2加密库: weight=0.7（主信号）
    L3TradFi: weight=0.3（宏观参照，样本补充）

    返回 (adj: float, confidence: str, n: int, wr: float)
    """
    try:
        from fangcang_hcme_bridge import fangcang_context_match
        # [修复 2026-08-29] bb_width多路备用：主链传入ms里字段名不一致导致bbw=None全部过滤
        bbw = (
            ms.get('bb_width') or          # brahma_core传入的小数格式
            ms.get('bbw') or               # 短字段别名
            ms.get('bb_pct') or            # 百分比格式
            (ms.get('bb') or {}).get('width') or  # 嵌套格式
            0.01                           # 最终安全値（0.01=1%，普通压缩程度）
        )
        bbw = float(bbw)
        # bbw单位归一化：如果>1说明是百分比形式转小数
        if bbw > 1:
            bbw = bbw / 100
        rsi = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)

        result = fangcang_context_match(symbol, bbw, rsi, regime, signal_dir)
        n_crypto = result.get('n_similar', 0)

        if signal_dir == 'LONG':
            wr_crypto = float(result.get('long_pct', 0.5))
        elif signal_dir == 'SHORT':
            wr_crypto = float(result.get('short_pct', 0.5))
        else:
            wr_crypto = 0.5

        # ── L3 TradFi贝叶斯融合（仅当TradFi代币有映射时启用）────────────────
        wr_final = wr_crypto
        n_final  = n_crypto
        tradfi_note = ''
        try:
            from fangcang_tradfi_db import query_tradfi, TOKEN_TO_STOCK
            if symbol in TOKEN_TO_STOCK:
                # TradFi方向映射
                tf_dir = 'UP' if signal_dir == 'LONG' else 'DOWN'
                squeeze_bars = float(ms.get('squeeze_bars', ms.get('compress_bars', 20)) or 20)
                burst_atr    = float(ms.get('atr_ratio', ms.get('burst_atr', 1.5)) or 1.5)
                vol_ratio    = float(ms.get('vol_ratio', 2.0) or 2.0)
                tf_result = query_tradfi(
                    token=symbol,
                    bb_width_raw=bbw * 100 if bbw < 1 else bbw,  # 统一为%单位
                    squeeze_bars=squeeze_bars,
                    burst_atr=burst_atr,
                    vol_ratio=vol_ratio,
                    rsi=rsi,
                    direction=tf_dir,
                    top_k=20,
                )
                n_tf = tf_result.get('n', 0)
                wr_tf = tf_result.get('wr_directional', 0.5)
                if n_tf >= 3:
                    # 贝叶斯融合：L2加密0.7 + L3TradFi0.3
                    if n_crypto >= 3:
                        wr_final = wr_crypto * 0.7 + wr_tf * 0.3
                    else:
                        # 加密样本不足时，TradFi作主要参考
                        wr_final = wr_crypto * 0.4 + wr_tf * 0.6
                    n_final  = n_crypto + int(n_tf * 0.3)  # 有效样本折算
                    tradfi_note = f' L3={wr_tf:.0%}(n={n_tf})'
                    _log.debug(f'[unified·s2] TradFi融合 {symbol} wr_crypto={wr_crypto:.2f} wr_tf={wr_tf:.2f} → wr_final={wr_final:.2f}')
        except Exception as tf_err:
            _log.debug(f'[unified·s2] TradFi跳过: {tf_err}')
        # ────────────────────────────────────────────────────────────────────

        if n_final < 3:
            return 0.0, 'insufficient', n_final, 0.0

        # WR → adj 映射（40年经验升级版）
        # WR=0.7 → +8.4  WR=0.6 → +2.4  WR=0.5 → 0  WR=0.4 → -2.4  WR=0.3 → -8.4
        adj = (wr_final - 0.5) * 24.0

        # 【新增】burst_atr_mult 加成（Top20相似案例的平均突破力度）
        # 铁证: burst_atr_mult>1.5x + UP → WR=56%~80%，平均+1.28%
        try:
            from fangcang_hcme_bridge import _FANGCANG_CACHE, _load_fangcang_cases
            _fc = result  # fangcang_context_match返回的结果已包含样本信息
            # 从原始结果读取avg_burst（若有）
            _avg_burst = float(result.get('avg_burst_atr_mult', 0) or 0)
            if _avg_burst >= 1.5:
                adj += 3.0   # 平均强突破加分
            elif _avg_burst >= 1.0:
                adj += 1.5
            elif 0 < _avg_burst < 0.5:
                adj -= 3.0   # 平均弱突破惩罚
        except Exception:
            pass

        adj = max(MIN_ADJ, min(MAX_ADJ, adj))

        # 样本量权重（n越多越可信）
        n_weight = min(1.0, n_final / 20.0)
        adj *= n_weight

        confidence = 'HIGH' if n_final >= 15 else ('MEDIUM' if n_final >= 8 else 'LOW')
        if tradfi_note:
            confidence += '+TradFi'
        return round(adj, 2), confidence, n_final, round(wr_final, 3)

    except Exception as e:
        _log.debug(f'[unified·s2] {e}')
        return 0.0, 'error', 0, 0.0


# ── 假突破惩罚 ─────────────────────────────────────────────────────────
def _genuine_breakout_weight(symbol: str, signal_dir: str) -> float:
    """
    检查案例库中假突破比例，高假突破率降低整体可信度。
    返回权重乘数 [0.5, 1.0]
    """
    try:
        import json
        # [2026-08-28 梵天设计院封印] 优先读取统一主库
        data_dir = _BASE.parent / 'data'
        merged_path = data_dir / 'fangcang_merged_v2.json'
        sym_key = symbol.replace('USDT', '').upper()
        if merged_path.exists():
            raw = json.loads(merged_path.read_text())
            all_cases = raw.get('cases', raw) if isinstance(raw, dict) else raw
            cases = [c for c in all_cases if str(c.get('symbol','')).upper() == sym_key]
        else:
            # 备用：分散加载
            fpath = data_dir / f'fangcang_cases_{sym_key.lower()}.json'
            if not fpath.exists():
                return 1.0
            cases = json.loads(fpath.read_text())
            if isinstance(cases, dict):
                cases = cases.get('cases', [])
        n = len(cases)
        if n == 0:
            return 1.0
        fake_n = sum(1 for c in cases if c.get('is_genuine_breakout') is False)
        fake_rate = fake_n / n
        # 假突破率35%→权重0.65，假突破率0%→权重1.0
        return max(0.5, 1.0 - fake_rate * 1.0)
    except Exception:
        return 1.0


# ── 主入口：统一方仓查询 ───────────────────────────────────────────────
def unified_fangcang(
    symbol:     str,
    ms:         dict,
    signal_dir: str,
    regime:     str = 'UNKNOWN',
) -> dict:
    """
    梵天统一方仓查询层。
    合并系统1（K线结构）+ 系统2（案例库WR）→ unified_adj

    参数：
      symbol:     交易对，如 BTCUSDT
      ms:         market_state dict（含bb_width/rsi_1h等）
      signal_dir: LONG / SHORT
      regime:     当前体制

    返回：
      {
        unified_adj:   float,  # 注入 score_final（替代 hcme_wr_adj=0.5）
        s1_adj:        float,  # 系统1贡献
        s2_adj:        float,  # 系统2贡献
        s1_confidence: str,
        s2_confidence: str,
        s1_n:          int,    # 系统1匹配数
        s2_n:          int,    # 系统2匹配数（案例库）
        s2_wr:         float,  # 案例库胜率
        genuine_weight: float, # 真实突破权重
        summary:       str,    # 一句话总结
      }
    """
    t0 = time.time()

    # 并行获取两套系统结果
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_get_engine_adj, symbol, regime, signal_dir)
        f2 = pool.submit(_get_cases_adj, symbol, ms, signal_dir, regime)
        try:
            s1_adj, s1_conf, s1_n   = f1.result(timeout=15)
        except Exception:
            s1_adj, s1_conf, s1_n   = 0.0, 'timeout', 0
        try:
            s2_adj, s2_conf, s2_n, s2_wr = f2.result(timeout=10)
        except Exception:
            s2_adj, s2_conf, s2_n, s2_wr = 0.0, 'timeout', 0, 0.5

    # 假突破权重（降低高假突破率案例库的影响）
    genuine_w = _genuine_breakout_weight(symbol, signal_dir)

    # 加权合并
    raw_adj = s1_adj * W1 + s2_adj * W2 * genuine_w
    unified_adj = round(max(MIN_ADJ, min(MAX_ADJ, raw_adj)), 2)

    # 置信度降级（两套都insufficient时，adj归零）
    both_low = s1_conf in ('insufficient', 'error', 'unavailable') and \
               s2_conf in ('insufficient', 'error', 'unavailable')
    if both_low:
        unified_adj = 0.0

    # 一句话总结
    dir_cn = '做多' if signal_dir == 'LONG' else '做空'
    if unified_adj > 5:
        summary = f'方仓强力确认{dir_cn}(adj={unified_adj:+.1f}): K线相似+案例库WR={s2_wr:.0%}'
    elif unified_adj > 1:
        summary = f'方仓轻微支持{dir_cn}(adj={unified_adj:+.1f}): 案例库n={s2_n} WR={s2_wr:.0%}'
    elif unified_adj < -5:
        summary = f'方仓强力反对{dir_cn}(adj={unified_adj:+.1f}): 历史数据不支持'
    elif unified_adj < -1:
        summary = f'方仓轻微反对{dir_cn}(adj={unified_adj:+.1f}): 案例库WR={s2_wr:.0%}偏低'
    else:
        summary = f'方仓中性(adj={unified_adj:+.1f}): 信号不明确'

    return {
        'unified_adj':    unified_adj,
        's1_adj':         s1_adj,
        's2_adj':         s2_adj,
        's1_confidence':  s1_conf,
        's2_confidence':  s2_conf,
        's1_n':           s1_n,
        's2_n':           s2_n,
        's2_wr':          s2_wr,
        'genuine_weight': genuine_w,
        'summary':        summary,
        'elapsed_ms':     int((time.time() - t0) * 1000),
    }


# ── CLI 验证 ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    dr  = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    ms_fake = {'bb_width': 0.008, 'rsi_1h': 57.0, 'rsi_4h': 68.0}
    print(f'[unified] {sym} {dr} CHOP_MID...')
    result = unified_fangcang(sym, ms_fake, dr, 'CHOP_MID')
    print(f'  unified_adj  = {result["unified_adj"]:+.2f}')
    print(f'  s1(engine)   = {result["s1_adj"]:+.2f}  conf={result["s1_confidence"]} n={result["s1_n"]}')
    print(f'  s2(cases)    = {result["s2_adj"]:+.2f}  conf={result["s2_confidence"]} n={result["s2_n"]} WR={result["s2_wr"]:.0%}')
    print(f'  genuine_w    = {result["genuine_weight"]:.2f}')
    print(f'  summary      = {result["summary"]}')
    print(f'  elapsed      = {result["elapsed_ms"]}ms')
