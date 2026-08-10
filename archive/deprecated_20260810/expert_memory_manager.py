#!/usr/bin/env python3
"""
expert_memory_manager.py — 六方专家记忆持久化管理器
P1落地 [苏摩111 2026-07-19]

六方专家身份：
  设计院        → 系统架构/全局视角
  量化工程师    → 数据统计/WR/评分校准
  合约交易员    → 执行决策/仓位管理
  SMC结构师     → 订单块/CHoCH/流动性结构
  衍生品专家    → OI/FR/期权/清算
  宏观分析师    → 宏观/DXY/体制判断

核心功能：
  1. 读取记忆 → 注入当前分析上下文（替代每次重新推理）
  2. 写入记忆 → 分析完成后自动更新专家洞察
  3. 衰减管理 → 过期洞察自动降权/清理
  4. 摘要生成 → 为LLM提供精简的专家记忆摘要（<500字符）
"""
import json, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
MEMORY_FILE = BASE / 'data' / 'expert_memory.json'

# 六方专家定义
EXPERTS = ['设计院', '量化工程师', '合约交易员', 'SMC结构师', '衍生品专家', '宏观分析师']

# 记忆衰减配置（小时）
MEMORY_TTL = {
    'SMC结构师':   {'structure': 6,   'ob_zones': 24,  'choch': 4   },
    '量化工程师':  {'wr_stats':  168,  'threshold': 72, 'regime': 24 },
    '合约交易员':  {'bias':      4,    'entry_zone': 2, 'sl_logic': 8},
    '衍生品专家':  {'fr':        2,    'oi_trend': 4,   'options': 8 },
    '宏观分析师':  {'dxy':       6,    'macro': 24,     'season': 168},
    '设计院':      {'arch':      168,  'system': 24,    'lesson': 720},
}

def load_memory() -> dict:
    """加载专家记忆，处理版本兼容"""
    if not MEMORY_FILE.exists():
        return _init_memory()
    try:
        d = json.loads(MEMORY_FILE.read_text())
        # 版本升级
        if d.get('version') != '2.0':
            d = _migrate_v1_to_v2(d)
        return d
    except Exception:
        return _init_memory()

def _init_memory() -> dict:
    """初始化v2.0结构"""
    now = datetime.now(timezone.utc).isoformat()
    return {
        'version': '2.0',
        'updated_at': now,
        'experts': {
            'SMC结构师': {
                'btc': {'structure_bias': 'NEUTRAL', 'key_ob': [], 'last_choch': None, 'fvg_zones': [], 'ts': 0},
                'eth': {'structure_bias': 'NEUTRAL', 'key_ob': [], 'last_choch': None, 'fvg_zones': [], 'ts': 0},
                'lessons': []
            },
            '量化工程师': {
                'wr_30d': None, 'wr_bear_short': None, 'wr_bull_long': None,
                'score_threshold': 155, 'grade_threshold': 80,
                'signal_count_30d': 0, 'avg_rr': None,
                'calibration_note': '样本积累中',
                'ts': 0
            },
            '合约交易员': {
                'position_bias': 'NEUTRAL', 'preferred_lev': 5,
                'current_regime_strategy': None,
                'recent_trades': [],  # 最近5笔交易结果
                'lessons': [],        # 实战教训
                'ts': 0
            },
            '衍生品专家': {
                'btc_fr_7d': None, 'eth_fr_7d': None,
                'oi_trend': 'NEUTRAL', 'oi_btc_24h_pct': None,
                'options_pcr': None, 'gex_zone': None,
                'liquidation_clusters': [],
                'ts': 0
            },
            '宏观分析师': {
                'dxy_trend': 'NEUTRAL', 'nq_trend': 'NEUTRAL',
                'btc_dominance': None, 'macro_regime': 'NEUTRAL',
                'seasonal_factor': None,
                'key_events': [],  # 未来3天重要事件
                'ts': 0
            },
            '设计院': {
                'system_health': 100, 'last_audit': None,
                'arch_notes': [],    # 架构洞察
                'risk_flags': [],    # 当前风险标记
                'ts': 0
            }
        },
        'cross_expert_consensus': {
            'btc_direction': None,
            'eth_direction': None,
            'confidence': 0,
            'last_update': 0,
            'agreement_count': 0  # 几个专家一致
        }
    }

def _migrate_v1_to_v2(old: dict) -> dict:
    """v1.0 → v2.0 平滑迁移"""
    new = _init_memory()
    # 迁移量化工程师数据
    qe = old.get('量化工程师', {})
    if qe:
        new['experts']['量化工程师']['score_threshold'] = qe.get('avg_score_threshold', 155)
        new['experts']['量化工程师']['calibration_note'] = qe.get('observation', '')

    # 迁移SMC结构师
    smc = old.get('SMC结构师', {})
    if smc.get('BTCUSDT'):
        b = smc['BTCUSDT']
        new['experts']['SMC结构师']['btc']['structure_bias'] = b.get('structure_bias', 'NEUTRAL')

    print('✅ expert_memory v1.0 → v2.0 迁移完成')
    return new

