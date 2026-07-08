"""
layer_9_12.py — 梵天12层断路器 Layer 9~12
设计院 2026-07-08 | P2-1 外部审计改善方案

Layer 9:  强平墙距离保护（<3% → 仓位×0.5）
Layer 10: 资金费率极端层（FR > 0.1%/8H → 禁新多）
Layer 11: 系统健康联动（self_heal DEGRADED → 暂停执行）
Layer 12: LLM议会置信度（分歧>40% → SKIP）
"""
import json, time, pathlib, sys
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Layer 9: 强平墙距离保护 ────────────────────────────────────────
def layer9_liquidation_wall(symbol: str, price: float, direction: str,
                             notional: float) -> dict:
    """
    检测强平墙距离，<3%时仓位减半
    返回: {'pass': bool, 'discount': float, 'reason': str}
    """
    try:
        from scripts.auto_executor import _signed
        # 获取近期强平数据（通过 /futures/data/allForceOrders 估算）
        liq_r = _signed('GET', '/futures/data/allForceOrders',
                         {'symbol': symbol, 'period': '1h', 'limit': 5})
        if not isinstance(liq_r, list) or not liq_r:
            return {'pass': True, 'discount': 1.0, 'reason': 'Layer9: 无强平数据，放行'}
        
        # 估算强平密集区（最近5次平均强平价）
        avg_liq_price = sum(float(l.get('averagePrice', price)) for l in liq_r) / len(liq_r)
        dist_pct = abs(price - avg_liq_price) / price * 100
        
        WALL_DANGER_PCT = 3.0  # 3%内为危险区
        if dist_pct < WALL_DANGER_PCT:
            return {
                'pass': True,  # 不拒绝，但减仓
                'discount': 0.5,
                'reason': f'Layer9: 强平墙距离{dist_pct:.1f}%<3% → 仓位×0.5'
            }
        return {'pass': True, 'discount': 1.0, 'reason': f'Layer9: 强平墙安全{dist_pct:.1f}%'}
    except Exception as e:
        return {'pass': True, 'discount': 1.0, 'reason': f'Layer9: 检查跳过({e})'}


# ── Layer 10: 资金费率极端层 ────────────────────────────────────────
def layer10_funding_rate(symbol: str, direction: str) -> dict:
    """
    FR > 0.1%/8H 时禁止新多（多头支付过高）
    返回: {'pass': bool, 'reason': str}
    """
    try:
        from scripts.auto_executor import _signed
        fr_r = _signed('GET', '/fapi/v1/fundingRate', {'symbol': symbol, 'limit': 1})
        if not fr_r:
            return {'pass': True, 'reason': 'Layer10: 无FR数据，放行'}
        
        fr_pct = float(fr_r[-1].get('fundingRate', 0)) * 100  # 转为百分比
        FR_DANGER = 0.1  # 0.1%/8H
        
        if direction in ('LONG', 'BUY') and fr_pct > FR_DANGER:
            return {
                'pass': False,
                'reason': f'Layer10: FR={fr_pct:.4f}%>{FR_DANGER}% 资金费率极端 禁新多'
            }
        return {'pass': True, 'reason': f'Layer10: FR={fr_pct:.4f}% 正常'}
    except Exception as e:
        return {'pass': True, 'reason': f'Layer10: 检查跳过({e})'}


# ── Layer 11: 系统健康联动 ────────────────────────────────────────
def layer11_system_health() -> dict:
    """
    self_heal DEGRADED 时暂停执行
    返回: {'pass': bool, 'reason': str}
    """
    try:
        health_log = DATA_DIR / 'brahma_event_log.jsonl'
        if not health_log.exists():
            return {'pass': True, 'reason': 'Layer11: 无健康日志，放行'}
        
        # 读最近一条健康事件
        with open(health_log) as f:
            lines = [l.strip() for l in f if l.strip()]
        
        if not lines:
            return {'pass': True, 'reason': 'Layer11: 无事件，放行'}
        
        last = json.loads(lines[-1])
        status = last.get('status', last.get('health_status', 'UNKNOWN'))
        age = time.time() - float(last.get('ts', time.time()))
        
        # 只有最近30分钟内的DEGRADED才拦截
        if 'DEGRADED' in str(status).upper() and age < 1800:
            return {
                'pass': False,
                'reason': f'Layer11: 系统健康DEGRADED({age/60:.0f}min前) → 暂停执行'
            }
        return {'pass': True, 'reason': f'Layer11: 系统健康{status}'}
    except Exception as e:
        return {'pass': True, 'reason': f'Layer11: 检查跳过({e})'}


