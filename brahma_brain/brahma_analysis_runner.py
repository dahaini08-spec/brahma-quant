"""
brahma_analysis_runner.py — 梵天分析唯一入口
设计院·达摩院 固化封印 2026-06-30

═══════════════════════════════════════════════════
核心原则：
  1. 单一入口  — 所有分析必须调用此文件，禁止裸HTTP+inline计算
  2. 标准输出  — 所有结果必须经 extract_standard_fields() 归一化
  3. 并发执行  — 多标的统一走 brahma_parallel_engine.batch_analyze()
  4. 零临时代码 — 禁止在分析流程外新建HTTP调用或临时计算
═══════════════════════════════════════════════════

用法:
  # Python调用
  from brahma_brain.brahma_analysis_runner import run_analysis, run_batch
  result  = run_analysis('BTCUSDT')           # 单标的
  results = run_batch(['BTCUSDT', 'ETHUSDT'])  # 多标的并发

  # CLI调用
  python brahma_analysis_runner.py BTCUSDT ETHUSDT [--card] [--full]
"""

import sys
import os
import time
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, '..'))

# ── 唯一数据入口（封印）────────────────────────────────────────
from brahma_brain.brahma_core import analyze as _core_analyze
from brahma_brain.brahma_parallel_engine import batch_analyze as _batch_analyze
from brahma_brain.formatter import (
    format_report,
    format_standard_card,
    extract_standard_fields,
    STANDARD_FIELDS,
    build_output_tag,
    tag_is_valid_signal,
    tag_parse,
)


# ── 时机过滤层（设计院 2026-07-01 落地）──────────────────────────────────
try:
    from brahma_brain.timing_filter import evaluate_timing, format_timing_badge
    _TIMING_OK = True
except Exception:
    _TIMING_OK = False
# ── 系统配置（路由到正确线程）────────────────────────────────
try:
    sys.path.insert(0, os.path.join(BASE_DIR, '..', 'scripts'))
    from system_config import JARVIS_THREAD_ID, JARVIS_USER_ID
    _JARVIS_TARGET = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
except Exception:
    _JARVIS_TARGET = None

# ══════════════════════════════════════════════════════════════
# 封印：分析质量检查
# ══════════════════════════════════════════════════════════════

def _validate_result(r: dict) -> list:
    """
    检查 analyze() 结果是否包含所有必需字段
    返回缺失字段列表（空列表=全部完整）
    """
    if r.get('error'):
        return ['error: ' + str(r['error'])]

    f = extract_standard_fields(r)
    required = ['regime', 'score', 'direction', 'entry_lo', 'entry_hi', 'sl', 'tp1', 'rr']
    missing = [k for k in required if f.get(k) is None]
    return missing


# ══════════════════════════════════════════════════════════════
# 公开 API（所有调用者使用此接口）
# ══════════════════════════════════════════════════════════════

def run_analysis(symbol: str, deep: bool = True) -> dict:
    """
    单标的分析 — 封印版唯一入口

    规则：
      - 必须走 brahma_core.analyze(deep=True)
      - 不得绕过此函数直接调用 brahma_core
      - 返回值包含 _runner_meta 字段标记来源

    返回: analyze() 原始结果 + _runner_meta
    """
    t0 = time.time()
    sym = symbol.upper().replace('/','').replace('-','')
    if not sym.endswith('USDT'):
        sym = sym + 'USDT'

    result = _core_analyze(sym, deep=deep)
    missing = _validate_result(result)

    result['_runner_meta'] = {
        'runner_version': '1.0',
        'entry':          'brahma_analysis_runner.run_analysis',
        'symbol':         sym,
        'ts':             datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'elapsed':        round(time.time() - t0, 2),
        'fields_missing': missing,
        'fields_ok':      len(missing) == 0,
        'output_tag':     build_output_tag(result, source='RUNNER'),
    }
    return result


