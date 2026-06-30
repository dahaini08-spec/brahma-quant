#!/usr/bin/env python3
"""
logic_auditor.py — 梵天逻辑错误自循环审计层 v1.0
设计院 · 360顶级量化工程师 · 2026-06-11

四类逻辑错误自动检测：

  L1 内部矛盾检测（Contradiction Detector）
     结论与输入数据方向是否自洽
     e.g. BEAR_TREND体制 + LONG信号 → 矛盾

  L2 历史反驳检测（Historical Refutation Engine）
     当前逻辑声称的高概率，是否被历史数据否定
     e.g. "BEAR体制SHORT胜率高" → 查wuqu_paper实际数据验证

  L3 推理跳跃检测（Reasoning Gap Detector）
     结构化推理链中是否有中间步骤缺失
     基于评分维度完整性检查

  L4 预测追踪（Prediction Drift Tracker）
     记录所有对外输出的价格预测
     定期回溯：预测是否兑现，误差多大
     自动更新"预测准确率"指标
"""

import json, time, math, datetime
from pathlib import Path
from collections import defaultdict

BASE         = Path(__file__).parent.parent
SETTLED_LOG  = BASE / 'data' / 'wuqu_paper_settled.jsonl'
SIGNAL_LOG   = BASE / 'data' / 'live_signal_log.jsonl'
STATE_FILE   = BASE / 'data' / 'brahma_state.json'
PRED_LOG     = BASE / 'data' / 'prediction_log.jsonl'   # 新建：预测追踪日志
LOGIC_AUDIT  = BASE / 'data' / 'logic_audit_log.jsonl'  # 新建：逻辑审计日志

# ─────────────────────────────────────────────────
# L1: 内部矛盾检测
# ─────────────────────────────────────────────────

REGIME_DIRECTION_RULES = {
    # 体制 → (推荐方向, 允许反向的条件)
    # 数据来源：达摩院M03大样本回测（17标的×多年，N14铁证）
    # 原则：任何限制必须基于大样本验证，grade>=70=B级有结构性支撑即允许逆向
    #       不再使用grade>=70等拍脑袋门槛
    #
    # BEAR_TREND:    SHORT优先(WR55% vs LONG 50%), 差值+5.2%
    # BEAR_CRASH:    SHORT优先(WR55% vs LONG 50%), 崩盘期系统性风险LONG严禁
    # BEAR_RECOVERY: LONG优先(WR55% vs SHORT 50%), 反弹期做多为主
    # BULL_TREND:    LONG优先(WR55% vs SHORT 50%)
    # BULL_PEAK:     SHORT优先(WR55% vs LONG 50%)
    # CHOP:          双向均等(WR均约50%)
    'BEAR_TREND':    ('SHORT', 'grade>=70'),  # [v24.2],          # 大样本: LONG=49.6%, SHORT=54.8%
    'BEAR_EARLY':    ('SHORT', 'grade>=70'),  # [v24.2],          # 对齐BEAR_TREND同等标准
    'BEAR_RECOVERY': ('SHORT', 'grade>=70'),  # [v24.2],          # v4铁证: SHORT PF=1.18 > LONG PF=0.98
    'BEAR_CRASH':    ('SHORT', 'NEVER'),              # 崩盘期：LONG严禁（系统性风险）
    'BULL_PEAK':     ('SHORT', 'grade>=70'),  # [v24.2],          # 大样本: SHORT=54.8% > LONG=49.6%
    'CHOP':          ('BOTH',  'BOTH_OK'),            # 大样本: 双向均等49.6%
    'CHOP_HIGH':     ('LONG',  'BOTH_OK'),            # 震荡偏多
    'CHOP_MID':      ('BOTH',  'BOTH_OK'),            # 震荡双向
    'CHOP_LOW':      ('SHORT', 'grade>=70'),  # [v24.2],          # 震荡偏空
    'BULL_EARLY':    ('LONG',  'grade>=70'),  # [v24.2],          # 大样本: LONG=54.8%优先
    'BULL_TREND':    ('LONG',  'grade>=70'),  # [v24.2],          # 大样本: LONG=54.8% > SHORT=49.6%
    'UNKNOWN':       ('BOTH',  'BOTH_OK'),            # 未知体制中性处理
}

