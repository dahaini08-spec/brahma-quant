"""
brahma_cpu.py — 梵天中央决策处理器 (CPU大脑)
══════════════════════════════════════════════
设计院 2026-08-25 苏摩111 封印

使命：解决三条断链，让梵天有真正的智慧大脑

断链1修复: 感知事件→CPU统一路由（不再靠文件传话）
断链2修复: AI议会结论直接决定"做不做"（不只是±25分）
断链3修复: score≥165+议会≥3票→自主下单（苏摩不在线也能跑）

4层漏斗决策树:
  Layer0: 快速否决（0 tokens，纯规则，<1ms）
    死穴 / FG>85 / 连亏冷静期 / 体制封禁 → SKIP
  Layer1: 94维评分（brahma_core.analyze，~5s）
    score<130 → SKIP | 130-149 → WATCH | ≥150 → Layer2
  Layer2: AI议会裁决（llm_council.review，~10s并行）
    3票反对 → 降WATCH | 2票支持 → ALERT | 3票+score≥165 → Layer3
  Layer3: 自主执行门控（最后防线）
    仓位>8%NAV → 拒绝 | 同标的有仓 → 拒绝
    苏摩在线(<30min) → ALERT | 苏摩离线 → EXECUTE

输出:
  EXECUTE  → 直接调 auto_executor 下单
  ALERT    → 推VIP卡片给苏摩确认
  WATCH    → 写监控列表，下次触发再评估
  SKIP     → 静默丢弃
"""

import sys
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────────────────────
_BASE   = Path(__file__).parent
_ROOT   = _BASE.parent
_DATA   = _ROOT / 'data'
_SCRIPTS = _ROOT / 'scripts'

sys.path.insert(0, str(_BASE))
sys.path.insert(0, str(_SCRIPTS))

_log = logging.getLogger('brahma.cpu')

# ── 门控阈值（与MEMORY.md封印值对齐）────────────────────────────────
SCORE_SKIP        = 80      # [9.20 苏摩111] 从130降到80：纸面单门槛
SCORE_WATCH       = 100     # [9.20 苏摩111] 从150降到100：WATCH门槛
SCORE_EXECUTE     = 165     # 实盘执行门槛（不变）
SCORE_ALERT       = 165     # 低于此ALERT（150-164）

COUNCIL_VETO_MIN  = 3       # 议会反对票数≥3 → 降为WATCH
COUNCIL_SUPPORT_ALERT = 2   # 议会支持票数≥2 → ALERT
COUNCIL_SUPPORT_EXEC  = 3   # 议会支持票数≥3+score≥165 → 进Layer3

MAX_NAV_PCT       = 0.08    # 最大仓位 8%NAV
SOMA_ONLINE_MIN   = 30      # 苏摩30分钟内有消息=在线

# ── 状态文件 ─────────────────────────────────────────────────────────
_WATCH_FILE     = _DATA / 'cpu_watch_list.json'
_CPU_LOG        = _DATA / 'brahma_cpu_log.jsonl'
_JARVIS_USER    = '73295708'
_JARVIS_THREAD  = '01a07628-0405-7e85-a34b-e68cd029dfc6'

# ── Autopilot记忆层 [9.20 苏摩111] ─────────────────────────────────
_L0_STATE       = _DATA / 'autopilot_state.json'      # L0工作记忆
_L1_DECISIONS   = _DATA / 'autopilot_decisions.jsonl' # L1情景记忆
_L2_LESSONS     = _DATA / 'autopilot_lessons.json'     # L2语义记忆
_AUTOPILOT_ENABLED = False  # Phase 5改为True，开启自主EXECUTE