def run_batch(symbols: list, deep: bool = True) -> dict:
    """
    多标的并发分析 — 封印版唯一入口

    规则：
      - 必须走 brahma_parallel_engine.batch_analyze()
      - 4x加速，数据层通过 BrahmaBus 自动去重
      - 返回 {symbol: result} 字典，每个 result 含 _runner_meta

    返回: {symbol: run_analysis结果}
    """
    t0 = time.time()
    norm_syms = []
    for s in symbols:
        s = s.upper().replace('/','').replace('-','')
        if not s.endswith('USDT'):
            s = s + 'USDT'
        norm_syms.append(s)

    raw_results = _batch_analyze(norm_syms)
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    results = {}
    for sym, r in raw_results.items():
        missing = _validate_result(r)
        r['_runner_meta'] = {
            'runner_version': '1.0',
            'entry':          'brahma_analysis_runner.run_batch',
            'symbol':         sym,
            'ts':             ts,
            'elapsed':        round(time.time() - t0, 2),
            'fields_missing': missing,
            'fields_ok':      len(missing) == 0,
            'output_tag':     build_output_tag(r, source='RUNNER'),
        }
        results[sym] = r

    return results


def format_batch_report(results: dict, mode: str = 'card') -> str:
    """
    批量格式化输出 — 封印版标准报告
    每张卡片头部强制嵌入 BRAHMA 标签，防混淆防误识别

    mode:
      'card'  — 精简信号卡（推送用）
      'full'  — 完整分析报告（调试用）
    """
    lines = []
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines.append(f'🏛️ 梵天系统 · 实时分析  {ts}')
    lines.append('─' * 48)

    for sym in ['BTCUSDT', 'ETHUSDT'] + [s for s in results if s not in ('BTCUSDT','ETHUSDT')]:
        if sym not in results:
            continue
        r    = results[sym]
        meta = r.get('_runner_meta', {})
        tag  = meta.get('output_tag') or build_output_tag(r, source='RUNNER')

        # ── 标签头：每张卡片第一行必须是BRAHMA标签 ──
        lines.append(tag)

        if mode == 'card':
            lines.append(format_standard_card(r, ts=None))
        else:
            lines.append(format_report(r))

        # ── 时机过滤层注入（设计院 2026-07-01）───────────────────────
        if _TIMING_OK:
            try:
                f = extract_standard_fields(r)
                _timing = evaluate_timing(
                    symbol=sym,
                    signal_dir=f.get('direction', 'SHORT'),
                    score=f.get('score', 0),
                    grade=f.get('structure_grade', 70),
                    entry_lo=float(f.get('entry_lo', 0) or 0),
                    entry_hi=float(f.get('entry_hi', 0) or 0),
                    current_price=float(f.get('price', 0) or 0),
                    s23_p_up=r.get('s23_p_up', 0.5),
                    regime=f.get('regime', 'BEAR_TREND'),
                )
                lines.append(format_timing_badge(_timing))
                # 将timing注入result供下游使用
                r['_timing'] = _timing
            except Exception:
                pass

        # 质量警告
        missing = meta.get('fields_missing', [])
        if missing:
            lines.append(f'  ⚠️ 字段缺失: {missing}')

        # 非法输出盖识别器：标签不是SIG:RUNNER则加警告
        if not tag_is_valid_signal(tag):
            parsed = tag_parse(tag)
            lines.append(
                f'  🚨 警告: 此输出不是有效信号 — '
                f'level={parsed.get("level")} score={parsed.get("score")} '
                f'valid_sig={parsed.get("valid_sig")}'
            )

    return '\n'.join(lines)


