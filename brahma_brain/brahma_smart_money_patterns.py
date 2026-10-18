#!/usr/bin/env python3
"""
brahma_smart_money_patterns.py — Opus 5.5主力手法识别模式库
[封印 2026-10-18 苏摩111] claude-quickstarts逆向工程能力落地

参考: Opus 5.5 "reverse engineering" 能力
功能: 识别主力6种经典手法，向量化存入方仓决策依据

主力手法库（6种）:
  P1 逼空行情   — OI减少+价格急涨+CVD短暂正→急速反转
  P2 猎杀散户   — LSR>2.5+OI增+价格下跌触清算池
  P3 吸筹建仓   — OI持续增+CVD正+价格横盘低位
  P4 派货出逃   — OI减+CVD转负+价格高位横盘
  P5 GEX钉价   — 价格在ZF±0.3%内反复震荡
  P6 假突破诱多 — 价格穿越阻力+成交量大+快速回落
"""
import json, time
from pathlib import Path

BASE = Path(__file__).parent

# ── 模式定义 ──────────────────────────────────────────────────────────────

SMART_MONEY_PATTERNS = {
    'P1_SQUEEZE': {
        'name': '逼空行情',
        'desc': '空头被迫平仓引发的被动拉升，无真实多头入场',
        'signals': {
            'oi_dir': 'LONG_UNWIND',   # OI减少=空头平仓
            'taker_min': 1.5,          # Taker主动买>1.5
            'lsr_max': 1.8,            # LSR不极端（不是真多头）
            'price_surge_pct': 1.5,    # 短期涨幅>1.5%
        },
        'implication': '快速冲高后急速反转，不追多，等回落做空',
        'historical_reversal_rate': 0.72,
    },
    'P2_HUNT': {
        'name': '猎杀散户',
        'desc': '主力向下猎杀散户多单止损池',
        'signals': {
            'lsr_min': 2.5,            # 全局LSR>2.5（散户极偏多）
            'oi_dir': 'SHORT_BUILD',   # OI增+主力建空
            'dist_to_liq_pct_max': 2.0, # 距清算池<2%
            'taker_max': 0.9,          # Taker主动卖>主动买
        },
        'implication': '清算池将被穿越，等穿越后反手做多（D计划）',
        'historical_bounce_rate': 0.78,
    },
    'P3_ACCUMULATE': {
        'name': '吸筹建仓',
        'desc': '主力在低位慢慢建多仓，价格横盘不跌',
        'signals': {
            'oi_dir': 'SHORT_BUILD',   # OI增（主力在建仓）
            'price_range_pct_max': 1.5, # 24H振幅<1.5%（横盘）
            'taker_min': 1.0,          # Taker买>卖
            'rsi_max': 45,             # RSI偏低（低位）
        },
        'implication': '等待方向确认后跟随做多，止损低位支撑下方',
        'breakout_probability': 0.65,
    },
    'P4_DISTRIBUTE': {
        'name': '派货出逃',
        'desc': '主力在高位慢慢卖出，价格横盘不涨',
        'signals': {
            'oi_dir': 'LONG_UNWIND',   # OI减（主力在平仓）
            'price_range_pct_max': 1.5, # 24H振幅<1.5%（横盘）
            'taker_max': 0.9,          # Taker卖>买（主动卖出）
            'rsi_min': 60,             # RSI偏高（高位）
        },
        'implication': '不追多，等确认下跌后做空',
        'breakdown_probability': 0.68,
    },
    'P5_GEX_PIN': {
        'name': 'GEX钉价',
        'desc': '做市商在ZF附近反复对冲，价格被钉住',
        'signals': {
            'dist_to_zf_pct_max': 0.3, # 距ZF<0.3%
            'iv_min': 30,              # IV不低（做市商活跃）
            'price_range_pct_max': 1.0, # 短期振幅小
        },
        'implication': '等待突破ZF方向，突破后方向延续性强',
        'breakout_follow_rate': 0.71,
    },
    'P6_FAKE_BREAKOUT': {
        'name': '假突破诱多',
        'desc': '价格穿越关键阻力后快速回落，诱散户追涨',
        'signals': {
            'taker_on_breakout_min': 1.8, # 突破时Taker买>1.8
            'oi_on_breakout': 'LONG_UNWIND', # OI反而减少（真多头没进）
            'reversal_within_bars': 3,    # 3根K线内回落
        },
        'implication': '假突破=做空机会，等回落确认后入场',
        'trap_rate': 0.69,
    },
}

