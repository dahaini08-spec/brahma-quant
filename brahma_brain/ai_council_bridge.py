"""
ai_council_bridge.py — 梵天AI议会+在线学习集成桥
设计院封印 2026-09-12 苏摩111

职责：
  1. 整合llm_council(规则裁决) + online_bayes(贝叶斯增量) + online_learner_v2(权重校准) + ev_feedback(经验闭环)
  2. 为ensemble_engine提供AI增强层
  3. 输出统一ai_council_verdict供trader_brain参考

接入位置：
  - brahma_brain/ai_council_bridge.py（本文件）
  - brahma_core.analyze() 输出后调用 get_council_verdict()
  - 与ensemble_score并行，不替换任何现有逻辑

已有组件（1896行）：
  - llm_council.py (268行): 三专家规则裁决 → bias/reason/action/confidence
  - online_bayes.py (155行): 贝叶斯后验WR → adj_score增量
  - online_learner_v2.py (240行): 信号权重自动校准
  - ev_feedback.py (231行): 经验矩阵+参数微调
"""
import sys, json
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))


def get_council_verdict(symbol: str, direction: str, brahma_result: dict = None,
                        ensemble_result: dict = None) -> dict:
    """
    整合4个已有AI/学习组件，输出统一裁决
    
    参数：
      symbol: 交易标的
      direction: 方向
      brahma_result: brahma_core.analyze()返回
      ensemble_result: ensemble_engine.get_ensemble_score()返回
    
    返回：
      {
        'council_bias': str,        # 偏多/偏空/中性
        'council_action': str,       # ENTER/WAIT/AVOID
        'council_confidence': str,   # HIGH/MED/LOW
        'council_reason': str,      # 一句话原因
        'bayes_adjustment': float,  # 贝叶斯增量分
        'weight_calibration': dict, # 权重校准状态
        'experience_nudge': str,     # 经验矩阵微调提示
        'council_score': int,       # -3~+3
        'combined_score': float,    # ensemble + bayes
      }
    """
    if not brahma_result:
        return _empty_verdict()
    
    r = brahma_result
    x = r.get('extra') or {}
    regime = r.get('regime', 'CHOP_MID')
    score = r.get('score', 0) or r.get('confluence', {}).get('total', 0)
    
    # ── 1. LLM Council规则裁决（已有，确定性）──────────────
    council = _get_council_verdict(r, direction, regime, score, x, symbol)
    
    # ── 2. Online Bayes贝叶斯增量（已有）────────────────────
    bayes_adj = _get_bayes_adjustment(symbol, regime, direction, score)
    
    # ── 3. Online Learner权重校准状态（已有）────────────────
    weight_cal = _get_weight_calibration()
    
    # ── 4. Experience Feedback经验提示（已有）──────────────
    exp_nudge = _get_experience_nudge(regime, direction, score)
    
    # ── 5. 组合分数：ensemble + bayes ────────────────────────
    ensemble_score = ensemble_result.get('ensemble_score', 0) if ensemble_result else 0
    combined_score = ensemble_score + bayes_adj['adjustment']
    combined_score = max(0, min(100, combined_score))
    
    return {
        'council_bias': council.get('bias', '中性'),
        'council_action': council.get('action', 'WAIT'),
        'council_confidence': council.get('confidence', 'LOW'),
        'council_reason': council.get('reason', ''),
        'bayes_adjustment': bayes_adj.get('adjustment', 0),
        'bayes_detail': bayes_adj.get('detail', ''),
        'weight_calibration': weight_cal,
        'experience_nudge': exp_nudge,
        'council_score': council.get('council_score', 0),
        'combined_score': round(combined_score, 2),
    }


def _get_council_verdict(r, direction, regime, score, x, symbol):
    """调用llm_council.council_verdict()"""
    try:
        from brahma_brain.llm_council import council_verdict
        
        # 从brahma_result提取council需要的参数
        cf = r.get('confluence', {}) or {}
        breakdown = cf.get('breakdown', {})
        
        sm = x.get('smart_money') or {}
        fvg_data = x.get('fvg') or {}
        
        return council_verdict(
            breakdown=breakdown,
            signal_dir=direction,
            regime=regime,
            score=score,
            liq_data=x.get('liquidity'),
            fvg_dir=fvg_data.get('consensus', 'NONE') if isinstance(fvg_data, dict) else 'NONE',
            oi_signal=sm.get('oi_momentum', 'MIXED') if isinstance(sm, dict) else 'MIXED',
            sm_signal=sm.get('sm_signal', 'NEUTRAL') if isinstance(sm, dict) else 'NEUTRAL',
            hurst=x.get('hurst', 0.5),
            kappa=x.get('kappa', 0.0),
            entry_lo=cf.get('entry_lo', 0),
            entry_hi=cf.get('entry_hi', 0),
            price=r.get('price', 0),
            sym=symbol[:3] if symbol else 'BTC',
        )
    except Exception as e:
        return {'bias': '中性', 'action': 'WAIT', 'confidence': 'LOW',
                'reason': f'council error: {str(e)[:50]}', 'council_score': 0}