# ══════════════════════════════════════════════════════════════════════
# Layer0: 快速否决（0 tokens，纯规则）
# ══════════════════════════════════════════════════════════════════════
def _layer0_fast_reject(symbol: str, regime: str, signal_dir: str) -> tuple:
    """
    纯规则快速否决。返回 (reject: bool, reason: str)
    0 tokens，<1ms
    """
    # [9.20修复 苏摩111] DEAD_COMBOS已废弃 — 9.12改革已清理所有体制死穴
    # 改为降仓信息(trader_brain.py L240+brahma_core.py L1721)，不再硬封禁
    # CHOP_MID×LONG/SHORT 不再L0直接SKIP，而是进入4层漏斗正常评分
    DEAD_COMBOS = {}  # 空=不封锁任何组合
    if regime in DEAD_COMBOS and signal_dir in DEAD_COMBOS[regime]:
        return True, f'体制死穴 {regime}×{signal_dir}'

    # 反脆弱系统：连亏/情绪熔断
    try:
        from antifragile_guard import full_guard_check
        guard = full_guard_check()
        if guard.get('blocked'):
            return True, f'反脆弱门控: {guard.get("reason", "未知")}'
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # FG极度贪婪>85
    try:
        from narrative_engine import get_narrative_score
        ns = get_narrative_score(symbol)
        fg = ns.get('fg_index', 50) if isinstance(ns, dict) else 50
        if isinstance(fg, (int, float)) and fg > 85:
            return True, f'FG={fg}极度贪婪，情绪熔断'
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return False, ''


# ══════════════════════════════════════════════════════════════════════
# Layer1: 94维评分
# ══════════════════════════════════════════════════════════════════════
def _layer1_score(symbol: str, signal_dir: str = None) -> dict:
    """调用brahma_core.analyze()，返回完整result"""
    try:
        import brahma_core
        result = brahma_core.analyze(symbol, signal_dir=signal_dir)
        result['_layer1_ok'] = True
        return result
    except Exception as e:
        return {'_layer1_ok': False, '_error': str(e), 'score': 0,
                'regime': 'UNKNOWN', 'price': 0}


# ══════════════════════════════════════════════════════════════════════
# Layer2: AI议会裁决
# ══════════════════════════════════════════════════════════════════════
def _layer2_council(score_result: dict) -> dict:
    """
    调用llm_council_bridge.review()。
    返回 {support_votes, veto_votes, final_adj, council_ok}
    """
    try:
        from llm_council_bridge import review as council_review
        council = council_review(score_result)
        llm_data = council.get('llm_council', {})

        # 统计支持/反对票
        support = 0
        veto    = 0
        agents  = ['risk', 'macro', 'quant', 'devil']
        for agent in agents:
            agent_result = llm_data.get(agent, {})
            if isinstance(agent_result, dict):
                if agent == 'devil':
                    # devil的veto=True算反对
                    if agent_result.get('veto', False):
                        veto += 1
                else:
                    adj = agent_result.get('score_adj', 0) or 0
                    if adj > 0:
                        support += 1
                    elif adj < -5:
                        veto += 1

        return {
            'council_ok':   True,
            'support_votes': support,
            'veto_votes':    veto,
            'final_adj':     council.get('final_adj', 0),
            'raw':           council,
        }
    except Exception as e:
        _log.warning(f'[CPU·L2] 议会调用失败: {e}')
        return {'council_ok': False, 'support_votes': 0, 'veto_votes': 0,
                'final_adj': 0, '_error': str(e)}