def save_memory(data: dict):
    """保存专家记忆"""
    data['updated_at'] = datetime.now(timezone.utc).isoformat()
    MEMORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def get_compact_summary() -> str:
    """
    生成精简专家记忆摘要，供LLM分析时注入（目标<400字符）
    替代每次重新推理，直接复用历史洞察
    """
    d = load_memory()
    experts = d.get('experts', {})
    now_ts = time.time()
    lines = []

    # SMC结构师
    smc = experts.get('SMC结构师', {})
    btc_b = smc.get('btc', {})
    if btc_b.get('structure_bias') and btc_b.get('structure_bias') != 'NEUTRAL':
        lines.append(f"SMC:BTC结构={btc_b['structure_bias']}")
    if btc_b.get('last_choch'):
        lines.append(f"CHoCH={btc_b['last_choch'][:10]}")

    # 量化工程师
    qe = experts.get('量化工程师', {})
    if qe.get('wr_30d'):
        lines.append(f"QE:WR30d={qe['wr_30d']:.1f}%({qe.get('signal_count_30d',0)}笔)")
    if qe.get('score_threshold') and qe['score_threshold'] != 155:
        lines.append(f"阈值={qe['score_threshold']}")

    # 合约交易员
    ct = experts.get('合约交易员', {})
    if ct.get('current_regime_strategy'):
        lines.append(f"交易员:{ct['current_regime_strategy'][:30]}")
    recent = ct.get('recent_trades', [])
    if recent:
        wins = sum(1 for t in recent[-5:] if t.get('pnl',0) > 0)
        lines.append(f"近5笔:{wins}/5胜")

    # 衍生品专家
    de = experts.get('衍生品专家', {})
    age_h = (now_ts - de.get('ts', 0)) / 3600
    if age_h < 4:
        if de.get('oi_trend') and de['oi_trend'] != 'NEUTRAL':
            lines.append(f"OI:{de['oi_trend']}")
        if de.get('btc_fr_7d') is not None:
            lines.append(f"FR7d={de['btc_fr_7d']:.4f}")

    # 宏观分析师
    ma = experts.get('宏观分析师', {})
    age_h = (now_ts - ma.get('ts', 0)) / 3600
    if age_h < 8:
        if ma.get('dxy_trend') and ma['dxy_trend'] != 'NEUTRAL':
            lines.append(f"DXY:{ma['dxy_trend']}")
        if ma.get('macro_regime') and ma['macro_regime'] != 'NEUTRAL':
            lines.append(f"宏观:{ma['macro_regime']}")

    # 六方共识
    cc = d.get('cross_expert_consensus', {})
    age_h = (now_ts - cc.get('last_update', 0)) / 3600
    if age_h < 2 and cc.get('btc_direction'):
        lines.append(f"共识:{cc['btc_direction']}({cc.get('agreement_count',0)}/6)")

    if not lines:
        return "专家记忆: 积累中，首次分析"

    summary = " | ".join(lines)
    return f"[专家记忆] {summary}"

def update_expert(expert: str, data: dict):
    """更新单个专家的记忆"""
    mem = load_memory()
    if expert not in mem.get('experts', {}):
        mem.setdefault('experts', {})[expert] = {}
    mem['experts'][expert].update(data)
    mem['experts'][expert]['ts'] = time.time()
    save_memory(mem)

def update_consensus(btc_dir: str = None, eth_dir: str = None,
                     agreement: int = 0, confidence: float = 0.0):
    """更新六方共识"""
    mem = load_memory()
    cc = mem.setdefault('cross_expert_consensus', {})
    if btc_dir: cc['btc_direction'] = btc_dir
    if eth_dir: cc['eth_direction'] = eth_dir
    cc['agreement_count'] = agreement
    cc['confidence'] = confidence
    cc['last_update'] = time.time()
    save_memory(mem)

def add_trade_result(symbol: str, direction: str, pnl: float,
                     score: float, regime: str, note: str = ''):
    """记录交易结果，供量化工程师统计WR"""
    mem = load_memory()
    ct = mem['experts'].setdefault('合约交易员', {})
    trades = ct.setdefault('recent_trades', [])
    trades.append({
        'symbol': symbol, 'direction': direction,
        'pnl': pnl, 'score': score, 'regime': regime,
        'note': note, 'ts': time.time()
    })
    # 只保留最近20笔
    ct['recent_trades'] = trades[-20:]
    # 更新WR统计
    qe = mem['experts'].setdefault('量化工程师', {})
    all_trades = ct['recent_trades']
    wins = sum(1 for t in all_trades if t.get('pnl', 0) > 0)
    qe['wr_30d'] = wins / len(all_trades) * 100 if all_trades else None
    qe['signal_count_30d'] = len(all_trades)
    save_memory(mem)
    print(f'✅ 交易记录已写入: {symbol} {direction} pnl={pnl:.3f} WR={qe["wr_30d"]:.1f}%')

if __name__ == '__main__':
    import sys
    if '--summary' in sys.argv:
        print(get_compact_summary())
    elif '--init' in sys.argv:
        d = _init_memory()
        save_memory(d)
        print('✅ expert_memory.json v2.0 初始化完成')
    elif '--migrate' in sys.argv:
        d = load_memory()
        save_memory(d)
        print('✅ 迁移完成')
    elif '--status' in sys.argv:
        d = load_memory()
        print(f"版本: {d.get('version')}")
        print(f"更新: {d.get('updated_at','')[:16]}")
        print(f"六方专家: {list(d.get('experts',{}).keys())}")
        print(f"摘要: {get_compact_summary()}")
    else:
        print("用法: --summary | --init | --migrate | --status")