# ── 实时模式识别 ──────────────────────────────────────────────────────────

def detect_patterns(sym: str, data: dict) -> list:
    """
    输入: Flows或state数据字典
    输出: 匹配的主力手法列表（按置信度排序）
    """
    matched = []
    price  = data.get('price', 0)
    rsi    = data.get('rsi_1h', 50)
    oi_dir = data.get('oi_dir', 'FLAT')
    lsr    = data.get('lsr', 1)
    taker  = data.get('taker', 1)
    zf     = data.get('btc_zf' if sym=='BTC' else 'eth_zf', 0) or data.get('zero_flip', 0)
    liq_dn = data.get('liq_long', 0)
    chg    = abs(data.get('chg', 0))
    iv     = data.get('iv_rank', 50)

    # P1 逼空行情
    if oi_dir == 'LONG_UNWIND' and taker >= 1.5 and lsr <= 1.8 and chg >= 1.5:
        matched.append(('P1_SQUEEZE', 0.75,
            f"OI减少+Taker={taker:.2f}+价格涨{chg:.1f}%"))

    # P2 猎杀散户
    dist_liq = abs(price - liq_dn) / price * 100 if price > 0 and liq_dn > 0 else 99
    if lsr >= 2.5 and oi_dir in ('SHORT_BUILD', 'FLAT') and dist_liq <= 2.0 and taker <= 0.9:
        conf = 0.85 if lsr >= 3.0 else 0.72
        matched.append(('P2_HUNT', conf,
            f"LSR={lsr:.3f}+距清算池{dist_liq:.1f}%+Taker={taker:.2f}"))

    # P3 吸筹建仓
    if oi_dir == 'SHORT_BUILD' and taker >= 1.0 and rsi <= 45 and chg <= 1.5:
        matched.append(('P3_ACCUMULATE', 0.62,
            f"OI增+RSI={rsi:.1f}低位+Taker买=主导"))

    # P4 派货出逃
    if oi_dir == 'LONG_UNWIND' and taker <= 0.9 and rsi >= 60 and chg <= 1.5:
        matched.append(('P4_DISTRIBUTE', 0.65,
            f"OI减+RSI={rsi:.1f}高位+Taker卖=主导"))

    # P5 GEX钉价
    dist_zf = abs(price - zf) / price * 100 if price > 0 and zf > 0 else 99
    if dist_zf <= 0.3 and iv >= 30 and chg <= 1.0:
        matched.append(('P5_GEX_PIN', 0.70,
            f"距ZF={dist_zf:.2f}%+IV={iv:.0f}%"))

    # 按置信度排序
    matched.sort(key=lambda x: x[1], reverse=True)
    return matched

def format_patterns(sym: str, matched: list) -> str:
    if not matched:
        return f'{sym}: 未识别到明确主力手法（等待信号）'
    lines = [f'🔍 {sym} 主力手法识别:']
    for pid, conf, evidence in matched:
        p = SMART_MONEY_PATTERNS[pid]
        lines.append(
            f"  {p['name']}（置信度{conf:.0%}）\n"
            f"  证据: {evidence}\n"
            f"  含义: {p['implication']}"
        )
    return '\n'.join(lines)

def save_pattern_to_fangcang(sym: str, pattern_id: str, data: dict):
    """将识别到的主力手法记录写入方仓案例库扩展"""
    try:
        import fangcang_engine as fe
        p = SMART_MONEY_PATTERNS.get(pattern_id, {})
        record = {
            'ts': time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime()),
            'sym': sym,
            'pattern': pattern_id,
            'pattern_name': p.get('name', ''),
            'price': data.get('price', 0),
            'rsi_1h': data.get('rsi_1h', 50),
            'lsr': data.get('lsr', 1),
            'taker': data.get('taker', 1),
            'oi_dir': data.get('oi_dir', 'FLAT'),
        }
        log_f = BASE / 'data' / 'smart_money_patterns.jsonl'
        with open(log_f, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        return True
    except Exception:
        return False

if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(BASE / 'brahma_brain'))
    sys.path.insert(0, str(BASE / 'scripts'))

    # 从state读取当前数据
    for sym in ['BTC', 'ETH']:
        sf = BASE / 'data' / f'brahma_state_{sym.lower()}.json'
        if sf.exists():
            d = json.load(open(sf))
            matched = detect_patterns(sym, d)
            print(format_patterns(sym, matched))
            print()
            for pid, conf, ev in matched:
                save_pattern_to_fangcang(sym, pid, d)