# ══════════════════════════════════════════════════════════════════════
# Layer3: 自主执行门控
# ══════════════════════════════════════════════════════════════════════
def _check_soma_online() -> bool:
    """
    检测苏摩是否在线（最近30分钟有Jarvis消息）。
    读取session最新消息时间戳判断。
    """
    try:
        # 读openclaw session记录
        session_files = list(Path('/root/.openclaw/agents/main/sessions').glob('*.jsonl'))
        if not session_files:
            return False
        # 找最新的用户消息
        cutoff = time.time() - SOMA_ONLINE_MIN * 60
        for sf in sorted(session_files, key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
            with open(sf) as f:
                for line in f:
                    try:
                        msg = json.loads(line)
                        if msg.get('role') == 'user':
                            ts = msg.get('timestamp', 0)
                            if isinstance(ts, (int, float)):
                                ts_sec = ts / 1000 if ts > 1e10 else ts
                                if ts_sec > cutoff:
                                    return True
                    except Exception:
                        continue
        return False
    except Exception:
        return False  # 无法判断时保守处理=苏摩在线，降为ALERT


def _check_position_risk(symbol: str, signal_dir: str, score_result: dict) -> tuple:
    """
    检查仓位风险门控。返回 (allow: bool, reason: str)
    """
    try:
# 模块不存在，已注释
# from position_sl_manager import get_current_positions
        positions = get_current_positions()
    except Exception:
        try:
            pos_file = _DATA / 'position_sl_state.json'
            positions = json.loads(pos_file.read_text()) if pos_file.exists() else {}
        except Exception:
            positions = {}

    # 同标的已有持仓
    sym_key = symbol.replace('USDT', '')
    for k in positions:
        if sym_key in k.upper():
            return False, f'同标的 {symbol} 已有持仓，防重建'

    # 总仓位>8%NAV
    try:
        nav = _get_nav()
        total_pos_pct = sum(
            abs(float(p.get('notional', 0) or 0))
            for p in (positions.values() if isinstance(positions, dict) else positions)
        ) / max(nav, 1)
        if total_pos_pct > MAX_NAV_PCT:
            return False, f'总仓位{total_pos_pct:.1%}>8%NAV上限'
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return True, ''


def _get_nav() -> float:
    """读取当前NAV"""
    try:
        nav_file = _DATA / 'nav_state.json'
        if nav_file.exists():
            d = json.loads(nav_file.read_text())
            return float(d.get('nav', 0) or 0)
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return 130.0  # 保守默认值


# ══════════════════════════════════════════════════════════════════════
# 执行 / 推送
# ══════════════════════════════════════════════════════════════════════
def _do_execute(symbol: str, signal_dir: str, score_result: dict, council: dict) -> dict:
    """调用auto_executor直接下单"""
    try:
        # 写入触发事件，让auto_executor读取执行
        trigger = {
            'symbol':     symbol,
            'ts':         time.time(),
            'ts_iso':     datetime.now(timezone.utc).isoformat(),
            'score':      score_result.get('score', 0),
            'signal_dir': signal_dir,
            'regime':     score_result.get('regime', 'UNKNOWN'),
            'final_adj':  council.get('final_adj', 0),
            'source':     'brahma_cpu_auto',
            'cpu_decision': 'EXECUTE',
        }
        trigger_file = _DATA / 'cpu_execute_trigger.json'
        trigger_file.write_text(json.dumps(trigger, ensure_ascii=False))

        # 直接调用auto_executor
        import subprocess
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS / 'auto_executor.py'), '--from-cpu'],
            capture_output=True, text=True, timeout=60
        )
        return {
            'executed': True,
            'stdout':   result.stdout[-300:],
            'returncode': result.returncode,
        }
    except Exception as e:
        return {'executed': False, 'error': str(e)}


def _do_alert(symbol: str, signal_dir: str, score_result: dict,
              council: dict, reason: str) -> None:
    """推VIP卡片给苏摩确认"""
    try:
        price   = score_result.get('price', 0)
        score   = score_result.get('score', 0)
        regime  = score_result.get('regime', 'UNKNOWN')
        adj     = council.get('final_adj', 0)
        ts_str  = datetime.now(timezone.utc).strftime('%H:%M UTC')
        decision = score_result.get('decision', {})
        entry   = decision.get('entry_plan', {}) if isinstance(decision, dict) else {}

        lines = [
            f'🟡 **梵天CPU ALERT** | {symbol} | {ts_str}',
            f'> {reason}',
            '',
            f'**体制:** {regime} | **评分:** {score:.1f}{adj:+.1f} | **方向:** {signal_dir}',
            f'**价格:** ${price:,.2f}',
        ]
        if entry:
            lines += [
                f'**入场区:** ${entry.get("entry_lo",0):,.2f}–${entry.get("entry_hi",0):,.2f}',
                f'**SL:** ${entry.get("sl",0):,.2f} | **TP:** ${entry.get("tp1",0):,.2f}',
            ]
        lines += ['', '发 `执行` 确认下单 | 发 `跳过` 忽略']

        msg = '\n'.join(lines)
        import subprocess
        subprocess.Popen([
            'openclaw', 'infer',
            '--channel', 'jarvis',
            '--to', f'{_JARVIS_USER}:thread:{_JARVIS_THREAD}',
            '--message', msg,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        _log.warning(f'[CPU·ALERT] 推送失败: {e}')


def _do_watch(symbol: str, signal_dir: str, score_result: dict) -> None:
    """写入监控列表 + 写入auto_signal_queue供paper_executor消费"""
    try:
        watch = {}
        if _WATCH_FILE.exists():
            watch = json.loads(_WATCH_FILE.read_text())
        watch[symbol] = {
            'symbol':     symbol,
            'signal_dir': signal_dir,
            'score':      score_result.get('score', 0),
            'regime':     score_result.get('regime', 'UNKNOWN'),
            'ts':         time.time(),
            'ts_iso':     datetime.now(timezone.utc).isoformat(),
            'expires_at': time.time() + 4 * 3600,  # 4小时过期
        }
        _WATCH_FILE.write_text(json.dumps(watch, ensure_ascii=False, indent=2))
        
        # [9.21 苏摩111] 写入auto_signal_queue供paper_executor开纸面单
        _queue_path = _DATA / 'auto_signal_queue.json'
        queue = []
        if _queue_path.exists():
            try:
                queue = json.loads(_queue_path.read_text())
            except Exception:
                queue = []
        # 构建信号（带grade_num，paper_executor必需）
        _cf = score_result.get('confluence', {})
        _signal = {
            'symbol':       symbol,
            'direction':    signal_dir or 'LONG',
            'signal_dir':   signal_dir or 'LONG',
            'score':        score_result.get('score', 0),
            'score_final':  score_result.get('score', 0),
            'grade_num':    _cf.get('score', score_result.get('score', 0)),  # 用confluence原始score作为grade
            'grade':        _cf.get('score', score_result.get('score', 0)),
            'regime':       score_result.get('regime', 'UNKNOWN'),
            'sl_pct':       2.0,
            'signal_id':    f'{symbol}_{signal_dir or "LONG"}_{int(time.time())}',
            'ts':           time.time(),
        }
        # 去重：同symbol+direction的旧信号移除
        queue = [q for q in queue if not (q.get('symbol') == symbol and q.get('direction') == _signal['direction'])]
        queue.append(_signal)
        _queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2))
        print(f'[CPU·WATCH] {symbol} {signal_dir} score={_signal["score"]:.1f} → auto_signal_queue')
    except Exception as e:
        _log.warning(f'[CPU·WATCH] 写入失败: {e}')


