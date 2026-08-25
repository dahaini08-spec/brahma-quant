"""
brahma_pipeline.py — 梵天全能力分析强制流水线
══════════════════════════════════════════════
设计院 2026-08-25 苏摩111 封印

使命：
  苏摩每次在对话框说"分析BTC/ETH"，必须走这里。
  6步全部走完，不跳步，不用旧数据，不AI主观乱输出。

调用方式：
  from brahma_pipeline import run_full_pipeline
  output = run_full_pipeline('BTCUSDT')
  print(output)  # 直接打印VIP卡片格式

6步强制流水线：
  Step1: 实时数据拉取（API调用，price_ts强制）
  Step2: 梵天35维评分（brahma_core.analyze唯一入口）
  Step3: AI议会裁决（llm_council 3专家并行）
  Step4: 战场预判（price_zone_engine）
  Step5: 叙事层（narrative_engine + 极端事件）
  Step6: 标准化输出（VIP卡片格式，带时间戳）
"""

import os
import sys
import time
import json
import traceback
from datetime import datetime, timezone

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)
sys.path.insert(0, os.path.join(_BASE, '..', 'scripts'))

# ── 步骤超时保护 ────────────────────────────────────────────────────
STEP_TIMEOUT = {
    'step1': 10,   # 数据拉取
    'step2': 45,   # 35维评分
    'step3': 20,   # AI议会
    'step4': 10,   # 战场预判
    'step5': 10,   # 叙事层
}

MAX_DATA_AGE_SEC = 300  # 超过5分钟的数据拒绝输出


# ── Step1: 实时数据拉取 ──────────────────────────────────────────────
def step1_fetch_realtime(symbol: str) -> dict:
    """
    强制从API拉取实时数据，写入price_ts。
    返回 {price, price_ts, data_age_sec, ok, error}
    """
    t0 = time.time()
    try:
        from brahma_bus import get_price, get_funding, get_oi
        price = get_price(symbol)
        if not price or price <= 0:
            return {'ok': False, 'error': f'价格拉取失败: {price}', 'price': 0, 'price_ts': t0}
        price_ts = time.time()
        age = price_ts - t0
        fr = None
        oi = None
        try:
            fr = get_funding(symbol)
        except Exception:
            pass
        try:
            oi_data = get_oi(symbol)
            oi = oi_data.get('openInterest') if isinstance(oi_data, dict) else None
        except Exception:
            pass
        return {
            'ok': True,
            'symbol': symbol,
            'price': price,
            'price_ts': price_ts,
            'data_age_sec': age,
            'funding_rate': fr,
            'open_interest': oi,
            'fetch_ms': int((time.time() - t0) * 1000),
        }
    except Exception as e:
        return {'ok': False, 'error': str(e), 'price': 0, 'price_ts': t0}


# ── Step2: 梵天35维评分 ─────────────────────────────────────────────
def step2_brahma_analyze(symbol: str) -> dict:
    """
    调用brahma_core.analyze()唯一入口，返回完整result。
    验证price_ts存在且新鲜。
    """
    try:
        import brahma_core
        result = brahma_core.analyze(symbol)

        # 验证price_ts
        ts = result.get('price_ts')
        if ts is None:
            result['_step2_warn'] = 'price_ts缺失，数据来源不可验证'
        else:
            age = time.time() - ts
            if age > MAX_DATA_AGE_SEC:
                return {
                    'ok': False,
                    'error': f'Step2数据过期 {age:.0f}s（最大允许{MAX_DATA_AGE_SEC}s），拒绝输出',
                    '_step': 'step2',
                }
            result['data_age_sec'] = age

        result['ok'] = True
        return result
    except Exception as e:
        return {'ok': False, 'error': f'Step2异常: {e}', '_step': 'step2',
                '_tb': traceback.format_exc()[-300:]}


# ── Step3: AI议会裁决 ────────────────────────────────────────────────
def step3_council(score_result: dict) -> dict:
    """
    调用llm_council_bridge.review()，3专家并行裁决。
    返回 {final_adj, votes, council_summary}
    """
    try:
        from llm_council_bridge import review as council_review
        council_result = council_review(score_result)
        return {
            'ok': True,
            'final_adj': council_result.get('final_adj', 0),
            'votes': council_result.get('votes', {}),
            'council_summary': council_result.get('summary', ''),
            'raw': council_result,
        }
    except ImportError:
        return {'ok': False, 'error': 'llm_council_bridge不可用', 'final_adj': 0}
    except Exception as e:
        return {'ok': False, 'error': f'Step3异常: {e}', 'final_adj': 0}


# ── Step4: 战场预判 ──────────────────────────────────────────────────
def step4_price_zone(symbol: str) -> dict:
    """
    调用price_zone_engine.calc_zones() + format_zone_report()。
    返回高空区/低多区/路径概率。
    """
    try:
        from price_zone_engine import calc_zones, format_zone_report
        zones = calc_zones(symbol)
        report = format_zone_report(zones, compact=True)
        return {'ok': True, 'zone': zones, 'summary': report}
    except ImportError:
        return {'ok': False, 'error': 'price_zone_engine不可用', 'zone': {}}
    except Exception as e:
        return {'ok': False, 'error': f'Step4异常: {e}', 'zone': {}}