def check_correlation_risk(results: dict) -> dict:
    """
    相关性去重防错（设计院 2026-07-01）

    BTC+ETH同向开仓时，实际风险敞口 = 1.85x BTC（相关系数≈0.85）
    输出建议：只开优先序更高的一个

    返回：
      risk_flag     : 是否存在相关高集中风险
      primary       : 建议操作的标的
      secondary     : 建议观望的标的
      note          : 说明
    """
    btc = results.get('BTCUSDT', {})
    eth = results.get('ETHUSDT', {})

    if not btc or not eth:
        return {'risk_flag': False, 'primary': None, 'secondary': None, 'note': '单标的无相关风险'}

    # 获取两者方向和score
    btc_dir = btc.get('signal_dir', '') or btc.get('confluence', {}).get('direction', '')
    eth_dir = eth.get('signal_dir', '') or eth.get('confluence', {}).get('direction', '')
    btc_score = float(btc.get('confluence', {}).get('total', 0) or btc.get('score', 0) or 0)
    eth_score = float(eth.get('confluence', {}).get('total', 0) or eth.get('score', 0) or 0)
    btc_valid = btc.get('valid', False)
    eth_valid = eth.get('valid', False)

    # 只有两者都有效且同向才存在相关风险
    if not (btc_valid and eth_valid and btc_dir and eth_dir and btc_dir == eth_dir):
        return {'risk_flag': False, 'primary': None, 'secondary': None,
                'note': f'无双开风险 (btc_valid={btc_valid} eth_valid={eth_valid} dir={btc_dir}/{eth_dir})'}

    # 同向双开：ETH得分高 AND BTC.D>54% → 优先ETH
    btc_dom = 55.4  # 当前实时値，稍后可动态拉取
    try:
        import requests as _rq
        cg = _rq.get('https://api.coingecko.com/api/v3/global', timeout=5).json()
        btc_dom = float(cg['data']['market_cap_percentage'].get('btc', 55.4))
    except Exception:
        pass

    if eth_score >= btc_score and btc_dom >= 54:
        primary = 'ETHUSDT'
        secondary = 'BTCUSDT'
        reason = f'ETH得分({eth_score:.0f})高于BTC({btc_score:.0f}) + BTC.D={btc_dom:.1f}%高位 → 优先ETH，BTC观望'
    else:
        primary = 'BTCUSDT'
        secondary = 'ETHUSDT'
        reason = f'BTC得分({btc_score:.0f})高或BTC.D不高 → 优先BTC'

    return {
        'risk_flag': True,
        'primary': primary,
        'secondary': secondary,
        'correlation': 0.85,
        'actual_exposure': '1.85x BTC风险',
        'note': f'❗ BTC/ETH同向{btc_dir}，实际风险敞口1.85x | {reason}',
        'btc_dom': btc_dom,
    }


# ══════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天分析唯一入口')
    parser.add_argument('symbols', nargs='*', default=['BTCUSDT', 'ETHUSDT'],
                        help='交易对列表（默认 BTC ETH）')
    parser.add_argument('--card', action='store_true', help='精简信号卡输出')
    parser.add_argument('--full', action='store_true', help='完整报告输出')
    parser.add_argument('--fields', action='store_true', help='仅输出标准字段')
    parser.add_argument('--validate', action='store_true', help='检查字段完整性')
    args = parser.parse_args()

    mode = 'full' if args.full else 'card'
    t0 = time.time()

    print(f'[Runner] 启动 | 标的: {args.symbols} | 模式: {mode}')
    print(f'[Runner] 入口: brahma_parallel_engine.batch_analyze (并发4x加速)')
    print()

    results = run_batch(args.symbols)
    total = round(time.time() - t0, 2)

    if args.fields:
        for sym, r in results.items():
            print(f'=== {sym} 标准字段 ===')
            f = extract_standard_fields(r)
            for k in STANDARD_FIELDS:
                v = f.get(k)
                status = '✅' if v is not None else '❌'
                print(f'  {status} {k}: {v}')
            print()
    elif args.validate:
        all_ok = True
        for sym, r in results.items():
            meta = r.get('_runner_meta', {})
            missing = meta.get('fields_missing', [])
            ok = meta.get('fields_ok', False)
            icon = '✅' if ok else '❌'
            print(f'{icon} {sym}: {"完整" if ok else "缺失=" + str(missing)}')
            if not ok:
                all_ok = False
        print()
        print(f'总结: {"全部完整 ✅" if all_ok else "有字段缺失 ❌"}  耗时 {total}s')
    else:
        print(format_batch_report(results, mode=mode))
        print()
        print(f'[Runner] 完成 | 耗时 {total}s | {len(results)} 标的')
        for sym, r in results.items():
            meta = r.get('_runner_meta', {})
            ok_icon = '✅' if meta.get('fields_ok') else '⚠️'
            print(f'  {ok_icon} {sym}: score={extract_standard_fields(r).get("score")} '
                  f'valid={extract_standard_fields(r).get("valid")} '
                  f'missing={meta.get("fields_missing",[])}')