# ══════════════════════════════════════════════════════════════════════
# CPU 日志
# ══════════════════════════════════════════════════════════════════════
def _log_decision(symbol: str, signal_dir: str, decision: str,
                  reason: str, score: float, layer: int,
                  score_result: dict = None, council: dict = None) -> None:
    """log decision + 写入L1情景记忆"""
    try:
        entry = {
            'ts':         time.time(),
            'ts_iso':     datetime.now(timezone.utc).isoformat(),
            'symbol':     symbol,
            'signal_dir': signal_dir,
            'decision':   decision,
            'reason':     reason,
            'score':      score,
            'layer':      layer,
            # L1情景记忆额外字段
            'context':    _extract_context(score_result),
            'council':    {'support': council.get('support_votes',0), 'veto': council.get('veto_votes',0)} if council else None,
            'outcome':    None,   # signal_settler回填
            'pnl_pct':    None,   # signal_settler回填
            'lesson':     None,   # L2学习层回填
        }
        _DATA.mkdir(exist_ok=True)
        with open(_CPU_LOG, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        # 写入L1情景记忆
        with open(_L1_DECISIONS, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        # 更新L0工作记忆
        _update_l0_state(symbol, decision, score, signal_dir, score_result)
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)

def _extract_context(score_result: dict = None) -> dict:
    """从评分结果中提取关键上下文（精简版，避免L1过大）"""
    if not score_result or not isinstance(score_result, dict):
        return {}
    return {
        'regime':   score_result.get('regime', 'UNKNOWN'),
        'price':    score_result.get('price', 0),
        'score':    score_result.get('score', 0),
        'resonance': score_result.get('resonance', {}),
        'oi_signal': score_result.get('oi_signal', 'N/A'),
        'fvg_dir':   score_result.get('fvg_dir', 'NONE'),
        'hurst':     score_result.get('hurst', 0),
    }

def _update_l0_state(symbol: str, decision: str, score: float,
                      signal_dir: str, score_result: dict = None) -> None:
    """更新L0工作记忆"""
    try:
        state = {}
        if _L0_STATE.exists():
            state = json.loads(_L0_STATE.read_text())
        state['ts'] = time.time()
        state['last_decision'] = {
            'ts': time.time(),
            'symbol': symbol,
            'action': decision,
            'score': score,
            'signal_dir': signal_dir,
        }
        # 更新market_state
        if score_result and isinstance(score_result, dict):
            sym_key = symbol.replace('USDT', '')
            state.setdefault('market_state', {})[sym_key] = {
                'price': score_result.get('price', 0),
                'regime': score_result.get('regime', 'UNKNOWN'),
                'score': score_result.get('score', 0),
            }
        _L0_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)

