"""
rl_position_ab.py — RL仓位A/B分流控制器 v1.0
设计院 P3-B | 2026-07-08

架构:
  - A/B分流: 50%流量走RL建议仓位，50%走梵天标准仓位
  - 安全阀: RL建议偏离梵天score超过30分时自动降权
  - 渐进升级: 验证通过后从50%→75%→100%
  - 日志: 每笔交易记录A/B标记，便于效果对比
"""
import json, time, random, hashlib
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / 'data'
RL_LOG   = DATA_DIR / 'rl_ab_log.jsonl'
RL_STATE = DATA_DIR / 'rl_ab_state.json'

# A/B配置
AB_CONFIG = {
    'ab_ratio':       0.50,   # 50%流量走RL（苏摩授权初始值）
    'rl_max_dev':     30,     # RL建议score偏离梵天超过30→降权
    'rl_size_min':    0.5,    # RL最小仓位系数
    'rl_size_max':    1.5,    # RL最大仓位系数（防过度放大）
    'min_trades_promote': 20, # 升级到75%需要的最少验证交易数
    'win_rate_promote':   0.63,  # 升级条件: 胜率≥63%
    'phase': 'A/B_50',        # 当前阶段
}


def _load_state() -> dict:
    try:
        return json.loads(RL_STATE.read_text())
    except Exception:
        return {'phase': 'A/B_50', 'ab_ratio': 0.50,
                'rl_trades': 0, 'std_trades': 0,
                'rl_wins': 0, 'std_wins': 0}


def _save_state(state: dict):
    try:
        RL_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception:
        pass


def get_rl_suggestion(symbol: str, score: float, direction: str,
                       regime: str) -> dict:
    """
    获取RL模型的仓位建议
    当前: SHADOW模式用规则近似（torch RL模型接入后替换此函数）
    
    Returns: {'size_mult': float, 'confidence': float, 'source': str}
    """
    # SHADOW: 基于score和regime的规则近似（待torch RL替换）
    base_mult = 1.0

    # 高分信号放大
    if score >= 165:
        base_mult = 1.3
    elif score >= 155:
        base_mult = 1.15
    elif score >= 145:
        base_mult = 1.05
    elif score < 138:
        base_mult = 0.7  # 低分缩仓

    # 体制调整
    regime_adj = {
        'BEAR_RECOVERY': 1.1,  # WR=72.5%，略放大
        'BULL_TREND':    1.0,
        'CHOP_MID':      0.8,  # 震荡缩仓
        'BEAR_TREND':    0.6,  # 熊市严控
    }.get(regime, 1.0)

    size_mult = round(
        max(AB_CONFIG['rl_size_min'],
            min(AB_CONFIG['rl_size_max'], base_mult * regime_adj)),
        2
    )
    return {
        'size_mult':  size_mult,
        'confidence': 0.7,  # SHADOW固定置信度
        'source':     'shadow_rule',  # 'torch_rl' when model ready
    }


def decide_position_size(signal_id: str, symbol: str, score: float,
                          direction: str, regime: str,
                          std_nav_pct: float) -> dict:
    """
    A/B分流主入口
    
    Args:
        signal_id:    信号ID（用于确定性分流）
        std_nav_pct:  梵天标准仓位比例（如0.075）
    
    Returns:
        {'nav_pct': float, 'group': 'A'|'B', 'rl_mult': float, 'reason': str}
    """
    state = _load_state()
    ab_ratio = state.get('ab_ratio', 0.50)

    # 确定性分流（基于signal_id hash，保证可重现）
    hash_val = int(hashlib.md5(signal_id.encode()).hexdigest()[:8], 16)
    use_rl = (hash_val % 100) < int(ab_ratio * 100)
    group = 'B_RL' if use_rl else 'A_STD'

    if use_rl:
        rl = get_rl_suggestion(symbol, score, direction, regime)
        rl_mult = rl['size_mult']

        # 安全阀: 偏离梵天score对应乘数超过阈值→降权
        std_mult = 1.0  # 标准基准
        if abs(rl_mult - std_mult) > 0.4:
            rl_mult = std_mult + (0.4 if rl_mult > std_mult else -0.4)
            rl['source'] += '_clamped'

        nav_pct = round(std_nav_pct * rl_mult, 4)
        reason = f'RL[{rl["source"]}] mult×{rl_mult} nav_pct={nav_pct:.3f}'
    else:
        rl_mult = 1.0
        nav_pct = std_nav_pct
        reason = f'STD nav_pct={nav_pct:.3f}'

    # 记录日志
    log_entry = {
        'ts': time.time(),
        'signal_id': signal_id, 'symbol': symbol,
        'score': score, 'direction': direction, 'regime': regime,
        'group': group, 'rl_mult': rl_mult,
        'std_nav_pct': std_nav_pct, 'final_nav_pct': nav_pct,
        'ab_ratio': ab_ratio,
    }
    try:
        with open(RL_LOG, 'a') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    except Exception:
        pass

    return {
        'nav_pct': nav_pct,
        'group':   group,
        'rl_mult': rl_mult,
        'reason':  reason,
    }


def evaluate_ab_performance() -> dict:
    """评估A/B两组历史表现，决定是否升级ab_ratio"""
    if not RL_LOG.exists():
        return {'status': 'no_data'}

    logs = []
    with open(RL_LOG) as f:
        for line in f:
            try:
                logs.append(json.loads(line.strip()))
            except Exception:
                pass

    if len(logs) < 10:
        return {'status': 'insufficient_data', 'n': len(logs)}

    # 统计各组
    a_logs = [l for l in logs if l.get('group') == 'A_STD']
    b_logs = [l for l in logs if l.get('group') == 'B_RL']

    state = _load_state()
    current_phase = state.get('phase', 'A/B_50')

    result = {
        'status':    'evaluated',
        'phase':     current_phase,
        'A_count':   len(a_logs),
        'B_count':   len(b_logs),
        'ab_ratio':  state.get('ab_ratio', 0.50),
    }

    # 升级条件检查
    min_trades = AB_CONFIG['min_trades_promote']
    if (len(b_logs) >= min_trades and
            state.get('rl_wins', 0) / max(state.get('rl_trades', 1), 1) >= AB_CONFIG['win_rate_promote']):
        if current_phase == 'A/B_50':
            state['phase']    = 'A/B_75'
            state['ab_ratio'] = 0.75
            _save_state(state)
            result['promoted'] = True
            result['new_phase'] = 'A/B_75'
        elif current_phase == 'A/B_75':
            state['phase']    = 'RL_FULL'
            state['ab_ratio'] = 1.0
            _save_state(state)
            result['promoted'] = True
            result['new_phase'] = 'RL_FULL'

    return result


if __name__ == '__main__':
    # 测试
    r = decide_position_size('test_sig_001', 'ETHUSDT', 165.0, 'LONG', 'BEAR_RECOVERY', 0.075)
    print(f"ETH BEAR_RECOVERY 165分: group={r['group']} rl_mult={r['rl_mult']} nav_pct={r['nav_pct']:.3f}")
    r2 = decide_position_size('test_sig_002', 'BTCUSDT', 110.0, 'LONG', 'BULL_TREND', 0.05)
    print(f"BTC BULL_TREND 110分: group={r2['group']} rl_mult={r2['rl_mult']} nav_pct={r2['nav_pct']:.3f}")
    print(f"\nA/B性能评估: {evaluate_ab_performance()}")
