#!/usr/bin/env python3
"""
brahma_experience_distiller.py — 梵天经验蒸馏矩阵
══════════════════════════════════════════════════
设计院 2026-08-25 苏摩111 Phase3封印

使命：
  把15795条原始方仓案例 → 压缩成AI可直接调用的WR索引
  让AI议会不必读原始数据，直接查矩阵得到"梵天铁证"

索引维度：
  体制(regime) × 方向(dir) × 周期(tf) × 评分段(score_band)

输出文件：
  data/brahma_experience_matrix.json  ← 注射器读取入口
  data/brahma_wr_by_coin.json         ← 单币WR速查表
  data/brahma_phase3_report.txt       ← 人工可读摘要

核心指标（每个格子）：
  n         : 历史案例数
  wr        : 胜率（多=收益>0, 空=收益<0）
  avg_ret   : 平均收益率%
  best_coin : 胜率最高的币种
  worst_coin: 胜率最低的币种
  top_tf    : 最优周期
"""
import sys
import json
import glob
import time
from pathlib import Path
from datetime import datetime, timezone

_BASE = Path(__file__).parent
_DATA = _BASE.parent / 'data'

# ── 体制列表 ──────────────────────────────────────────────────────
REGIMES = ['BULL_TREND', 'BULL_EARLY', 'BEAR_TREND', 'BEAR_EARLY',
           'CHOP_MID', 'BEAR_RECOVERY']
DIRECTIONS = ['LONG', 'SHORT']
TIMEFRAMES  = ['15m', '1h', '4h', '1d', '1w', '1M']

# ── 方仓案例的方向字段映射 ─────────────────────────────────────────
def _norm_dir(raw: str) -> str:
    r = str(raw).upper()
    if r in ('UP', 'LONG'):    return 'LONG'
    if r in ('DOWN', 'SHORT'): return 'SHORT'
    return ''

def _is_win(ret: float, direction: str) -> bool:
    """多单收益>0为胜；空单收益<0为胜（future_return以多头视角计）"""
    if direction == 'LONG':
        return ret > 0
    else:
        return ret < 0


# ── 加载所有方仓JSON ───────────────────────────────────────────────
def load_all_cases() -> list:
    files = glob.glob(str(_DATA / 'fangcang_*_*.json'))
    files = [f for f in files if 'snapshot' not in f and 'cases_' not in f
             and 'weights' not in f]
    all_cases = []
    for f in files:
        try:
            data = json.loads(Path(f).read_text())
            if isinstance(data, list):
                all_cases.extend(data)
        except Exception:
            pass
    return all_cases


# ── 从案例推断体制（方仓案例无直接体制字段，用规则映射）────────────
def infer_regime_from_case(case: dict) -> list:
    """
    方仓案例不含体制标签，返回所有可能体制（宽泛匹配）。
    蒸馏时：每条案例贡献到所有体制 × 对应方向的桶。
    后续AI调用时查特定体制桶。
    """
    # 1w/1M案例用EMA叉，代表趋势切换
    tf = case.get('timeframe', '4h')
    trigger = case.get('trigger', '')
    direction = _norm_dir(case.get('direction', ''))

    if tf in ('1w', '1M'):
        if trigger == 'golden_cross':
            return ['BULL_TREND', 'BULL_EARLY']
        elif trigger == 'death_cross':
            return ['BEAR_TREND', 'BEAR_EARLY']
        else:
            return ['BULL_TREND', 'BEAR_TREND']

    # 短周期：BB压缩爆发 → 所有体制都有效（不分体制，只分方向+周期）
    return ['ALL']