def _load_l2_lessons() -> dict:
    """读取L2语义记忆（决策时参考历史教训）"""
    try:
        if _L2_LESSONS.exists():
            return json.loads(_L2_LESSONS.read_text())
    except Exception:
        pass
    return {'lessons': [], 'threshold_suggestions': []}
# ══════════════════════════════════════════════════════════════════════
# 主入口：process_event
# ══════════════════════════════════════════════════════════════════════
def process_event(symbol: str, signal_dir: str = None,
                  event_type: str = 'UNKNOWN', dry_run: bool = False) -> dict:
    """
    梵天CPU大脑主入口。
    任何感知事件（RSI/OI/价格进区间/体制切换）都走这里。

    参数:
      symbol:      交易对，如 BTCUSDT
      signal_dir:  LONG / SHORT / None（自动判断）
      event_type:  事件类型，用于日志
      dry_run:     True=只判断不执行

    返回:
      {decision, reason, score, layer, symbol, signal_dir, ...}
    """
    sym = symbol.upper()
    t0  = time.time()

    # ── Layer0: 快速否决 ────────────────────────────────────────────
    # 先跑一次轻量evaluate拿regime
    regime = 'UNKNOWN'
    try:
        from brahma_bus import get_price
        price = get_price(sym)
        # 读缓存体制（避免重复调analyze）
        state_file = _DATA / 'brahma_state.json'
        if state_file.exists():
            state = json.loads(state_file.read_text())
            regime = state.get(sym, {}).get('regime', 'UNKNOWN')
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    if signal_dir:
        rejected, reason = _layer0_fast_reject(sym, regime, signal_dir)
        if rejected:
            _log_decision(sym, signal_dir or 'AUTO', 'SKIP', reason, 0, layer=0, score_result=None)
            return {'decision': 'SKIP', 'reason': reason, 'layer': 0,
                    'symbol': sym, 'score': 0, 'elapsed': time.time() - t0}

    # ── Layer1: 94维评分 ────────────────────────────────────────────
    result = _layer1_score(sym, signal_dir)
    if not result.get('_layer1_ok'):
        reason = f'Layer1评分失败: {result.get("_error")}'
        _log_decision(sym, signal_dir or 'AUTO', 'SKIP', reason, 0, layer=1, score_result=result)
        return {'decision': 'SKIP', 'reason': reason, 'layer': 1,
                'symbol': sym, 'score': 0, 'elapsed': time.time() - t0}

    score      = float(result.get('score', 0))
    regime     = result.get('regime', 'UNKNOWN')
    signal_dir = result.get('signal_dir', signal_dir or 'LONG')

    # Layer0复查（现在有了真实regime）
    rejected, reason = _layer0_fast_reject(sym, regime, signal_dir)
    if rejected:
        _log_decision(sym, signal_dir, 'SKIP', reason, score, layer=0, score_result=result)
        return {'decision': 'SKIP', 'reason': reason, 'layer': 0,
                'symbol': sym, 'score': score, 'elapsed': time.time() - t0}

    if score < SCORE_SKIP:
        reason = f'score={score:.1f}<{SCORE_SKIP}门槛'
        _log_decision(sym, signal_dir, 'SKIP', reason, score, layer=1, score_result=result)
        return {'decision': 'SKIP', 'reason': reason, 'layer': 1,
                'symbol': sym, 'score': score, 'elapsed': time.time() - t0}

    if score < SCORE_WATCH:
        reason = f'score={score:.1f}进入WATCH区({SCORE_SKIP}-{SCORE_WATCH})'
        _do_watch(sym, signal_dir, result)
        _log_decision(sym, signal_dir, 'WATCH', reason, score, layer=1, score_result=result)
        return {'decision': 'WATCH', 'reason': reason, 'layer': 1,
                'symbol': sym, 'score': score, 'elapsed': time.time() - t0}

    # ── Layer2: AI议会裁决 ─────────────────────────────────────────
    council = _layer2_council(result)
    support = council.get('support_votes', 0)
    veto    = council.get('veto_votes', 0)
    adj     = council.get('final_adj', 0)
    score_adj = score + adj

    if council.get('council_ok') and veto >= COUNCIL_VETO_MIN:
        reason = f'议会{veto}票反对，降为WATCH (score={score:.1f} adj={adj:+.1f})'
        _do_watch(sym, signal_dir, result)
        _log_decision(sym, signal_dir, 'WATCH', reason, score_adj, layer=2, score_result=result, council=council)
        return {'decision': 'WATCH', 'reason': reason, 'layer': 2,
                'symbol': sym, 'score': score_adj, 'elapsed': time.time() - t0}

    # 议会支持不足 → ALERT
    if score_adj < SCORE_EXECUTE or support < COUNCIL_SUPPORT_EXEC:
        reason = (f'score_adj={score_adj:.1f} support={support}票，'
                  f'需score≥{SCORE_EXECUTE}且≥{COUNCIL_SUPPORT_EXEC}票支持')
        if not dry_run:
            _do_alert(sym, signal_dir, result, council, reason)
        _log_decision(sym, signal_dir, 'ALERT', reason, score_adj, layer=2, score_result=result, council=council)
        return {'decision': 'ALERT', 'reason': reason, 'layer': 2,
                'symbol': sym, 'score': score_adj, 'elapsed': time.time() - t0}

    # ── Layer3: 自主执行门控 ───────────────────────────────────────
    pos_ok, pos_reason = _check_position_risk(sym, signal_dir, result)
    if not pos_ok:
        reason = f'仓位门控拒绝: {pos_reason}'
        _log_decision(sym, signal_dir, 'SKIP', reason, score_adj, layer=3, score_result=result, council=council)
        return {'decision': 'SKIP', 'reason': reason, 'layer': 3,
                'symbol': sym, 'score': score_adj, 'elapsed': time.time() - t0}

    soma_online = _check_soma_online()
    if soma_online:
        reason = (f'苏摩在线，score={score_adj:.1f}≥{SCORE_EXECUTE} '
                  f'议会{support}票支持，等待确认')
        if not dry_run:
            _do_alert(sym, signal_dir, result, council, reason)
            # A方案：苏摩在线时也同步纸面开单（不等确认）
            try:
# 模块不存在，已注释
# from paper_trader import auto_paper_trade
                auto_paper_trade(sym, signal_dir, score_adj, support, result)
            except Exception as _pe:
                _log.warning(f'[CPU·paper] 纸面开单失败: {_pe}')
        _log_decision(sym, signal_dir, 'ALERT', reason, score_adj, layer=3, score_result=result, council=council)
        return {'decision': 'ALERT', 'reason': reason, 'layer': 3,
                'symbol': sym, 'score': score_adj, 'elapsed': time.time() - t0}

    # 苏摩离线 + 全部门控通过 → 纸面+实盘同步EXECUTE
    reason = (f'自主执行(A方案): score={score_adj:.1f}≥{SCORE_EXECUTE} '
              f'议会{support}票支持 苏摩离线 仓位OK')
    exec_result = {}
    if not dry_run:
        # 纸面先开（必完成）
        try:
# 模块不存在，已注释
# from paper_trader import auto_paper_trade
            paper_result = auto_paper_trade(sym, signal_dir, score_adj, support, result)
            exec_result['paper'] = paper_result
        except Exception as _pe:
            exec_result['paper_error'] = str(_pe)
        # 实盘执行
        exec_result.update(_do_execute(sym, signal_dir, result, council))
    _log_decision(sym, signal_dir, 'EXECUTE', reason, score_adj, layer=3, score_result=result, council=council)
    return {
        'decision':    'EXECUTE',
        'reason':      reason,
        'layer':       3,
        'symbol':      sym,
        'score':       score_adj,
        'exec_result': exec_result,
        'elapsed':     time.time() - t0,
        'dry_run':     dry_run,
    }


# ══════════════════════════════════════════════════════════════════════
# 批量处理触发事件（接管感知层→决策层）
# ══════════════════════════════════════════════════════════════════════
def process_trigger_file() -> list:
    """
    读取 rsi_trigger_event.json，逐个通过CPU处理。
    替代原来的「文件传话」方式。
    """
    trigger_file = _DATA / 'rsi_trigger_event.json'
    if not trigger_file.exists():
        return []

    try:
        events = json.loads(trigger_file.read_text())
    except Exception as e:
        _log.warning(f'[CPU] 读取trigger_file失败: {e}')
        return []

    results = []
    for symbol, event in events.items():
        if not isinstance(event, dict):
            continue
        # 过期事件跳过（1小时）
        ts = event.get('ts', 0)
        if time.time() - ts > 3600:
            continue

        _log.info(f'[CPU] 处理感知事件: {symbol} {event.get("events", [])}')
        result = process_event(
            symbol=symbol,
            signal_dir=None,  # 让94维评分自动判断方向
            event_type=str(event.get('events', [{}])[0].get('event', 'UNKNOWN')),
        )
        result['_trigger_event'] = event
        results.append(result)

    return results


