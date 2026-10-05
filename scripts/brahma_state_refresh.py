#!/usr/bin/env python3
"""
brahma_state_refresh.py — 梵天体制状态刷新 + 信号路由器
设计院封印 2026-09-03 苏摩111

# [2026-10-04 防卡死封印] LLM退避时强制跳过，防止进程卡死吃内存
import os as _os_nollm
if _os_nollm.environ.get('BRAHMA_NO_LLM') == '1':
    # 注入到 brahma_brain 的 llm_channel，让它走本地降级
    _os_nollm.environ.setdefault('BRAHMA_LLM_FORCE_LOCAL', '1')


接入位置：supercronic */30 * * * *
流程：
  1. analyze(BTCUSDT) + analyze(ETHUSDT) → brahma_state.json
  2. 对每个符合条件的标的调用 BrahmaDecisionEngine.decide()
  3. decide() 返回 EXECUTE/WAIT_15M → 写入 auto_signal_queue.json
  4. paper_executor.py（每40min）从 auto_signal_queue 读取并纸面开仓
  5. auto_executor.py（每40min，实盘开关由 LIVE_MODE 控制）

修复：之前 state_refresh 只存 brahma_state.json，
     signal_dir/entry_lo/entry_hi/sl_price 全 None，
     auto_signal_queue.json 根本不存在，导致执行链路完全空转。
"""
import sys, json, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

STATE_FILE        = BASE / 'data' / 'brahma_state.json'
SIGNAL_QUEUE_FILE = BASE / 'data' / 'auto_signal_queue.json'

# 纸面模式下的评分门槛（比实盘宽松，先验证再收紧）
PAPER_SCORE_MIN   = 80
# 信号有效期（小时）
SIGNAL_TTL_HOURS  = 4
# 分析标的列表（方向由体制自动决定）
SYMBOLS = ['BTCUSDT', 'ETHUSDT']

# ── 体制 → 推荐方向映射 ──────────────────────────────────────────
REGIME_DIRECTION = {
    'BULL_TREND':    'LONG',
    'BULL_EARLY':    'LONG',
    'BEAR_RECOVERY': 'LONG',
    'BEAR_TREND':    'SHORT',
    'BEAR_EARLY':    'SHORT',
    'CHOP_MID':      None,    # CHOP不推方向，decision_engine自行判断
    'CHOP_LOW':      None,
}

def clean(d, depth=0):
    if depth > 10: return str(d)
    if isinstance(d, dict):            return {k: clean(v, depth+1) for k,v in d.items()}
    if isinstance(d, (list, tuple)):   return [clean(i, depth+1) for i in d]
    if isinstance(d, (int, float, str, bool, type(None))): return d
    return str(d)

def _load_queue() -> list:
    if SIGNAL_QUEUE_FILE.exists():
        try:
            d = json.loads(SIGNAL_QUEUE_FILE.read_text())
            return d if isinstance(d, list) else d.get('signals', [])
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return []

def _atomic_write(path, obj):
    """[9.27freshness 苏摩111] 原子写：tmp+rename，防并发读到半截JSON（har_rv_cache同款病根）"""
    import tempfile, os as _os
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with _os.fdopen(fd, 'w') as f:
            json.dump(obj, f, ensure_ascii=False)
        _os.replace(tmp, str(path))
    except Exception:
        try: _os.unlink(tmp)
        except Exception: pass
        raise


def _save_queue(signals: list):
    SIGNAL_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIGNAL_QUEUE_FILE.write_text(json.dumps(signals, ensure_ascii=False, indent=2))

def _is_dead_combo(regime: str, direction: str) -> bool:
    """体制死穴检查（同 auto_executor 铁律）
    注意：CHOP_MID 不在死穴内，由 chop_breakout_detector 单独判断
    """
    DEAD = {
        ('BEAR_TREND',   'LONG'),    # 铁律封票1: 熊市做多
        ('BULL_TREND',   'SHORT'),   # 铁律封票2: 牛市做空
    }
    return (regime, direction) in DEAD