# ── Step5: 叙事层 ────────────────────────────────────────────────────
def step5_narrative(symbol: str, score_result: dict) -> dict:
    """
    调用narrative_engine + extreme_event_db，返回宏观叙事+极端事件预警。
    """
    output = {'ok': True, 'sentiment_score': None, 'extreme_risk': None, 'crowd': None}
    try:
        from narrative_engine import get_narrative_score, get_crowd_sentiment
        output['sentiment_score'] = get_narrative_score(symbol)
        output['crowd'] = get_crowd_sentiment(symbol)
    except Exception as e:
        output['narrative_err'] = str(e)

    try:
        from extreme_event_db import get_extreme_risk_note
        regime = score_result.get('regime', 'UNKNOWN')
        price = score_result.get('price', 0)
        output['extreme_risk'] = get_extreme_risk_note(symbol, price, regime)
    except Exception as e:
        output['extreme_err'] = str(e)

    return output


# ── Step6: 标准化输出 ────────────────────────────────────────────────
def step6_format_output(symbol: str, s1: dict, s2: dict, s3: dict,
                        s4: dict, s5: dict) -> str:
    """
    把6步结果组装成VIP卡片格式，带price_ts时间戳。
    """
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    price = s2.get('price', s1.get('price', 0))
    regime = s2.get('regime', 'UNKNOWN')
    score = s2.get('score', 0)
    decision = s2.get('decision', {})
    action = decision.get('action', 'SKIP') if isinstance(decision, dict) else 'SKIP'
    reason = decision.get('reason', '') if isinstance(decision, dict) else str(decision)

    price_ts = s2.get('price_ts')
    data_age = s2.get('data_age_sec', -1)
    ts_str = (datetime.fromtimestamp(price_ts, tz=timezone.utc).strftime('%H:%M:%S UTC')
              if price_ts else '未知')

    # 议会
    final_adj = s3.get('final_adj', 0)
    adj_str = f'{final_adj:+.1f}' if final_adj else '±0'

    # 战场
    zone = s4.get('zone', {})
    zone_str = s4.get('summary', zone.get('summary', '战场预判不可用') if isinstance(zone, dict) else str(zone))

    # 叙事
    sentiment = s5.get('sentiment_score')
    extreme = s5.get('extreme_risk', '')
    crowd = s5.get('crowd', {})

    # fangcang
    fc = s2.get('fangcang', {})
    fc_hint = fc.get('signal_hint', 'N/A') if isinstance(fc, dict) else 'N/A'
    fc_p_up = fc.get('prob_matrix', {}).get('p_up', 'N/A') if isinstance(fc, dict) else 'N/A'

    # 决策动作符号
    action_symbol = {'EXECUTE': '🟢', 'ALERT': '🟡', 'WATCH': '🔵', 'SKIP': '⚫'}.get(action, '⚫')

    lines = [
        f'## 🔱 梵天全能力分析 | {symbol} | {now_utc}',
        f'> 数据时间: {ts_str} | 新鲜度: {data_age:.1f}s | 全6步流水线 ✅',
        '',
        f'### 📊 {symbol} · ${price:,.2f} | {regime}',
        '',
        '| 维度 | 数值 |',
        '|------|------|',
        f'| 梵天评分 | {score:.1f} |',
        f'| 议会调整 | {adj_str} |',
        f'| 方仓信号 | {fc_hint} (p_up={fc_p_up}) |',
    ]

    if sentiment:
        fg = sentiment.get('fg_index', 'N/A')
        lines.append(f'| FG指数 | {fg} |')

    if crowd:
        lsr = crowd.get('lsr', 'N/A')
        lines.append(f'| LSR多空比 | {lsr} |')

    lines += ['', '### 🗺️ 战场预判', zone_str, '']

    if extreme:
        lines += [f'### ⚠️ 极端事件预警', extreme, '']

    lines += [
        f'### {action_symbol} 决策: {action}',
        f'> {reason}' if reason else '',
        '',
        '---',
        f'*梵天全能力流水线 · price_ts={ts_str} · 6步全通过*',
    ]

    return '\n'.join(l for l in lines if l is not None)


# ── 主入口 ───────────────────────────────────────────────────────────
def run_full_pipeline(symbol: str) -> str:
    """
    梵天全能力分析唯一入口。
    6步强制走完，任何步骤失败明确说明原因。
    """
    sym = symbol.upper()
    if not sym.endswith('USDT'):
        sym = sym + 'USDT'

    errors = []

    # Step1
    s1 = step1_fetch_realtime(sym)
    if not s1.get('ok'):
        return f'❌ Step1失败（实时数据拉取）: {s1.get("error")}\n分析中止，不用旧数据。'

    # Step2（核心，失败则中止）
    s2 = step2_brahma_analyze(sym)
    if not s2.get('ok'):
        return f'❌ Step2失败（35维评分）: {s2.get("error")}\n分析中止。'

    # Step3（失败可降级，不中止）
    s3 = step3_council(s2)
    if not s3.get('ok'):
        errors.append(f'Step3降级: {s3.get("error")}')

    # Step4（失败可降级）
    s4 = step4_price_zone(sym)
    if not s4.get('ok'):
        errors.append(f'Step4降级: {s4.get("error")}')

    # Step5（失败可降级）
    s5 = step5_narrative(sym, s2)

    # Step6：标准输出
    output = step6_format_output(sym, s1, s2, s3, s4, s5)

    if errors:
        output += '\n\n> ⚠️ 部分步骤降级: ' + ' | '.join(errors)

    return output


# ── CLI ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    print(run_full_pipeline(sym))