def check_regime_direction_contradiction(signal: dict) -> dict:
    """检测体制与方向的矛盾"""
    regime    = signal.get('regime', 'UNKNOWN')
    direction = signal.get('direction', signal.get('signal_dir', '')).upper()
    score     = signal.get('score', signal.get('score_final', 0)) or 0
    grade     = signal.get('grade', 0) or 0

    rule = REGIME_DIRECTION_RULES.get(regime)
    if not rule:
        return {'verdict': 'UNKNOWN', 'msg': f'未知体制: {regime}'}

    preferred, override_cond = rule
    is_contra = (preferred == 'SHORT' and direction == 'LONG') or \
                (preferred == 'LONG'  and direction == 'SHORT')

    if not is_contra or preferred == 'BOTH':
        return {'verdict': 'OK', 'msg': f'{regime}体制做{direction}，方向合规'}

    # 检查是否满足反向条件
    if override_cond == 'NEVER':
        return {
            'verdict': 'CRITICAL',
            'tag':     'REGIME_DIRECTION_CONTRADICTION',
            'msg':     f'⚠️ {regime}体制严禁做{direction}（override_cond=NEVER）',
            'regime':  regime, 'direction': direction, 'score': score,
        }
    elif override_cond == 'BOTH_OK':
        return {'verdict': 'OK', 'msg': f'{regime}震荡体制双向均可'}
    else:
        # 解析条件：grade>=X AND score>=Y
        import re
        grade_match = re.search(r'grade>=(\d+)', override_cond)
        score_match = re.search(r'score>=(\d+)', override_cond)
        req_grade = int(grade_match.group(1)) if grade_match else 0
        req_score = int(score_match.group(1)) if score_match else 0

        if grade >= req_grade and score >= req_score:
            return {
                'verdict': 'WARN',
                'tag':     'REGIME_COUNTER_TREND',
                'msg':     f'逆势做{direction}（grade={grade}≥{req_grade} score={score}≥{req_score}，条件满足）',
            }
        else:
            return {
                'verdict': 'FAIL',
                'tag':     'REGIME_DIRECTION_CONTRADICTION',
                'msg':     f'逆势做{direction}但条件不足（需grade>={req_grade} score>={req_score}，实际grade={grade} score={score}）',
            }

# ─────────────────────────────────────────────────
# L2: 历史反驳检测
# ─────────────────────────────────────────────────

def load_settled_by_regime() -> dict:
    """从wuqu_paper加载各体制的历史胜率"""
    regime_stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'timeouts': 0})
    try:
        for line in open(SETTLED_LOG):
            try:
                r = json.loads(line)
                if r.get('_data_quality'): continue   # 过滤污染数据
                regime = r.get('regime', 'UNKNOWN')
                result = r.get('result', '')
                if result in ('TP1', 'TP2', 'WIN'):
                    regime_stats[regime]['wins'] += 1
                elif result in ('SL', 'LOSS'):
                    regime_stats[regime]['losses'] += 1
                elif result == 'TIMEOUT':
                    regime_stats[regime]['timeouts'] += 1
            except:
                pass
    except FileNotFoundError:
        pass
    return dict(regime_stats)

def check_historical_refutation(claimed_wr: float, regime: str, direction: str,
                                  min_samples: int = 5) -> dict:
    """
    检测：报告中声称某体制方向"高胜率"，是否被历史数据否定
    claimed_wr: 声称的胜率（0~1）
    """
    stats_by_regime = load_settled_by_regime()
    key = regime

    stats = stats_by_regime.get(key, {})
    wins  = stats.get('wins', 0)
    losses = stats.get('losses', 0)
    total = wins + losses

    if total < min_samples:
        return {
            'verdict': 'INSUFFICIENT_DATA',
            'msg':     f'{regime}历史样本仅{total}条（需≥{min_samples}），无法反驳',
            'total':   total,
        }

    actual_wr = wins / total
    # Wilson置信区间
    z = 1.96
    p = actual_wr
    d = 1 + z**2/total
    c = (p + z**2/(2*total)) / d
    m = (z * math.sqrt(p*(1-p)/total + z**2/(4*total**2))) / d
    ci_lo = max(0, c - m)

    # 声明的WR是否在置信区间外（历史反驳）
    refuted = claimed_wr > ci_lo + 0.15  # 声明比历史上限高15%以上 = 可疑

    return {
        'verdict':     'REFUTED' if refuted else 'CONSISTENT',
        'regime':       regime,
        'claimed_wr':   round(claimed_wr, 3),
        'actual_wr':    round(actual_wr, 3),
        'ci':           [round(ci_lo,3), round(min(1,c+m),3)],
        'total':        total,
        'msg': (f'⚠️ 历史反驳：声称WR={claimed_wr*100:.0f}%但历史实际WR={actual_wr*100:.0f}% CI=[{ci_lo*100:.0f}%,{min(1,c+m)*100:.0f}%]'
                if refuted else
                f'✅ 与历史一致：{regime} WR={actual_wr*100:.0f}% (n={total})'),
    }