# ── 核心蒸馏函数 ──────────────────────────────────────────────────
def distill(cases: list) -> dict:
    """
    返回多层索引：
    {
      "by_regime_dir_tf": {
        "BEAR_TREND:SHORT:4h": {"n":49, "wr":0.61, "avg_ret":-0.8, ...},
        ...
      },
      "by_coin_dir_tf": {
        "BTC:SHORT:4h": {"n":49, "wr":0.61, ...},
        ...
      },
      "by_dir_tf": {
        "SHORT:4h": {"n":350, "wr":0.58, ...},
        ...
      },
      "top_coins_by_regime_dir": {
        "BEAR_TREND:SHORT": [{"coin":"BTC","wr":0.67,"n":134},...],
        ...
      },
      "meta": {...}
    }
    """
    # 按维度聚合桶
    # key → list of (ret, direction)
    buckets_rdt  = {}   # regime:dir:tf
    buckets_cdt  = {}   # coin:dir:tf
    buckets_dt   = {}   # dir:tf
    buckets_rd   = {}   # regime:dir (for top_coins)
    buckets_rd_coin = {}  # regime:dir:coin

    total = 0
    skipped = 0
    for c in cases:
        direction = _norm_dir(c.get('direction', c.get('breakout_direction', '')))
        if not direction:
            skipped += 1
            continue

        ret_raw = c.get('future_return', c.get('future_return_24h', c.get('future_ret', None)))
        if ret_raw is None:
            skipped += 1
            continue
        ret = float(ret_raw)
        tf  = str(c.get('timeframe', '4h'))
        sym = str(c.get('symbol', '')).upper().replace('USDT', '')
        if not sym:
            skipped += 1
            continue

        win = _is_win(ret, direction)
        total += 1

        # by_dir_tf
        k_dt = f'{direction}:{tf}'
        buckets_dt.setdefault(k_dt, []).append((ret, win))

        # by_coin_dir_tf
        k_cdt = f'{sym}:{direction}:{tf}'
        buckets_cdt.setdefault(k_cdt, []).append((ret, win))

        # by_regime_dir_tf — 对每个推断体制都写入
        regimes = infer_regime_from_case(c)
        for rgm in regimes:
            k_rdt = f'{rgm}:{direction}:{tf}'
            buckets_rdt.setdefault(k_rdt, []).append((ret, win))

            k_rd = f'{rgm}:{direction}'
            buckets_rd.setdefault(k_rd, []).append((ret, win))

            k_rdc = f'{rgm}:{direction}:{sym}'
            buckets_rd_coin.setdefault(k_rdc, []).append((ret, win))

    def _calc(entries: list) -> dict:
        if not entries:
            return {'n': 0, 'wr': 0.0, 'avg_ret': 0.0}
        rets = [e[0] for e in entries]
        wins = sum(1 for e in entries if e[1])
        return {
            'n':       len(entries),
            'wr':      round(wins / len(entries), 4),
            'avg_ret': round(sum(rets) / len(entries), 4),
        }

    # 计算每个桶的统计
    by_rdt = {k: _calc(v) for k, v in buckets_rdt.items()}
    by_cdt = {k: _calc(v) for k, v in buckets_cdt.items()}
    by_dt  = {k: _calc(v) for k, v in buckets_dt.items()}

    # top_coins_by_regime_dir
    top_coins = {}
    for rd_key, rd_entries in buckets_rd.items():
        # 收集该 regime:dir 下各 coin 的表现
        regime_dir = rd_key  # e.g. "BEAR_TREND:SHORT"
        coin_stats = {}
        for rdc_key, rdc_entries in buckets_rd_coin.items():
            parts = rdc_key.split(':')
            if len(parts) == 3:
                r, d, coin = parts
                if f'{r}:{d}' == regime_dir:
                    stats = _calc(rdc_entries)
                    if stats['n'] >= 3:  # 至少3条才有意义
                        coin_stats[coin] = stats

        ranked = sorted(coin_stats.items(), key=lambda x: x[1]['wr'], reverse=True)
        top_coins[regime_dir] = [
            {'coin': coin, 'wr': s['wr'], 'n': s['n'], 'avg_ret': s['avg_ret']}
            for coin, s in ranked[:10]
        ]

    return {
        'by_regime_dir_tf':      by_rdt,
        'by_coin_dir_tf':        by_cdt,
        'by_dir_tf':             by_dt,
        'top_coins_by_regime_dir': top_coins,
        'meta': {
            'total_cases':   total,
            'skipped':       skipped,
            'built_at':      datetime.now(timezone.utc).isoformat(),
            'version':       'phase3-v1.0',
        }
    }