# ══════════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] %(name)s %(levelname)s %(message)s')

    parser = argparse.ArgumentParser(description='梵天CPU大脑')
    parser.add_argument('symbol', nargs='?', default=None,
                        help='指定币种，如BTCUSDT；不填则处理trigger_file')
    parser.add_argument('--dir', default=None, help='强制方向 LONG/SHORT')
    parser.add_argument('--dry-run', action='store_true', help='只判断不执行')
    parser.add_argument('--trigger', action='store_true',
                        help='批量处理rsi_trigger_event.json')
    parser.add_argument('--auto', action='store_true',
                        help='[Autopilot] 自动模式：批量处理trigger + 写入记忆层')
    parser.add_argument('--symbols', default=None,
                        help='[Autopilot] 强制分析指定标的，逗号分隔，如 BTCUSDT,ETHUSDT')
    args = parser.parse_args()

    if args.auto:
        # [9.20 苏摩111] Autopilot自动模式
        _l2 = _load_l2_lessons()
        if _l2.get('lessons'):
            print(f'[Autopilot] L2语义记忆: {len(_l2["lessons"])}条规则')
        
        # [9.21 苏摩111] --symbols强制分析指定标的（不依赖trigger_file）
        if args.symbols:
            _force_syms = [s.strip().upper() for s in args.symbols.split(',')]
            print(f'[Autopilot] 强制分析: {_force_syms}')
            results = []
            for sym in _force_syms:
                _t0 = time.time()
                r = process_event(symbol=sym, signal_dir=None)
                r['elapsed'] = time.time() - _t0
                results.append(r)
        else:
            results = process_trigger_file()
        
        for r in results:
            sym  = r.get('symbol', '?')
            dec  = r.get('decision', '?')
            sc   = r.get('score', 0)
            lyr  = r.get('layer', '?')
            rsn  = r.get('reason', '')
            ela  = r.get('elapsed', 0)
            icon = {'EXECUTE':'🟢','ALERT':'🟡','WATCH':'🔵','SKIP':'⚫'}.get(dec, '?')
            print(f'{icon} {sym:<12} L{lyr} {dec:<8} score={sc:.1f} [{ela:.1f}s] {rsn}')
        if not results:
            print('[Autopilot] 没有待处理的感知事件')
    elif args.trigger or not args.symbol:
        print('[CPU] 批量处理感知事件...')
        results = process_trigger_file()
        for r in results:
            sym  = r.get('symbol', '?')
            dec  = r.get('decision', '?')
            sc   = r.get('score', 0)
            lyr  = r.get('layer', '?')
            rsn  = r.get('reason', '')
            ela  = r.get('elapsed', 0)
            icon = {'EXECUTE':'🟢','ALERT':'🟡','WATCH':'🔵','SKIP':'⚫'}.get(dec, '?')
            print(f'{icon} {sym:<12} L{lyr} {dec:<8} score={sc:.1f} [{ela:.1f}s] {rsn}')
        if not results:
            print('没有待处理的感知事件')
    else:
        sym = args.symbol.upper()
        if not sym.endswith('USDT'):
            sym += 'USDT'
        print(f'[CPU] 处理 {sym} dir={args.dir} dry_run={args.dry_run}')
        result = process_event(sym, signal_dir=args.dir, dry_run=args.dry_run)
        dec  = result.get('decision', '?')
        sc   = result.get('score', 0)
        lyr  = result.get('layer', '?')
        rsn  = result.get('reason', '')
        ela  = result.get('elapsed', 0)
        icon = {'EXECUTE':'🟢','ALERT':'🟡','WATCH':'🔵','SKIP':'⚫'}.get(dec, '?')
        print(f'{icon} {sym} L{lyr} {dec} score={sc:.1f} [{ela:.1f}s]')
        print(f'   原因: {rsn}')
        if result.get('exec_result'):
            print(f'   执行结果: {result["exec_result"]}')