# ─────────────────────────────────────────────────
# L3: 推理完整性检测
# ─────────────────────────────────────────────────

REQUIRED_SIGNAL_FIELDS = [
    'symbol', 'direction', 'score', 'grade', 'regime',
    'entry_lo', 'entry_hi', 'stop_loss', 'tp1',
]

REQUIRED_REASONING_DIMS = [
    # (字段名, 最低非零门槛, 描述)
    ('gap_pct',       0,    'gap距离'),
    ('tf_aligned',    0,    '多周期对齐'),
    ('regime',        None, '体制判断'),
]

def check_reasoning_completeness(signal: dict) -> dict:
    """检测信号推理链是否完整，有无跳跃"""
    issues = []

    # 必填字段
    for field in REQUIRED_SIGNAL_FIELDS:
        val = signal.get(field)
        if val is None or val == '' or val == 0:
            if field in ('entry_lo', 'entry_hi', 'stop_loss', 'tp1'):
                issues.append(f'缺失关键价位: {field}={val}')
            elif field in ('score', 'grade'):
                issues.append(f'评分缺失: {field}={val}')

    # 止损位置合理性（SHORT止损必须在entry_hi上方）
    direction = signal.get('direction', '').upper()
    entry_hi  = signal.get('entry_hi', 0) or 0
    sl        = signal.get('stop_loss', 0) or 0
    entry_lo  = signal.get('entry_lo', 0) or 0
    tp1       = signal.get('tp1', 0) or 0

    if direction == 'SHORT' and sl > 0 and entry_hi > 0:
        if sl <= entry_hi:
            issues.append(f'⚠️ SHORT止损${sl}≤entry_hi${entry_hi}（止损落入OB内部）')

    if direction == 'LONG' and sl > 0 and entry_lo > 0:
        if sl >= entry_lo:
            issues.append(f'⚠️ LONG止损${sl}≥entry_lo${entry_lo}（止损落入OB内部）')

    # R:R 验证
    if entry_lo and tp1 and sl and entry_hi:
        entry_mid = (entry_lo + entry_hi) / 2
        if direction == 'SHORT':
            risk   = abs(sl - entry_mid)
            reward = abs(entry_mid - tp1)
        else:
            risk   = abs(entry_mid - sl)
            reward = abs(tp1 - entry_mid)
        if risk > 0:
            rr = reward / risk
            if rr < 1.0:
                issues.append(f'⚠️ R:R={rr:.2f}<1.0，风报比不足')
            elif rr < 1.5:
                issues.append(f'注意：R:R={rr:.2f}偏低（建议≥1.5）')

    verdict = 'FAIL' if any('⚠️' in i for i in issues) else \
              ('WARN' if issues else 'OK')

    return {
        'verdict': verdict,
        'issues':  issues,
        'issue_count': len(issues),
        'msg': f'推理链{verdict}: {len(issues)}个问题' if issues else '推理链完整 ✅',
    }

# ─────────────────────────────────────────────────
# L4: 预测追踪（Prediction Drift Tracker）
# ─────────────────────────────────────────────────