# ── 单币WR速查表 ──────────────────────────────────────────────────
def build_coin_wr_table(matrix: dict) -> dict:
    """
    格式: {
      "BTC": {
        "SHORT": {"4h": {"wr":0.61,"n":49}, "1h": {...}, ...},
        "LONG":  {...}
      }, ...
    }
    """
    result = {}
    for key, stats in matrix['by_coin_dir_tf'].items():
        parts = key.split(':')
        if len(parts) != 3:
            continue
        coin, direction, tf = parts
        result.setdefault(coin, {}).setdefault(direction, {})[tf] = {
            'wr': stats['wr'], 'n': stats['n'], 'avg_ret': stats['avg_ret']
        }
    return result


# ── 人工可读报告 ──────────────────────────────────────────────────
def build_report(matrix: dict) -> str:
    lines = [
        '═══ 梵天经验蒸馏矩阵 Phase3 报告 ═══',
        f'生成时间: {matrix["meta"]["built_at"]}',
        f'总案例: {matrix["meta"]["total_cases"]:,}  跳过: {matrix["meta"]["skipped"]}',
        '',
    ]

    # 核心体制×方向×周期汇总
    lines.append('【核心 WR 矩阵（体制×方向×周期）】')
    key_combos = [
        ('BEAR_TREND', 'SHORT'), ('BEAR_TREND', 'LONG'),
        ('BULL_TREND', 'LONG'),  ('BULL_TREND', 'SHORT'),
        ('CHOP_MID',  'SHORT'), ('ALL', 'SHORT'), ('ALL', 'LONG'),
    ]
    for rgm, dr in key_combos:
        row_parts = []
        for tf in ['15m', '1h', '4h', '1d']:
            k = f'{rgm}:{dr}:{tf}'
            s = matrix['by_regime_dir_tf'].get(k)
            if s and s['n'] > 0:
                row_parts.append(f'{tf}:WR={s["wr"]:.0%}(n={s["n"]})')
        if row_parts:
            lines.append(f'  {rgm}×{dr}: ' + '  '.join(row_parts))
    lines.append('')

    # top coins per regime:dir
    lines.append('【各体制最优币种 Top5】')
    for rd_key, coins in matrix['top_coins_by_regime_dir'].items():
        if not coins:
            continue
        top5 = coins[:5]
        coins_str = '  '.join(f'{c["coin"]}:{c["wr"]:.0%}(n={c["n"]})' for c in top5)
        lines.append(f'  {rd_key}: {coins_str}')
    lines.append('')

    # 全局最优周期
    lines.append('【各周期全局 WR（所有币种合计）】')
    for tf in TIMEFRAMES:
        long_k  = f'LONG:{tf}'
        short_k = f'SHORT:{tf}'
        ls = matrix['by_dir_tf'].get(long_k,  {})
        ss = matrix['by_dir_tf'].get(short_k, {})
        if ls.get('n', 0) > 0 or ss.get('n', 0) > 0:
            lines.append(
                f'  {tf}: '
                f'LONG  WR={ls.get("wr",0):.0%} n={ls.get("n",0)}  '
                f'SHORT WR={ss.get("wr",0):.0%} n={ss.get("n",0)}'
            )

    return '\n'.join(lines)


# ── CLI 入口 ──────────────────────────────────────────────────────
if __name__ == '__main__':
    print('加载方仓案例...')
    t0 = time.time()
    cases = load_all_cases()
    print(f'已加载 {len(cases):,} 条案例  ({time.time()-t0:.1f}s)')

    print('蒸馏中...')
    t1 = time.time()
    matrix = distill(cases)
    print(f'蒸馏完成  ({time.time()-t1:.1f}s)')

    # 写主矩阵
    out_main = _DATA / 'brahma_experience_matrix.json'
    out_main.write_text(json.dumps(matrix, ensure_ascii=False))
    size_kb = out_main.stat().st_size / 1024
    print(f'✅ 写入 {out_main.name}  ({size_kb:.1f} KB)')

    # 写单币WR速查表
    coin_table = build_coin_wr_table(matrix)
    out_coin = _DATA / 'brahma_wr_by_coin.json'
    out_coin.write_text(json.dumps(coin_table, ensure_ascii=False))
    print(f'✅ 写入 {out_coin.name}  ({out_coin.stat().st_size/1024:.1f} KB)')

    # 写人工报告
    report = build_report(matrix)
    out_report = _DATA / 'brahma_phase3_report.txt'
    out_report.write_text(report)
    print(f'✅ 写入 {out_report.name}')

    print()
    print(report)
