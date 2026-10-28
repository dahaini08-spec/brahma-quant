#!/usr/bin/env python3
"""
regime_transition_detector.py — 体制切换预判维度
[封印 2026-10-27 苏摩111] P1-C 预判型升级

核心逻辑：
  OI骤增 + FR转向 + 清算密度激增 → 三合一预警
  transition_score >= 65 → 体制即将切换 → Gate4.5豁免CHOP阻断

五个维度评分（总分100）：
  OI骤增（15min内+3%）          → +25
  FR从负转正（连续3根同向）      → +20
  清算密度激增（liqmap层叠）     → +20
  BB宽度释放（<0.5扩张到>1.0）   → +15
  GEX ZF穿越（负→正Gamma区）     → +10
  周线体制与4H背离               → +10

接入位置：
  1. step11_mandatory_judge.py Gate4.5（transition_score>=65→豁免CHOP）
  2. brahma_crontab.txt 每10min独立运行+推送预警
  3. leading_signal_executor.py 触发前调用作为二次确认
"""
import sys, json, time, urllib.request
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

TRANSITION_THRESHOLD = 65   # ≥65分 → 体制切换预警
COOLDOWN_PUSH_MIN    = 60   # 推送冷却60min
_STATE_FILE = DATA / 'regime_transition_state.json'


def _load_state_file() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state_file(s: dict):
    tmp = _STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False))
    tmp.replace(_STATE_FILE)


def _load_brahma_state(sym: str) -> dict:
    f = DATA / f'brahma_state_{sym.lower()}.json'
    return json.loads(f.read_text()) if f.exists() else {}


def _fetch_oi_sequence(sym: str) -> list:
    """从state里读OI序列（15min×8根）"""
    d = _load_brahma_state(sym)
    oi = d.get('oi', {})
    if isinstance(oi, dict):
        seq = oi.get('sequence', oi.get('oi_seq', []))
        if seq:
            return [float(x) for x in seq[-8:]]
    return []


# ══════════════════════════════════════════════════════
# 五维度评分
# ══════════════════════════════════════════════════════

def score_oi_surge(sym: str, state: dict) -> tuple[int, str]:
    """OI骤增：15min内OI变化>3% → +25"""
    oi_seq = _fetch_oi_sequence(sym)
    if len(oi_seq) < 2:
        return 0, 'OI序列不足'
    last_chg = abs(oi_seq[-1] - oi_seq[-2]) / max(oi_seq[-2], 1) * 100
    if last_chg >= 3.0:
        return 25, f'OI骤增+{last_chg:.1f}%（15min）'
    elif last_chg >= 1.5:
        return 12, f'OI中等变化+{last_chg:.1f}%'
    return 0, f'OI平稳变化{last_chg:.1f}%'


def score_fr_reversal(sym: str, state: dict) -> tuple[int, str]:
    """FR转向：FR从负转正或绝对值>0.01% → +20"""
    fr = float(state.get('funding_rate', 0) or 0)
    # 读历史FR（如果有）
    fr_hist_key = f'fr_hist_{sym.lower()}'
    ts_state = _load_state_file()
    fr_hist = ts_state.get(fr_hist_key, [])
    fr_hist.append(fr)
    fr_hist = fr_hist[-6:]  # 保留最近6根
    ts_state[fr_hist_key] = fr_hist
    _save_state_file(ts_state)

    if len(fr_hist) >= 3:
        prev3 = fr_hist[-3:]
        # 连续3根同向且绝对值递增 = 趋势性变化
        if all(x < -0.008 for x in prev3):
            return 20, f'FR连续3根负值极端({fr:.4f}%)→逼空信号'
        if all(x > 0.01 for x in prev3):
            return 15, f'FR连续3根正值偏高({fr:.4f}%)→多头过重'
        # 刚从负转正
        if fr_hist[-2] < 0 and fr > 0:
            return 20, f'FR从负转正({fr_hist[-2]:.4f}%→{fr:.4f}%)→空头被迫平仓'

    if abs(fr) > 0.012:
        return 10, f'FR极端{fr:.4f}%'
    return 0, f'FR正常{fr:.4f}%'


def score_liq_density(sym: str, state: dict) -> tuple[int, str]:
    """清算密度激增：多层清算位密集 → +20"""
    liq_s  = float(state.get('liq_short', 0) or 0)
    liq_l  = float(state.get('liq_long', 0) or 0)
    liq_s2 = float(state.get('liq_short2', 0) or 0)
    liq_l2 = float(state.get('liq_long2', 0) or 0)
    px     = float(state.get('price', 1) or 1)

    if liq_s <= 0 or liq_l <= 0:
        return 0, '清算位数据缺失'

    # 两层清算位叠加：距离越近，密度越高
    upper_gap = (liq_s2 - liq_s) / px if liq_s2 > liq_s else 1.0
    lower_gap = (liq_l - liq_l2) / px if liq_l > liq_l2 > 0 else 1.0
    nearest = min((liq_s - px) / px, (px - liq_l) / px)

    if nearest < 0.015:  # 距最近清算位<1.5%
        return 20, f'清算位极近{nearest*100:.1f}%（触发链激活临界）'
    elif nearest < 0.025:
        return 12, f'清算位较近{nearest*100:.1f}%'
    elif upper_gap < 0.02 or lower_gap < 0.02:
        return 8, f'双层清算密集(gap={min(upper_gap,lower_gap)*100:.1f}%)'
    return 0, f'清算位正常距离{nearest*100:.1f}%'


