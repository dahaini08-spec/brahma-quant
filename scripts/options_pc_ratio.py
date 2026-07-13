#!/usr/bin/env python3
"""
P1c: 期权P/C比实时 — options_pc_ratio.py
设计院 v5.6 | 2026-07-13

数据源: Deribit 公开API (无需API Key)
输出: P/C OI比、IV偏度、到期结构、梵天评分贡献
"""
import sys, os, requests, json, time
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))


def get_options_pc(currency: str = 'BTC') -> dict:
    """Deribit期权P/C比 + IV结构"""
    try:
        # 1. 获取所有期权汇总（按到期日分组）
        r = requests.get(
            'https://deribit.com/api/v2/public/get_book_summary_by_currency',
            params={'currency': currency, 'kind': 'option'},
            timeout=12
        ).json()

        if 'result' not in r:
            return {'error': r.get('error', 'unknown'), 'currency': currency}

        opts = r['result']

        # 2. 按到期日分组统计
        expiry_data = defaultdict(lambda: {'call_oi': 0, 'put_oi': 0, 'call_vol': 0, 'put_vol': 0})

        total_call_oi = total_put_oi = 0.0
        total_call_vol = total_put_vol = 0.0
        near_expiry_pc = None  # 最近到期批次P/C比

        for opt in opts:
            name   = opt.get('instrument_name', '')
            oi     = float(opt.get('open_interest', 0))
            vol    = float(opt.get('volume', 0) or 0)
            # 解析名称: BTC-13JUL26-65000-C
            parts = name.split('-')
            if len(parts) < 4: continue
            kind   = parts[-1]  # C or P
            expiry = parts[1]   # 13JUL26

            if kind == 'C':
                total_call_oi += oi; total_call_vol += vol
                expiry_data[expiry]['call_oi'] += oi
                expiry_data[expiry]['call_vol'] += vol
            elif kind == 'P':
                total_put_oi += oi; total_put_vol += vol
                expiry_data[expiry]['put_oi'] += oi
                expiry_data[expiry]['put_vol'] += vol

        # 整体P/C比
        pc_oi_ratio  = round(total_put_oi  / total_call_oi,  3) if total_call_oi  > 0 else 0
        pc_vol_ratio = round(total_put_vol / total_call_vol, 3) if total_call_vol > 0 else 0

        # 最近3个到期批次P/C
        sorted_expiries = sorted(expiry_data.keys())[:3]
        near_expiry_ratios = {}
        for exp in sorted_expiries:
            d = expiry_data[exp]
            ratio = round(d['put_oi'] / d['call_oi'], 3) if d['call_oi'] > 0 else 0
            near_expiry_ratios[exp] = ratio

        # 近期第一个到期P/C
        near_expiry_pc = near_expiry_ratios.get(sorted_expiries[0]) if sorted_expiries else None

        # 3. 语义解读
        def interpret_pc(ratio):
            if ratio is None: return '数据不足'
            if ratio > 1.5:   return '🔴极度悲观(超过1.5，市场恐慌做保护)'
            if ratio > 1.2:   return '🟡偏空情绪(>1.2，保护性Put增多)'
            if ratio > 0.8:   return '⚪中性(0.8~1.2，多空均衡)'
            if ratio > 0.5:   return '🟢偏多(0.5~0.8，Call需求旺盛)'
            return '🔥极度贪婪(<0.5，市场超乐观)'

        interp_oi  = interpret_pc(pc_oi_ratio)
        interp_vol = interpret_pc(pc_vol_ratio)

        # 4. 梵天评分贡献
        # P/C OI < 0.7 → 市场偏多 → 多头环境加分
        # P/C OI > 1.3 → 极度恐慌 → 可能是底部信号（逆向指标）
        pc_score = 0
        if pc_oi_ratio < 0.7:   pc_score += 8   # 期权市场乐观
        elif pc_oi_ratio < 0.9: pc_score += 4
        elif pc_oi_ratio > 1.5: pc_score += 6   # 逆向信号：极度恐慌往往是底
        elif pc_oi_ratio > 1.2: pc_score += 2

        result = {
            'currency'          : currency,
            'total_call_oi'     : round(total_call_oi, 2),
            'total_put_oi'      : round(total_put_oi, 2),
            'pc_oi_ratio'       : pc_oi_ratio,
            'pc_vol_ratio'      : pc_vol_ratio,
            'interpretation_oi' : interp_oi,
            'interpretation_vol': interp_vol,
            'near_expiry_ratios': near_expiry_ratios,
            'near_expiry_pc'    : near_expiry_pc,
            'pc_score'          : pc_score,
            'ts'                : time.time(),
        }

        cache = BASE / 'data' / f'options_pc_{currency}.json'
        cache.write_text(json.dumps(result, indent=2))
        return result

    except Exception as e:
        return {'error': str(e), 'currency': currency}


def format_report(r: dict) -> str:
    if 'error' in r:
        return f'⚠️ options_pc error: {r["error"]}'
    lines = [
        f'📊 期权P/C比 — {r["currency"]}',
        f'  全量 OI P/C  : {r["pc_oi_ratio"]}  {r["interpretation_oi"]}',
        f'  全量 Vol P/C : {r["pc_vol_ratio"]}  {r["interpretation_vol"]}',
        f'  Call OI: {r["total_call_oi"]:,.0f}  Put OI: {r["total_put_oi"]:,.0f}',
        f'  近期到期P/C:',
    ]
    for exp, ratio in r.get('near_expiry_ratios', {}).items():
        lines.append(f'    {exp}: {ratio}')
    lines.append(f'  梵天评分贡献: +{r["pc_score"]}')
    return '\n'.join(lines)


if __name__ == '__main__':
    currencies = sys.argv[1:] if len(sys.argv) > 1 else ['BTC', 'ETH']
    for cur in currencies:
        r = get_options_pc(cur)
        print(format_report(r))
        print()