# ── Layer 12: LLM议会置信度 ────────────────────────────────────────
def layer12_council_confidence(symbol: str) -> dict:
    """
    LLM议会分歧度>40% → SKIP（不拒绝，建议跳过）
    返回: {'pass': bool, 'confidence': float, 'reason': str}
    """
    try:
        council_log = DATA_DIR / 'llm_council_cache.json'
        if not council_log.exists():
            return {'pass': True, 'confidence': 0.5, 'reason': 'Layer12: 无议会缓存，放行'}
        
        with open(council_log) as f:
            cache = json.load(f)
        
        sym_data = cache.get(symbol, {})
        if not sym_data:
            return {'pass': True, 'confidence': 0.5, 'reason': 'Layer12: 无该标的议会记录，放行'}
        
        # 议会一致性评分
        votes = sym_data.get('votes', {})
        if not votes:
            return {'pass': True, 'confidence': 0.5, 'reason': 'Layer12: 无投票记录，放行'}
        
        total = sum(votes.values())
        if total == 0:
            return {'pass': True, 'confidence': 0.5, 'reason': 'Layer12: 投票为0，放行'}
        
        max_vote = max(votes.values())
        confidence = max_vote / total  # 最大一致性比例
        
        DISAGREEMENT_THRESHOLD = 0.60  # 置信度<60% = 分歧>40%
        if confidence < DISAGREEMENT_THRESHOLD:
            return {
                'pass': False,
                'confidence': confidence,
                'reason': f'Layer12: 议会分歧{(1-confidence)*100:.0f}%>40% confidence={confidence:.2f} → SKIP'
            }
        return {
            'pass': True,
            'confidence': confidence,
            'reason': f'Layer12: 议会一致 confidence={confidence:.2f}'
        }
    except Exception as e:
        return {'pass': True, 'confidence': 0.5, 'reason': f'Layer12: 检查跳过({e})'}


# ── 统一入口 ────────────────────────────────────────────────────────
def check_layer9_12(symbol: str, price: float, direction: str,
                     notional: float = 0) -> dict:
    """
    运行 Layer 9~12 全部检查
    返回: {'pass': bool, 'discount': float, 'reasons': list, 'blocked_by': str}
    """
    results = {}
    discount = 1.0
    blocked_by = ''
    reasons = []

    # Layer 9
    r9 = layer9_liquidation_wall(symbol, price, direction, notional)
    results['layer9'] = r9
    reasons.append(r9['reason'])
    discount *= r9.get('discount', 1.0)

    # Layer 10
    r10 = layer10_funding_rate(symbol, direction)
    results['layer10'] = r10
    reasons.append(r10['reason'])
    if not r10['pass']:
        blocked_by = 'layer10'

    # Layer 11
    r11 = layer11_system_health()
    results['layer11'] = r11
    reasons.append(r11['reason'])
    if not r11['pass'] and not blocked_by:
        blocked_by = 'layer11'

    # Layer 12
    r12 = layer12_council_confidence(symbol)
    results['layer12'] = r12
    reasons.append(r12['reason'])
    if not r12['pass'] and not blocked_by:
        blocked_by = 'layer12'

    return {
        'pass': not blocked_by,
        'discount': round(discount, 2),
        'blocked_by': blocked_by,
        'reasons': reasons,
        'details': results,
    }


if __name__ == '__main__':
    # 测试
    result = check_layer9_12('ETHUSDT', 1735.0, 'LONG', 36.4)
    print(json.dumps(result, ensure_ascii=False, indent=2))