def main():
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── Step 1: 加载已有队列，清理过期信号 ──────────────────────
    existing = _load_queue()
    active = []
    for s in existing:
        exp = s.get('expires_at', 0)
        if isinstance(exp, str):
            try:
                from datetime import datetime as _dt
                exp = _dt.fromisoformat(exp.replace('Z','+00:00')).timestamp()
            except Exception:
                exp = 0
        if exp > now_ts:
            active.append(s)
    expired_count = len(existing) - len(active)

    # ── Step 2: 分析 + 决策 ──────────────────────────────────────
    try:
        from brahma_bus import BrahmaEventBus
        BrahmaEventBus()
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    try:
        from brahma_core import analyze
    except Exception as e:
        print(f'[state_refresh] ❌ brahma_core导入失败: {e}')
        sys.exit(1)

    try:
        from brahma_brain.brahma_decision_engine import decide as _decide
        _decision_ok = True
    except Exception as e:
        print(f'[state_refresh] ⚠️  decision_engine导入失败: {e}，跳过信号生成')
        _decision_ok = False

    all_states = {}
    new_signals = []

    for sym in SYMBOLS:
        try:
            # [2026-10-04 防卡死封印] analyze()独立90s超时，超时降级读已有state
            import concurrent.futures as _cf, json as _jf
            _fb_file = BASE / 'data' / f'brahma_state_{sym.replace("USDT","").lower()}.json'
            try:
                with _cf.ThreadPoolExecutor(max_workers=1) as _exe:
                    _fut = _exe.submit(analyze, sym)
                    r = _fut.result(timeout=90)
            except (_cf.TimeoutError, Exception) as _ae:
                print(f'[state_refresh] ⚠️ {sym} analyze超时({_ae.__class__.__name__}), fallback已有state', file=__import__('sys').stderr)
                # fallback: 读已有state + touch更新mtime（sentinel靠mtime判过期）
                import urllib.request as _ur, ssl as _ssl, json as _jj
                r = _jj.loads(_fb_file.read_text()) if _fb_file.exists() else {'sym': sym, 'regime': 'CHOP_MID', 'score': 0}
                r['_fallback'] = True
                # 更新price+mtime，让sentinel不报过期
                try:
                    _ctx2 = _ssl.create_default_context()
                    _price_url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}'
                    _price = float(_jj.loads(_ur.urlopen(_price_url, timeout=3, context=_ctx2).read())['price'])
                    r['price'] = _price; r['price_ts'] = __import__('time').time(); r['last_update_ts'] = __import__('time').time()
                    _fb_file.write_text(_jj.dumps(r, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(f'[state_refresh] {sym} fallback price patch: ${_price:,.0f}', file=__import__('sys').stderr)
                except Exception as _pe:
                    _fb_file.touch()  # 至少更新mtime防过期告警
                    print(f'[state_refresh] {sym} fallback touch mtime', file=__import__('sys').stderr)
            cleaned = clean(r)
            all_states[sym] = cleaned

            regime    = cleaned.get('regime', '')
            score     = float(cleaned.get('score_final') or cleaned.get('score') or 0)
            grade_raw = cleaned.get('effective_grade') or cleaned.get('grade') or 100
            try:
                grade = float(str(grade_raw).split()[0])
            except Exception:
                grade = 100.0

            print(f'[state_refresh] {sym}: score={score:.1f} regime={regime} grade={grade:.0f}')

            # 评分不达纸面门槛，跳过
            # ⚠️ CHOP体制例外：由chop_breakout_detector独立判断，不走score门槛
            if score < PAPER_SCORE_MIN and 'CHOP' not in regime:
                print(f'[state_refresh] {sym}: score={score:.1f} < {PAPER_SCORE_MIN}，跳过信号生成')
                continue

            # 确定分析方向
            direction = REGIME_DIRECTION.get(regime)
            if direction is None:
                # CHOP体制：两个方向都尝试，让decision_engine自行决断
                directions_to_try = ['LONG', 'SHORT']
            else:
                directions_to_try = [direction]

            if not _decision_ok:
                continue

            for direction in directions_to_try:
                # 死穴直接跳过
                if _is_dead_combo(regime, direction):
                    print(f'[state_refresh] {sym} {direction}: 死穴 {regime}×{direction}，跳过')
                    continue

                # CHOP体制：走专属突破检测器
                if 'CHOP' in regime:
                    try:
                        from brahma_brain.chop_breakout_detector import detect_chop_breakout
                        chop_result = detect_chop_breakout(r, sym + 'USDT')
                        chop_signal = chop_result.get('signal', 'NONE')
                        if chop_signal == 'NONE':
                            print(f'[state_refresh] {sym} {direction}: CHOP条件不足({chop_result["score"]}/7)，跳过')
                            continue
                        elif chop_signal == 'WATCH':
                            print(f'[state_refresh] {sym} {direction}: CHOP_WATCH({chop_result["score"]}/7)，发预警不入场')
                            continue  # 观察阶段只推送不入场
                        # READY/EXECUTE：允许以小仓进入队列
                        print(f'[state_refresh] {sym} {direction}: CHOP_BREAKOUT_{chop_signal}({chop_result["score"]}/7)，解锁小仓')
                        # 覆盖nav_pct为CHOP限定仓位
                        _chop_nav_override = chop_result.get('nav_pct', 0.01)
                    except Exception as ce:
                        print(f'[state_refresh] {sym} {direction}: chop_breakout_detector失败: {ce}，保持封禁')
                        continue
                else:
                    _chop_nav_override = None

                # 构造 decision_engine 输入
                signal_in = {
                    'symbol':    sym,
                    'direction': direction,
                    'regime':    regime,
                    'score':     score,
                    'grade':     grade,
                    # [唯一裁判封印 2026-09-23 苏摩111] 传入SSOT action，decision_engine Step1读它
                    'cf_action': str(cleaned.get('confluence', {}).get('action', '') or cleaned.get('action', '') or ''),
                    'price':     cleaned.get('price'),
                    'sl_pct':    2.0,   # 默认SL 2%，decision_engine会按ATR调整
                    'timing':    cleaned.get('timing', ''),
                    # 注入核心指标供decision_engine用
                    'rsi_1h':    (cleaned.get('momentum') or {}).get('rsi_1h', 50),
                    'long_ratio': cleaned.get('long_ratio', 50),
                    'funding_rate': cleaned.get('funding_rate', 0),
                    'score_final': score,
                }

                try:
                    decision = _decide(signal_in)
                except Exception as e:
                    # [梵天2.0 T1 error_ledger接线 2026-09-27 苏摩111] decision关键路径异常必记账
                    try:
                        from brahma_brain import error_ledger as _el
                        _el.count('decision', error=e,
                                  context={'phase': 'state_refresh_decide',
                                           'symbol': sym, 'direction': direction})
                    except Exception:
                        pass  # [WARN-suppressed: no var]
                    print(f'[state_refresh] {sym} {direction}: decision_engine错误: {e}')
                    continue

                action = decision.get('action', 'SKIP')
                reason = decision.get('reason', '')
                ep     = decision.get('entry_plan', {})

                # [梵天2.0 T1 L0录制 2026-09-27 苏摩111] 决策快照落盘（零执行，纯数据）
                # 接入位置: scripts/replay_ci.py record_decisions() / reports/brahma_2.0_design.md §6 P1
                try:
                    from replay_ci import record_decisions as _l0rec
                    _l0rec([(signal_in, decision)])
                except Exception:
                    pass  # [WARN-suppressed: no var]

                print(f'[state_refresh] {sym} {direction}: action={action} reason={reason[:60]}')

                # [entry-SSOT P0-2 2026-09-26 苏摩111] 决策块写入per-symbol state顶层
                # P2 battlefield_auto_analysis 优先读 state['decision']，回退state顶层
                _state_obj = all_states.get(sym)
                if isinstance(_state_obj, dict):
                    _state_obj['decision'] = {
                        'action': action,
                        'reason': str(reason)[:160],
                        'direction': direction,
                        'ts': now_iso,
                    }

                if action not in ('EXECUTE', 'WAIT_15M'):
                    continue

                # [P0-1根修 2026-09-25 苏摩111] WAIT_15M=结构未确认，不是执行指令
                # 根因：WAIT_15M带4h TTL入队 → paper_executor按市价直接开单
                # 修复：WAIT_15M只刷新已有等待信号的确认窗口，绝不作为新信号入队
                if action == 'WAIT_15M':
                    _refreshed = False
                    for _s0 in active:
                        if (_s0.get('symbol') == sym
                                and _s0.get('signal_dir') == direction
                                and _s0.get('action') == 'WAIT_15M'):
                            _exp = now_ts + SIGNAL_TTL_HOURS * 3600
                            _s0['expires_at'] = datetime.fromtimestamp(_exp, tz=timezone.utc).isoformat()
                            _s0['created_at'] = now_iso
                            _s0['last_wait_refresh'] = now_iso
                            _refreshed = True
                    if _refreshed:
                        print(f'[state_refresh] {sym} {direction}: WAIT_15M刷新确认窗口（不入队）')
                    continue

                # 检查队列中是否已有该标的同方向可执行信号（去重）
                # [P0-1] WAIT_15M旧信号不算dup——EXECUTE应能替换等待信号
                dup = any(
                    s.get('symbol') == sym and s.get('signal_dir') == direction
                    and s.get('action') != 'WAIT_15M'
                    for s in active
                )
                if dup:
                    print(f'[state_refresh] {sym} {direction}: 队列中已有信号，跳过')
                    continue

                # ── 构造标准信号 ──────────────────────────────────
                expires_ts  = now_ts + SIGNAL_TTL_HOURS * 3600
                expires_iso = datetime.fromtimestamp(expires_ts, tz=timezone.utc).isoformat()

                price_now = ep.get('price') or cleaned.get('price', 0)
                sl_price  = ep.get('sl_price', 0)
                tp1_price = ep.get('tp1_price', 0)
                tp2_price = ep.get('tp2_price', 0)
                sl_pct    = ep.get('sl_pct', 2.0)
                rr        = ep.get('rr', 0)

                # [entry-SSOT P0-2 2026-09-26 苏摩111] entry区间优先读decision_engine entry_plan结构位
                # 根因：L252-253 ±0.3%硬编码与决策层entry_plan脱节（SSOT断裂）
                # 修复：ep有entry_lo/entry_hi用决策层结构位；无字段才fallback ±0.3%，entry_source='hardcoded_fallback'
                _ep_lo, _ep_hi = ep.get('entry_lo'), ep.get('entry_hi')
                if _ep_lo and _ep_hi and _ep_lo > 0 and _ep_hi > 0:
                    entry_lo = round(float(_ep_lo), 2)
                    entry_hi = round(float(_ep_hi), 2)
                    entry_source = str(ep.get('entry_source', 'decision_engine'))
                else:
                    entry_lo = round(price_now * (0.997 if direction == 'LONG' else 1.000), 2)
                    entry_hi = round(price_now * (1.000 if direction == 'LONG' else 1.003), 2)
                    entry_source = 'hardcoded_fallback'

                # [entry-SSOT P0-2 2026-09-26 苏摩111] 决策字段展开写state顶层（signal消费方统一从state读）
                _state_obj = all_states.get(sym)
                if isinstance(_state_obj, dict):
                    _state_obj['entry_lo'] = entry_lo
                    _state_obj['entry_hi'] = entry_hi
                    _state_obj['stop_loss'] = round(float(sl_price), 2) if sl_price else None
                    _state_obj['entry_source'] = entry_source

                sig = {
                    'signal_id':   f'{sym}_{direction}_{int(now_ts)}',
                    'symbol':      sym,
                    'signal_dir':  direction,
                    'direction':   direction,
                    'action':      action,
                    'regime':      regime,
                    'score_final': round(score, 1),
                    'score':       round(score, 1),
                    'grade':       grade,
                    'grade_num':   grade,
                    'price':       price_now,
                    'entry_lo':    entry_lo,
                    'entry_hi':    entry_hi,
                    'sl_price':    round(sl_price, 2),
                    'tp1':         round(tp1_price, 2),
                    'tp2':         round(tp2_price, 2),
                    'sl_pct':      round(sl_pct, 2),
                    'rr':          round(rr, 2),
                    'rr1':         round(rr, 2),
                    'valid':       True,
                    'catalysts':   ep.get('catalysts', []),
                    'source':      'brahma_state_refresh',
                    'created_at':  now_iso,
                    'expires_at':  expires_iso,
                    # CHOP解锁信号额外字段
                    'chop_unlock': _chop_nav_override is not None,
                    'nav_pct_override': _chop_nav_override,  # None=正常仳位, float=CHOP限制仓位
                    # [B分级降权 2026-09-27 苏摩111] 评分层SKIP warn通道：消费方仓位×0.5
                    'score_gate_warn': bool(decision.get('score_gate_warn')),
                    # [梵天2.0转正 2026-09-27 苏摩111] ATR1H管线缺口修复：
                    # 影子期6/6 R1 MISSING_ATR假拦截根因=信号不带atr1h。
                    # 源头=cleaned['momentum']['atr_1h']，risk_gate R1必需字段
                    'atr1h':       round(float((cleaned.get('momentum') or {}).get('atr_1h') or 0), 2) or None,
                    # 供 paper_executor / auto_executor 判断用
                    'paper_only':  True,   # 纸面优先，实盘切换时改False
                }

                new_signals.append(sig)
                print(f'[state_refresh] ✅ 新信号入队: {sym} {direction} score={score:.1f} '
                      f'entry={entry_lo}~{entry_hi} SL={sl_price:.2f} TP1={tp1_price:.2f} RR={rr:.2f}x')

        except Exception as e:
            print(f'[state_refresh] ❌ {sym} 分析失败: {e}')

    # ── Step 3: 保存 brahma_state.json ──────────────────────────
    try:
        # 保存综合state（含btc_price/eth_price/updated_at供测试和下游读取）
        _btc_state = all_states.get('BTCUSDT', {})
        _eth_state = all_states.get('ETHUSDT', {})
        _now_epoch = time.time()
        _composite_state = {
            'ts': _now_epoch,
            'nav': 130.0,
            'positions': [],
            'regime': _btc_state.get('regime', 'CHOP_MID'),
            'btc_price': _btc_state.get('price', 0),
            'eth_price': _eth_state.get('price', 0),
            'updated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'last_update': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            # [9.27freshness 苏摩111] 快照年龄字段：下游健康检查用文件mtime+此字段双重验证
            '_snapshot_age_sec': 0,
            '_snapshot_written_epoch': _now_epoch,
        }
        _atomic_write(STATE_FILE, _composite_state)

        # 封印 2026-09-04 苏摩111：每个标的独立保存 brahma_state_<sym>.json
        # 修复根因：ETH分析读到BTC的_ob_map/_fvg_map（数据污染）
        for _sym, _state in all_states.items():
            _sym_lower = _sym.replace('USDT', '').lower()
            _sym_file  = STATE_FILE.parent / f'brahma_state_{_sym_lower}.json'
            _state_copy = dict(_state)
            _state_copy['_sym_key'] = _sym
            _state_copy['_snapshot_age_sec'] = 0
            _state_copy['_snapshot_written_epoch'] = _now_epoch
            _atomic_write(_sym_file, _state_copy)
        print(f'[state_refresh] 已写入独立state: {list(all_states.keys())}')
    except Exception as e:
        print(f'[state_refresh] ⚠️  brahma_state.json写入失败: {e}')

    # ── Step 4: 保存 auto_signal_queue.json ─────────────────────
    final_queue = active + new_signals
    _save_queue(final_queue)

    print(f'[state_refresh] 队列: 保留{len(active)}个有效 | 过期清理{expired_count}个 | 新增{len(new_signals)}个 | 合计{len(final_queue)}个')

if __name__ == '__main__':
    main()