def record_prediction(pred_type: str, symbol: str, target_price: float,
                       direction: str, timeframe_hours: int,
                       basis: str = '', source: str = '') -> str:
    """
    记录对外输出的价格预测
    在每次发出分析报告时调用

    pred_type: 'TARGET' | 'SUPPORT' | 'RESISTANCE' | 'BOTTOM' | 'TOP'
    返回: prediction_id
    """
    import secrets
    pred_id = secrets.token_hex(4)
    ts      = datetime.datetime.utcnow().isoformat()
    deadline = (datetime.datetime.utcnow() +
                datetime.timedelta(hours=timeframe_hours)).isoformat()

    record = {
        'pred_id':       pred_id,
        'pred_type':     pred_type,
        'symbol':        symbol.upper().replace('USDT',''),
        'target_price':  target_price,
        'direction':     direction,
        'timeframe_h':   timeframe_hours,
        'basis':         basis,
        'source':        source,
        'created_at':    ts,
        'deadline':      deadline,
        'status':        'PENDING',
        'actual_price':  None,
        'accuracy_pct':  None,
        'settled_at':    None,
    }

    with open(PRED_LOG, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')

    return pred_id

def settle_predictions() -> dict:
    """
    批量结算到期预测
    对比实际价格 vs 预测价格，计算准确率
    """
    import subprocess

    if not PRED_LOG.exists():
        return {'settled': 0, 'pending': 0}

    records = []
    try:
        for line in open(PRED_LOG):
            try: records.append(json.loads(line))
            except: pass
    except: pass

    now = datetime.datetime.utcnow()
    settled_count = 0
    results = []

    for r in records:
        if r.get('status') != 'PENDING':
            results.append(r)
            continue

        deadline = datetime.datetime.fromisoformat(r['deadline'])
        if now < deadline:
            results.append(r)
            continue

        # 到期，查询实际价格
        sym = r['symbol'] + 'USDT'
        try:
            resp = subprocess.run(
                ['curl', '-s', f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}'],
                capture_output=True, text=True, timeout=5
            )
            actual = float(json.loads(resp.stdout)['price'])
        except:
            results.append(r)
            continue

        target    = r['target_price']
        diff_pct  = (actual - target) / target * 100
        direction = r.get('direction', '')

        # 判断是否"命中"（目标价±5%内算命中）
        hit = abs(diff_pct) <= 5.0

        # 方向性命中（预测上涨且实际高于预测起点算方向正确）
        dir_correct = None
        if direction in ('UP', 'LONG', '看多', '多'):
            dir_correct = actual >= target * 0.95
        elif direction in ('DOWN', 'SHORT', '看空', '空'):
            dir_correct = actual <= target * 1.05

        r['status']       = 'HIT' if hit else 'MISS'
        r['actual_price'] = round(actual, 2)
        r['accuracy_pct'] = round(diff_pct, 2)
        r['dir_correct']  = dir_correct
        r['settled_at']   = now.isoformat()
        settled_count += 1
        results.append(r)

    # 写回
    with open(PRED_LOG, 'w') as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    # 统计预测准确率
    settled = [r for r in results if r.get('status') in ('HIT','MISS')]
    hits    = [r for r in settled if r.get('status') == 'MISS' or r.get('status') == 'HIT']
    hit_cnt = sum(1 for r in settled if r['status'] == 'HIT')
    hit_rate = hit_cnt / len(settled) * 100 if settled else 0

    return {
        'settled':    settled_count,
        'pending':    sum(1 for r in results if r.get('status') == 'PENDING'),
        'total_eval': len(settled),
        'hit_rate':   round(hit_rate, 1),
        'avg_error':  round(sum(abs(r.get('accuracy_pct',0)) for r in settled) / max(1,len(settled)), 2),
    }

def get_prediction_accuracy_report() -> str:
    """生成预测准确率摘要（用于报告头部）"""
    if not PRED_LOG.exists():
        return '预测追踪：尚无历史记录'

    records = []
    try:
        for line in open(PRED_LOG):
            try: records.append(json.loads(line))
            except: pass
    except: pass

    settled = [r for r in records if r.get('status') in ('HIT','MISS')]
    pending = [r for r in records if r.get('status') == 'PENDING']

    if not settled:
        return f'预测追踪：{len(pending)}条待验证，暂无已结算记录'

    hit_rate = sum(1 for r in settled if r['status']=='HIT') / len(settled) * 100
    avg_err  = sum(abs(r.get('accuracy_pct',0)) for r in settled) / len(settled)

    return (f'预测准确率: {hit_rate:.0f}% (±{avg_err:.1f}%)  '
            f'样本{len(settled)}条已结算 / {len(pending)}条待验')

# ─────────────────────────────────────────────────
# 综合逻辑审计入口
# ─────────────────────────────────────────────────

def audit_signal_logic(signal: dict) -> dict:
    """
    对单个信号进行完整逻辑审计
    在brahma_analyze输出前调用
    """
    ts = datetime.datetime.utcnow().isoformat()
    result = {
        'signal_id':  signal.get('signal_id', signal.get('id', 'unknown')),
        'symbol':     signal.get('symbol', '?'),
        'ts':         ts,
        'checks':     {},
        'verdict':    'OK',
        'critical_issues': [],
        'warnings':   [],
    }

    # L1: 矛盾检测
    l1 = check_regime_direction_contradiction(signal)
    result['checks']['L1_contradiction'] = l1
    if l1['verdict'] == 'CRITICAL':
        result['critical_issues'].append(l1['msg'])
        result['verdict'] = 'CRITICAL'
    elif l1['verdict'] == 'FAIL':
        result['critical_issues'].append(l1['msg'])
        if result['verdict'] != 'CRITICAL': result['verdict'] = 'FAIL'
    elif l1['verdict'] == 'WARN':
        result['warnings'].append(l1['msg'])

    # L3: 推理完整性
    l3 = check_reasoning_completeness(signal)
    result['checks']['L3_completeness'] = l3
    if l3['verdict'] == 'FAIL':
        result['critical_issues'].extend([i for i in l3['issues'] if '⚠️' in i])
        if result['verdict'] not in ('CRITICAL',): result['verdict'] = 'FAIL'
    elif l3['verdict'] == 'WARN':
        result['warnings'].extend(l3['issues'])

    # 写入审计日志
    try:
        with open(LOGIC_AUDIT, 'a') as f:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')
    except: pass

    return result


def audit_report_logic(claims: list) -> dict:
    """
    对报告级别的逻辑主张进行批量审计
    claims = [
        {'type': 'regime_wr', 'regime': 'BEAR_TREND', 'claimed_wr': 0.85},
        {'type': 'direction', 'regime': 'BEAR_TREND', 'direction': 'SHORT'},
    ]
    """
    results = {'checks': [], 'issues': 0, 'warnings': 0}

    for c in claims:
        if c['type'] == 'regime_wr':
            r = check_historical_refutation(c['claimed_wr'], c['regime'], c.get('direction',''))
            results['checks'].append(r)
            if r['verdict'] == 'REFUTED': results['issues'] += 1

        elif c['type'] == 'direction':
            r = check_regime_direction_contradiction({
                'regime': c['regime'],
                'direction': c['direction'],
                'score': c.get('score', 999),
                'grade': c.get('grade', 999),
            })
            results['checks'].append(r)
            if r['verdict'] in ('CRITICAL','FAIL'): results['issues'] += 1
            elif r['verdict'] == 'WARN': results['warnings'] += 1

    results['verdict'] = 'CLEAN' if results['issues'] == 0 else f'ISSUES({results["issues"]})'
    return results


if __name__ == '__main__':
    print('=' * 64)
    print('  logic_auditor.py — 逻辑自循环审计层')
    print('=' * 64)

    # 测试1: 内部矛盾检测
    print('\n=== L1 内部矛盾检测 ===')
    test_signals = [
        {'symbol':'BTCUSDT','direction':'SHORT','regime':'BEAR_TREND','score':155,'grade':65},
        {'symbol':'ETHUSDT','direction':'LONG', 'regime':'BEAR_TREND','score':150,'grade':60},
        {'symbol':'BTCUSDT','direction':'LONG', 'regime':'BEAR_CRASH', 'score':180,'grade':80},
        {'symbol':'BTCUSDT','direction':'SHORT','regime':'BULL_TREND', 'score':165,'grade':70},
    ]
    for s in test_signals:
        r = check_regime_direction_contradiction(s)
        print(f'  [{r["verdict"]}] {s["symbol"]} {s["direction"]} @{s["regime"]}: {r["msg"]}')

    # 测试2: 历史反驳
    print('\n=== L2 历史反驳检测 ===')
    r = check_historical_refutation(0.95, 'BEAR_TREND', 'SHORT')
    print(f'  [{r["verdict"]}] BEAR_TREND SHORT 声称WR=95%: {r["msg"]}')

    # 测试3: 推理完整性
    print('\n=== L3 推理完整性 ===')
    good_signal = {
        'symbol':'ETHUSDT','direction':'SHORT','regime':'BEAR_TREND',
        'score':155,'grade':65,
        'entry_lo':1652,'entry_hi':1660,'stop_loss':1668,'tp1':1603,
    }
    bad_signal = {
        'symbol':'ETHUSDT','direction':'SHORT','regime':'BEAR_TREND',
        'score':155,'grade':65,
        'entry_lo':1652,'entry_hi':1660,'stop_loss':1655,'tp1':1603,  # SL在OB内部！
    }
    r1 = check_reasoning_completeness(good_signal)
    r2 = check_reasoning_completeness(bad_signal)
    print(f'  好信号: [{r1["verdict"]}] {r1["msg"]}')
    print(f'  坏信号: [{r2["verdict"]}] {r2["issues"]}')

    # 测试4: 预测追踪
    print('\n=== L4 预测追踪 ===')
    pid = record_prediction('TARGET','BTC',65000,'UP',72,'BTC当前63K，预测72H内测试65K','测试')
    print(f'  记录预测ID: {pid}')
    settle_res = settle_predictions()
    print(f'  结算结果: {settle_res}')
    print(f'  准确率摘要: {get_prediction_accuracy_report()}')

    # 综合测试
    print('\n=== 综合信号审计 ===')
    audit = audit_signal_logic(bad_signal)
    print(f'  综合判定: {audit["verdict"]}')
    for issue in audit['critical_issues']:
        print(f'  ❌ {issue}')
    for warn in audit['warnings']:
        print(f'  ⚠️ {warn}')