def _get_bayes_adjustment(symbol, regime, direction, score):
    """调用online_bayes.score()获取贝叶斯增量"""
    try:
        from brahma_brain.online_bayes import score as bayes_score
        adj_score, detail = bayes_score(symbol, regime, direction, score)
        # adj_score已经是增量分（-8~+8），不需要再减score
        adjustment = float(adj_score) if isinstance(adj_score, (int, float)) else 0.0
        # detail是dict，提取关键信息
        if isinstance(detail, dict):
            detail_str = (f"prior={detail.get('prior_wr',0)}% "
                         f"post={detail.get('post_wr',0)}% "
                         f"n={detail.get('exp_n',0)} "
                         f"conf={detail.get('confidence','LOW')}")
        else:
            detail_str = str(detail)[:100]
        return {
            'adjustment': round(adjustment, 2),
            'detail': detail_str,
        }
    except Exception as e:
        return {'adjustment': 0, 'detail': f'bayes error: {str(e)[:50]}'}


def _get_weight_calibration():
    """读取online_learner_v2的权重校准状态"""
    try:
        from brahma_brain.online_learner_v2 import load_weights
        raw = load_weights()
        # load_weights()可能返回两种格式：
        # 1. 简单dict {dim: weight_float}（DEFAULT_WEIGHTS）
        # 2. 复杂dict（signal_weights.json实际格式，含version/weights等元数据）
        if 'weights' in raw and isinstance(raw['weights'], dict):
            # 复杂格式：提取实际权重子dict
            weights = raw['weights']
        else:
            weights = raw
        # 只取数值型权重
        numeric_weights = {k: v for k, v in weights.items()
                          if isinstance(v, (int, float))}
        n_adjusted = sum(1 for v in numeric_weights.values() if abs(v - 1.0) > 0.05)
        return {
            'weights': {k: v for k, v in numeric_weights.items() if abs(v - 1.0) > 0.05},
            'n_adjusted': n_adjusted,
            'status': 'CALIBRATED' if n_adjusted > 0 else 'DEFAULT',
        }
    except Exception:
        return {'weights': {}, 'n_adjusted': 0, 'status': 'N/A'}


def _get_experience_nudge(regime, direction, score):
    """读取ev_feedback经验矩阵，输出微调提示"""
    try:
        from brahma_brain.ev_feedback import get_ev_summary, _load_matrix
        # 直接读取完整矩阵，按regime+direction前缀匹配
        matrix = _load_matrix()
        if not matrix:
            return ''
        # 矩阵key格式: 'regime:direction:score_bin'
        prefix = f'{regime}:{direction}:'
        matched = {k: v for k, v in matrix.items() if k.startswith(prefix)}
        if not matched:
            return ''
        # 汇总匹配的keys
        total_n = sum(v.get('n', 0) for v in matched.values())
        total_wins = sum(v.get('n_win', 0) for v in matched.values())
        if total_n < 5:
            return f'经验矩阵: {regime}:{direction} n={total_n}<5 样本不足'
        wr = total_wins / total_n if total_n > 0 else 0
        if wr > 0.65:
            return f'经验矩阵: {regime}:{direction} WR={wr:.0%}(n={total_n}) → 顺势信号'
        elif wr < 0.40:
            return f'经验矩阵: {regime}:{direction} WR={wr:.0%}(n={total_n}) → 逆势警告'
        return f'经验矩阵: {regime}:{direction} WR={wr:.0%}(n={total_n}) 正常'
    except Exception:
        return ''


def _empty_verdict():
    return {
        'council_bias': '中性',
        'council_action': 'WAIT',
        'council_confidence': 'LOW',
        'council_reason': '',
        'bayes_adjustment': 0,
        'bayes_detail': '',
        'weight_calibration': {},
        'experience_nudge': '',
        'council_score': 0,
        'combined_score': 0,
    }


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    
    # Mock brahma_result for testing
    mock = {
        'regime': 'CHOP_MID', 'score': 100, 'price': 77000,
        'rsi_4h': 35, 'rsi_1h': 40,
        'confluence': {'total': 100, 'breakdown': {}, 'entry_lo': 77500, 'entry_hi': 78000},
        'extra': {
            'hurst': 0.43, 'kappa': 0.1,
            'smart_money': {'oi_momentum': 'SHORT_BUILD', 'sm_signal': 'NEUTRAL'},
            'fvg': {'consensus': 'BEAR'},
            'liquidity': {'nearest_above': 80000, 'nearest_below': 76000},
        },
    }
    
    from brahma_brain.ensemble_engine import get_ensemble_score
    ens = get_ensemble_score(sym, direction, mock)
    
    verdict = get_council_verdict(sym, direction, mock, ens)
    print(f"=== AI Council Bridge: {sym} {direction} ===")
    print(f"Council: {verdict['council_bias']} / {verdict['council_action']} / {verdict['council_confidence']}")
    print(f"Reason: {verdict['council_reason']}")
    print(f"Bayes adjustment: {verdict['bayes_adjustment']:+.2f} ({verdict['bayes_detail'][:60]})")
    print(f"Weight calibration: {verdict['weight_calibration'].get('status', 'N/A')}")
    print(f"Experience: {verdict['experience_nudge']}")
    print(f"Council score: {verdict['council_score']}")
    print(f"Combined score (ensemble+bayes): {verdict['combined_score']}")