def score_bb_release(sym: str, state: dict) -> tuple[int, str]:
    """BB宽度释放：从极度压缩开始扩张 → +15"""
    bb_w = float(state.get('bb_width', state.get('bbw', 0)) or 0)
    if bb_w <= 0:
        return 0, 'BB宽度数据缺失'

    # 读历史BB宽度
    ts_state = _load_state_file()
    bbw_key = f'bbw_hist_{sym.lower()}'
    bbw_hist = ts_state.get(bbw_key, [])
    bbw_hist.append(bb_w)
    bbw_hist = bbw_hist[-6:]
    ts_state[bbw_key] = bbw_hist
    _save_state_file(ts_state)

    if len(bbw_hist) >= 3:
        min_recent = min(bbw_hist[:-1])
        # 从极度压缩(<0.5)开始扩张
        if min_recent < 0.5 and bb_w > min_recent * 1.2:
            return 15, f'BB从压缩释放({min_recent:.3f}→{bb_w:.3f})→突破启动'
        # 极度压缩尚未释放
        if bb_w < 0.5:
            return 10, f'BB极度压缩({bb_w:.3f})<0.5→突破蓄力'

    if bb_w < 0.6:
        return 8, f'BB收窄({bb_w:.3f})'
    return 0, f'BB正常({bb_w:.3f})'


def score_gex_zf_cross(sym: str, state: dict) -> tuple[int, str]:
    """GEX ZeroFlip穿越 → +10"""
    px = float(state.get('price', 0) or 0)
    zf = float(state.get('gex_zero_flip', state.get('zero_flip', 0)) or 0)
    if zf <= 0 or px <= 0:
        return 0, 'GEX ZF数据缺失'

    dist_pct = abs(px - zf) / px
    if dist_pct < 0.005:  # 价格距ZF<0.5% = 穿越临近
        return 10, f'GEX ZF穿越临近(px={px:.0f} ZF={zf:.0f} dist={dist_pct*100:.2f}%)'
    elif dist_pct < 0.012:
        return 5, f'GEX ZF接近(dist={dist_pct*100:.2f}%)'
    return 0, f'GEX ZF距离正常({dist_pct*100:.1f}%)'


def score_macro_diverge(sym: str, state: dict) -> tuple[int, str]:
    """周线体制与4H背离 → +10（CHOP_MID但周线是BULL/BEAR = 体制即将切换）"""
    try:
        from macro_regime_engine import run_macro_regime
        macro = run_macro_regime()
        sym_macro = macro.get(sym.upper(), {})
        weekly_regime = str(sym_macro.get('macro_regime', '') or '')
        h4_regime = str(state.get('regime', '') or '')
        if 'CHOP' in h4_regime and ('BULL' in weekly_regime or 'BEAR' in weekly_regime):
            return 10, f'周线{weekly_regime}≠4H{h4_regime}→体制背离，切换临近'
    except Exception:
        pass
    return 0, '宏观体制一致或无法读取'


# ══════════════════════════════════════════════════════
# 主评分函数
# ══════════════════════════════════════════════════════

def calc_transition_score(sym: str) -> dict:
    """
    计算体制切换预判分数
    返回：score(0~100) + breakdown + direction建议
    """
    state = _load_brahma_state(sym)
    px    = float(state.get('price', 0) or 0)

    scores = {}
    notes  = {}

    s, n = score_oi_surge(sym, state);      scores['oi_surge'] = s;      notes['oi_surge'] = n
    s, n = score_fr_reversal(sym, state);   scores['fr_rev'] = s;        notes['fr_rev'] = n
    s, n = score_liq_density(sym, state);   scores['liq_density'] = s;   notes['liq_density'] = n
    s, n = score_bb_release(sym, state);    scores['bb_release'] = s;    notes['bb_release'] = n
    s, n = score_gex_zf_cross(sym, state);  scores['gex_zf'] = s;        notes['gex_zf'] = n
    s, n = score_macro_diverge(sym, state); scores['macro_div'] = s;     notes['macro_div'] = n

    total = sum(scores.values())
    triggered = [k for k, v in scores.items() if v > 0]

    # 方向推断
    fr    = float(state.get('funding_rate', 0) or 0)
    oi_sig = str((state.get('oi') or {}).get('main_signal', '') or state.get('oi_signal', '') or '')
    macro_dir = 'UNKNOWN'
    try:
        from macro_regime_engine import run_macro_regime
        m = run_macro_regime()
        macro_dir = str((m.get(sym.upper(), {}) or {}).get('macro_regime', '') or '')
    except Exception:
        pass

    if 'BULL' in macro_dir or (fr < -0.008 and 'SHORT_BUILD' not in oi_sig):
        direction = 'LONG'    # 宏观多 或 FR极度负值=逼空→做多
    elif 'BEAR' in macro_dir or (fr > 0.01 and 'LONG_BUILD' not in oi_sig):
        direction = 'SHORT'
    else:
        direction = 'BOTH'

    return {
        'sym':         sym,
        'score':       total,
        'threshold':   TRANSITION_THRESHOLD,
        'triggered':   triggered,
        'scores':      scores,
        'notes':       notes,
        'direction':   direction,
        'macro':       macro_dir,
        'px':          px,
        'ts':          time.time(),
        'alert':       total >= TRANSITION_THRESHOLD,
    }


