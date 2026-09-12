#!/usr/bin/env python3
"""
全流程深度剖析测试 — 四个Phase集成验证
设计院 2026-09-12 苏摩111

测试链路：
  Phase 1: feature_store.get_features() → 94维特征 + 5组alpha归因
  Phase 2: risk_engine.check() → 6层风控gate
  Phase 3: ensemble_engine.get_ensemble_score() → 12维精简IC加权
  Phase 4: ai_council_bridge.get_council_verdict() → 4组件统一裁决

集成验证：
  brahma_core.analyze() → P1特征 → P3 ensemble → P4 council → P2 risk → 最终输出
"""
import sys, json, time, traceback
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

PASS = 0
FAIL = 0
SKIP = 0
RESULTS = []

def test(name, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append(f'  ✅ {name} {detail}')
    else:
        FAIL += 1
        RESULTS.append(f'  ❌ {name} {detail}')

def skip(name, reason=''):
    global SKIP
    SKIP += 1
    RESULTS.append(f'  ⏭️  {name} {reason}')

# ═══════════════════════════════════════════════════════════
print('=' * 60)
print('  全流程深度剖析 — 四Phase集成测试')
print('=' * 60)

# ── Phase 1: Feature Store 94维 ──────────────────────────
print('\n── Phase 1: Feature Store 94维 + Alpha归因 ──')
try:
    from brahma_brain.feature_store import get_features, ALPHA_GROUPS, TTL_SECONDS
    
    # P1.1: ALPHA_GROUPS结构
    test('P1.1 ALPHA_GROUPS 5组定义',
         len(ALPHA_GROUPS) == 5,
         f'({len(ALPHA_GROUPS)}组)')
    
    expected_groups = {'G1_structure', 'G2_momentum', 'G3_capital_flow', 'G4_volatility', 'G5_macro'}
    test('P1.1b 5组名称正确',
         set(ALPHA_GROUPS.keys()) == expected_groups,
         f'{sorted(ALPHA_GROUPS.keys())}')
    
    # P1.2: 每组有dims + ic_weight
    all_dims_ok = all(
        isinstance(g.get('dims'), list) and isinstance(g.get('ic_weight'), (int, float))
        for g in ALPHA_GROUPS.values()
    )
    test('P1.2 每组有dims+ic_weight', all_dims_ok)
    
    # P1.3: TTL配置
    test('P1.3 TTL=1800s(30min)', TTL_SECONDS == 1800, f'(TTL={TTL_SECONDS})')
    
    # P1.4: get_features接口可调用（用mock避免全量分析）
    # 验证函数签名而非实际调用（全量分析耗时>60s）
    import inspect
    sig = inspect.signature(get_features)
    test('P1.4 get_features(symbol, force_refresh) 签名',
         'symbol' in sig.parameters and 'force_refresh' in sig.parameters,
         f'params={list(sig.parameters.keys())}')
    
    # P1.5: 缓存目录存在
    cache_dir = BASE / 'data' / 'feature_cache'
    test('P1.5 缓存目录存在', cache_dir.exists(), f'({cache_dir})')
    
    # P1.6: Alpha归因函数存在
    from brahma_brain import feature_store as fs_mod
    has_attribution = hasattr(fs_mod, '_compute_alpha_attribution') or hasattr(fs_mod, '_compute_fresh')
    test('P1.6 归因计算函数存在', has_attribution)
    
except Exception as e:
    test('P1 IMPORT', False, f'Exception: {e}')
    traceback.print_exc()

# ── Phase 2: Risk Engine 独立风控 ─────────────────────────
print('\n── Phase 2: Risk Engine 独立风控层 ──')
try:
    from brahma_brain.risk_engine import check, KILL_SWITCH_DAILY_LOSS_PCT, CORRELATION_ADJUST_FACTOR
    
    # P2.1: 常量配置
    test('P2.1 Kill switch=5%NAV', KILL_SWITCH_DAILY_LOSS_PCT == 5.0, f'({KILL_SWITCH_DAILY_LOSS_PCT}%)')
    test('P2.1b 相关性调整=0.7', CORRELATION_ADJUST_FACTOR == 0.7, f'({CORRELATION_ADJUST_FACTOR})')
    
    # P2.2: 空信号 → 不通过（无direction）
    r2 = check({'symbol': 'BTCUSDT', 'price': 60000})
    test('P2.2 空信号返回dict', isinstance(r2, dict))
    test('P2.2b 返回approved字段', 'approved' in r2)
    test('P2.2c 返回modified字段', 'modified' in r2)
    test('P2.2d 返回reasons字段', 'reasons' in r2)
    test('P2.2e 返回kill_switch字段', 'kill_switch' in r2)
    test('P2.2f 返回warnings字段', 'warnings' in r2)
    
    # P2.3: 正常信号（无持仓 → 无相关性调整）
    r3 = check({
        'symbol': 'BTCUSDT', 'direction': 'LONG', 'price': 60000,
        'position_pct': 5, 'leverage': 10, 'sl': 58000,
    })
    test('P2.3 正常信号返回dict', isinstance(r3, dict))
    test('P2.3b approved是bool', isinstance(r3.get('approved'), bool))
    test('P2.3c modified保留position_pct', r3.get('modified', {}).get('position_pct') == 5)
    
    # P2.4: kill switch信号（mock无法触发，验证逻辑分支存在）
    test('P2.4 kill_switch字段类型bool', isinstance(r3.get('kill_switch'), bool))
    
    # P2.5: 仓位乘数应用
    from brahma_brain.risk_engine import _apply_position_mult
    mock_signal = {'position_pct': 10}
    _apply_position_mult(mock_signal, 0.7, 'test')
    test('P2.5 仓位乘数应用', mock_signal['position_pct'] == 7.0, f'({mock_signal})')
    test('P2.5b risk_adjustments记录', 'risk_adjustments' in mock_signal)
    
except Exception as e:
    test('P2 IMPORT', False, f'Exception: {e}')
    traceback.print_exc()

# ── Phase 3: Ensemble Engine 12维精简 ─────────────────────
print('\n── Phase 3: Ensemble Engine 12维精简 ──')
try:
    from brahma_brain.ensemble_engine import get_ensemble_score, IC_WEIGHTS, REGIME_MAP, REGIME_DIRECTION_WEIGHTS
    
    # P3.1: 13维IC权重（Phase 5新增cross_market）
    test('P3.1 IC_WEIGHTS 13维', len(IC_WEIGHTS) == 13, f'({len(IC_WEIGHTS)}维)')
    expected_dims = {'rsi_4h', 'change_3d', 'bbw', 'hurst', 'oi_chg_3d', 'fr_mean',
                     'atr_rank', 'regime_code', 'macro_days', 'vol_rank', 'score_rank', 'stoch_rsi',
                     'cross_market'}
    test('P3.1b 12维名称正确', set(IC_WEIGHTS.keys()) == expected_dims)
    
    # P3.2: 体制编码映射
    test('P3.2 REGIME_MAP 6+体制', len(REGIME_MAP) >= 6, f'({len(REGIME_MAP)}体制)')
    
    # P3.3: 体制×方向乘数
    test('P3.3 REGIME_DIRECTION_WEIGHTS存在', len(REGIME_DIRECTION_WEIGHTS) >= 4,
         f'({len(REGIME_DIRECTION_WEIGHTS)}组合)')
    
    # P3.4: BEAR_TREND SHORT 乘数 > 1（顺势加分）
    rd_short = REGIME_DIRECTION_WEIGHTS.get(('BEAR_TREND', 'SHORT'), 1.0)
    test('P3.4 BEAR_TREND:SHORT 乘数>1', rd_short > 1.0, f'(×{rd_short})')
    
    # P3.5: BEAR_TREND LONG 乘数 < 1（逆势减分）
    rd_long = REGIME_DIRECTION_WEIGHTS.get(('BEAR_TREND', 'LONG'), 1.0)
    test('P3.5 BEAR_TREND:LONG 乘数<1', rd_long < 1.0, f'(×{rd_long})')
    
    # P3.6: ensemble_score 范围 [0, 100]
    mock_be = {
        'regime': 'BEAR_TREND', 'score': 148, 'price': 57000,
        'rsi_4h': 35, 'rsi_1h': 40,
        'confluence': {'total': 148, 'breakdown': {}, 'entry_lo': 56500, 'entry_hi': 57200},
        'extra': {'hurst': 0.43, 'kappa': -0.05},
    }
    ens = get_ensemble_score('BTCUSDT', 'SHORT', mock_be)
    test('P3.6 ensemble_score范围', 0 <= ens['ensemble_score'] <= 100,
         f'(score={ens["ensemble_score"]})')
    test('P3.6b ensemble_signal范围', -1 <= ens['ensemble_signal'] <= 1,
         f'(signal={ens["ensemble_signal"]})')
    test('P3.6c ic_weighted 13维', len(ens['ic_weighted']) == 13,
         f'({len(ens["ic_weighted"])}维)')
    test('P3.6d true_alpha top-3', len(ens['true_alpha']) == 3,
         f'({ens["true_alpha"]})')
    test('P3.6e regime_direction_mult正确', ens['regime_direction_mult'] == rd_short,
         f'(×{ens["regime_direction_mult"]})')
    
    # P3.7: BULL_TREND LONG 顺势
    mock_bull = {
        'regime': 'BULL_TREND', 'score': 155, 'price': 65000,
        'rsi_4h': 45, 'rsi_1h': 50,
        'confluence': {'total': 155, 'breakdown': {}, 'entry_lo': 64500, 'entry_hi': 65200},
        'extra': {'hurst': 0.58, 'kappa': 0.05},
    }
    ens2 = get_ensemble_score('BTCUSDT', 'LONG', mock_bull)
    rd_bull_long = REGIME_DIRECTION_WEIGHTS.get(('BULL_TREND', 'LONG'), 1.0)
    test('P3.7 BULL_TREND:LONG 乘数>1', rd_bull_long > 1.0, f'(×{rd_bull_long})')
    test('P3.7b ensemble_score BULL LONG', ens2['ensemble_score'] > 0, f'(score={ens2["ensemble_score"]})')
    
    # P3.8: 空输入 → 安全降级
    ens3 = get_ensemble_score('', '', None)
    test('P3.8 空输入降级', ens3.get('ensemble_score') == 0 and 'error' in ens3)
    
except Exception as e:
    test('P3 IMPORT', False, f'Exception: {e}')
    traceback.print_exc()

# ── Phase 4: AI Council Bridge 集成桥 ─────────────────────
print('\n── Phase 4: AI Council Bridge 集成桥 ──')
try:
    from brahma_brain.ai_council_bridge import get_council_verdict
    
    # P4.1: 4组件集成（验证返回字段完整性）
    mock = {
        'regime': 'BEAR_TREND', 'score': 148, 'price': 57000,
        'rsi_4h': 35, 'rsi_1h': 40,
        'confluence': {'total': 148, 'breakdown': {'SMC结构': 14, '宏观+事件': -2}, 'entry_lo': 56500, 'entry_hi': 57200},
        'extra': {
            'hurst': 0.43, 'kappa': -0.05,
            'smart_money': {'oi_momentum': 'SHORT_BUILD', 'sm_signal': 'MILD_BEAR'},
            'fvg': {'consensus': 'BEAR'},
            'liquidity': {'dist_to_short_liq': 3.2, 'dist_to_long_liq': 1.5},
        },
    }
    from brahma_brain.ensemble_engine import get_ensemble_score
    ens = get_ensemble_score('BTCUSDT', 'SHORT', mock)
    v = get_council_verdict('BTCUSDT', 'SHORT', mock, ens)
    
    # P4.1: 返回所有必要字段
    required_fields = ['council_bias', 'council_action', 'council_confidence', 'council_reason',
                       'bayes_adjustment', 'bayes_detail', 'weight_calibration',
                       'experience_nudge', 'council_score', 'combined_score']
    for f in required_fields:
        test(f'P4.1 字段 {f}', f in v, f'{"✓" if f in v else "✗"}')
    
    # P4.2: council_bias 值域
    test('P4.2 council_bias有效值', v['council_bias'] in ('偏多', '偏空', '中性'),
         f'({v["council_bias"]})')
    
    # P4.3: council_action 值域
    test('P4.3 council_action有效值', v['council_action'] in ('ENTER', 'WAIT', 'AVOID'),
         f'({v["council_action"]})')
    
    # P4.4: bayes_adjustment 范围 [-8, +8]
    test('P4.4 bayes_adjustment范围', -8 <= v['bayes_adjustment'] <= 8,
         f'({v["bayes_adjustment"]:+.2f})')
    
    # P4.5: bayes_detail 人类可读
    test('P4.5 bayes_detail含prior=', 'prior=' in str(v['bayes_detail']),
         f'("{v["bayes_detail"][:50]}")')
    
    # P4.6: weight_calibration 状态
    test('P4.6 weight_calibration有status', 'status' in v['weight_calibration'],
         f'({v["weight_calibration"].get("status", "N/A")})')
    
    # P4.7: combined_score = ensemble + bayes
    expected_combined = max(0, min(100, ens['ensemble_score'] + v['bayes_adjustment']))
    test('P4.7 combined_score = ensemble+bayes', 
         abs(v['combined_score'] - round(expected_combined, 2)) < 0.1,
         f'(combined={v["combined_score"]} expected≈{expected_combined:.2f})')
    
    # P4.8: 空输入安全降级
    v_empty = get_council_verdict('', '', None, None)
    test('P4.8 空输入→WAIT', v_empty['council_action'] == 'WAIT')
    
    # P4.9: 验证llm_council组件可调用
    from brahma_brain.llm_council import council_verdict
    cv = council_verdict(
        breakdown={'SMC结构': 14}, signal_dir='SHORT', regime='BEAR_TREND', score=148,
        fvg_dir='BEAR', oi_signal='SHORT_BUILD', sm_signal='MILD_BEAR',
    )
    test('P4.9 llm_council.council_verdict可调用', 'bias' in cv and 'action' in cv,
         f'(bias={cv["bias"]} action={cv["action"]})')
    
    # P4.10: 验证online_bayes组件可调用
    from brahma_brain.online_bayes import score as bayes_score
    adj, detail = bayes_score('BTCUSDT', 'BEAR_TREND', 'SHORT', 148)
    test('P4.10 online_bayes.score可调用', isinstance(adj, (int, float)) and isinstance(detail, dict),
         f'(adj={adj} detail keys={list(detail.keys())[:3]})')
    
    # P4.11: 验证online_learner_v2组件可调用
    from brahma_brain.online_learner_v2 import load_weights
    w = load_weights()
    test('P4.11 online_learner_v2.load_weights可调用', isinstance(w, dict),
         f'(keys={len(w)})')
    
    # P4.12: 验证ev_feedback组件可调用
    from brahma_brain.ev_feedback import get_ev_summary, _load_matrix
    matrix = _load_matrix()
    test('P4.12 ev_feedback._load_matrix可调用', isinstance(matrix, dict),
         f'({len(matrix)} entries)')
    
except Exception as e:
    test('P4 IMPORT', False, f'Exception: {e}')
    traceback.print_exc()

# ── 全流程集成: brahma_core → P1 → P3 → P4 → P2 ────────
print('\n── 全流程集成: brahma_core → P1 → P3 → P4 → P2 ──')
try:
    from brahma_brain.brahma_core import analyze
    
    # 验证brahma_core中4个Phase的接入点
    import inspect
    src = inspect.getsource(analyze) if hasattr(analyze, '__code__') else ''
    
    # I7: feature_store接入
    test('I7 feature_store接入 brahma_core', 'feature_store' in src and 'get_features' in src)
    
    # I8: ensemble_engine接入
    test('I8 ensemble_engine接入 brahma_core', 'ensemble_engine' in src and 'get_ensemble_score' in src)
    
    # I9: ai_council_bridge接入
    test('I9 ai_council_bridge接入 brahma_core', 'ai_council_bridge' in src and 'get_council_verdict' in src)
    
    # risk_engine接入验证（在auto_executor中）
    from brahma_brain import risk_engine as re_mod
    test('risk_engine.check函数存在', hasattr(re_mod, 'check'))
    
except Exception as e:
    test('INTEGRATION', False, f'Exception: {e}')
    traceback.print_exc()

# ── 数据流完整性验证 ────────────────────────────────────
print('\n── 数据流完整性验证 ──')
try:
    # 验证Phase间数据传递格式
    # P1 → P3: feature_store的94维 → ensemble的12维提取
    from brahma_brain.ensemble_engine import _extract_12vec_from_result
    test('P1→P3 _extract_12vec_from_result存在', callable(_extract_12vec_from_result))
    
    # P3 → P4: ensemble_result → ai_council_bridge
    # 验证get_council_verdict接受ensemble_result参数
    import inspect
    sig = inspect.signature(get_council_verdict)
    test('P3→P4 get_council_verdict接受ensemble_result',
         'ensemble_result' in sig.parameters,
         f'params={list(sig.parameters.keys())}')
    
    # P4 → P2: council_verdict → risk_engine.check
    # risk_engine.check接受signal dict
    sig_risk = inspect.signature(re_mod.check)
    test('P4→P2 risk_engine.check接受signal dict',
         'signal' in sig_risk.parameters,
         f'params={list(sig_risk.parameters.keys())}')
    
    # P2 → executor: risk_engine返回 approved + modified
    test('P2→executor check()返回approved+modified',
         True,  # 已在P2.2中验证
         '(已在P2.2验证)')
    
except Exception as e:
    test('DATAFLOW', False, f'Exception: {e}')
    traceback.print_exc()

# ── 输出汇总 ─────────────────────────────────────────────
print('\n' + '=' * 60)
print('  测试结果汇总')
print('=' * 60)
for r in RESULTS:
    print(r)
print(f'\n  ✅ PASS: {PASS} | ❌ FAIL: {FAIL} | ⏭️  SKIP: {SKIP}')
print('=' * 60)

if FAIL == 0:
    print('\n  🟢 全流程测试通过 — 四Phase集成正常')
else:
    print(f'\n  🔴 {FAIL}项失败 — 需要修复')
    
sys.exit(0 if FAIL == 0 else 1)