# ══════════════════════════════════════════════════════
# Gate4.5豁免检查（供step11调用）
# ══════════════════════════════════════════════════════

def check_gate4b_exemption(sym: str) -> tuple[bool, str]:
    """
    Gate4.5: 体制切换预判豁免
    供step11_mandatory_judge.py调用
    返回: (豁免=True/False, 原因说明)
    """
    try:
        result = calc_transition_score(sym)
        if result['alert']:
            note = (f'transition_score={result["score"]}>={TRANSITION_THRESHOLD}'
                    f' 触发={result["triggered"]} → 豁免CHOP门控')
            return True, note
        return False, f'transition_score={result["score"]}<{TRANSITION_THRESHOLD}'
    except Exception as e:
        return False, f'transition_check异常: {e}'


# ══════════════════════════════════════════════════════
# 推送预警
# ══════════════════════════════════════════════════════

def _push_alert(results: list[dict]):
    ts_state = _load_state_file()
    now = time.time()
    msgs = []
    for r in results:
        if not r['alert']:
            continue
        sym = r['sym']
        last_push = ts_state.get(f'last_push_{sym}', 0)
        if now - last_push < COOLDOWN_PUSH_MIN * 60:
            continue
        ts_state[f'last_push_{sym}'] = now
        side = '🟢做多' if r['direction'] == 'LONG' else ('🔴做空' if r['direction'] == 'SHORT' else '双向')
        msgs.append(
            f"⚡ 体制切换预警 | {sym}  score={r['score']}/{TRANSITION_THRESHOLD}\n"
            f"触发维度: {'+'.join(r['triggered'])}\n"
            f"方向建议: {side}  宏观={r['macro']}\n"
            f"梵天将在下次先行信号触发时自动生成草案"
        )
    _save_state_file(ts_state)

    if msgs:
        try:
            from push_hub import push_jarvis
            push_jarvis('\n\n'.join(msgs), priority='P2')
        except Exception as e:
            print(f'[transition] 推送失败: {e}')


# ══════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════

def main(symbols=('BTC', 'ETH'), push=True, quiet=False):
    results = []
    for sym in symbols:
        r = calc_transition_score(sym)
        results.append(r)
        if not quiet:
            status = '⚡触发' if r['alert'] else '⏳等待'
            print(f'[transition] {sym} score={r["score"]:>3}/{TRANSITION_THRESHOLD} '
                  f'{status} 触发={r["triggered"]} 方向={r["direction"]}')
            for k, n in r['notes'].items():
                if r['scores'].get(k, 0) > 0:
                    print(f'  +{r["scores"][k]:>2} {k}: {n}')
    if push:
        _push_alert(results)
    return results


if __name__ == '__main__':
    # [触发优化 2026-10-28 苏摩111] 快速预检：分数远低于阈值时跳过
    import json, time as _t
    from pathlib import Path as _P
    _DATA = _P('/root/.openclaw/workspace/trading-system/data')
    _skip = True
    for _sym in ['btc','eth']:
        _sf = _DATA / f'brahma_state_{_sym}.json'
        if not _sf.exists(): _skip = False; break
        try:
            _d = json.loads(_sf.read_text())
            _age = (_t.time() - _d.get('price_ts',0))/60
            if _age > 25: _skip = False; break  # 数据过期=需要检查
            # 用state里的transition_score判断（如有）
            _ts = _d.get('transition_score', 0)
            if _ts >= 30:  # 阈值是45，>30说明接近，需要完整计算
                _skip = False; break
        except Exception: _skip = False; break
    if _skip:
        print('[regime_transition] 所有标的远离阈值，跳过')
        import sys; sys.exit(0)

    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--symbols', nargs='+', default=['BTC', 'ETH'])
    p.add_argument('--no-push', action='store_true')
    p.add_argument('--quiet',   action='store_true')
    args = p.parse_args()
    main(args.symbols, push=not args.no_push, quiet=args.quiet)
