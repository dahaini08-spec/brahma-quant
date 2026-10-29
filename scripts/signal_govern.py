"""
梵天信号治理架构 v3.0
[封印 2026-10-29 苏摩111]

决策漏斗：所有推送经过4层过滤，只发最优结论
  L0 宏观硬门控  → BEAR_TREND禁多 / CHOP+score<140禁方向
  L1 方仓先验锚  → sim≥0.7硬锁 / 0.5~0.7软警告 / <0.5忽略
  L2 信号质量门  → RR<0.8 / SL<1.5×ATR / 清算位间距<0.3%
  L3 方向共识窗  → 30min反向 / 15min同源去重 / 作废精准静默

接入位置：scripts/push_hub.py  push_jarvis() 调用前
零侵入：现有 M1/M4/M7/vip_watcher 无需改动
"""

import json
import time
import sys
import re
from pathlib import Path

# ── 路径 ──
_BASE = Path(__file__).resolve().parent.parent
_STATE_FILE = _BASE / 'data' / 'signal_govern_state.json'
_LOG_FILE   = _BASE / 'logs' / 'signal_govern.log'

# ── 信号源优先级 ──
_SRC_PRIORITY = {'M7': 3, 'vip_watcher': 3, 'M1': 2, 'M4': 1, 'M8': 0, 'robot': 0}

# ── 冷却配置（秒）──
_COOLDOWN_REVERSE = 1800   # 30min 反向冲突窗口
_COOLDOWN_DEDUP   = 900    # 15min 同源同向去重
_COOLDOWN_CANCEL  = 1800   # 30min 作废静默（同方向）


# ══════════════════════════════════════════════════════════
#  状态读写（原子写，防gateway重启竞态）
# ══════════════════════════════════════════════════════════

def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(state), encoding='utf-8')
        tmp.rename(_STATE_FILE)
    except Exception as e:
        _log(f'[state] 写入失败: {e}')


# ══════════════════════════════════════════════════════════
#  数据读取辅助
# ══════════════════════════════════════════════════════════

def _get_regime_score(sym: str) -> tuple[str, float]:
    """从 brahma_state 读取体制和评分，缓存60s"""
    try:
        f = _BASE / f'data/brahma_state_{sym.lower()}.json'
        d = json.loads(f.read_text(encoding='utf-8'))
        return d.get('regime', 'CHOP_MID'), float(d.get('score_final', d.get('score', 0)))
    except Exception:
        return 'CHOP_MID', 100.0


def _get_fangcang(sym: str) -> tuple[str | None, float]:
    """
    从 brahma_state 读取方仓方向和 sim
    字段名: fc_signal(SHORT/LONG/NEUTRAL) + fc_sim
    由 brahma_state_refresh._inject_fangcang_to_state() 每1H写入
    [保活 2026-10-29 苏摩111] 数据>90min自动触发重注入，防停机窗口失效
    """
    try:
        import time as _t
        f = _BASE / f'data/brahma_state_{sym.lower()}.json'
        d = json.loads(f.read_text(encoding='utf-8'))
        raw = d.get('fc_signal') or ''
        sim = float(d.get('fc_sim') or 0)
        # 保活检查：fc_sim为None或state超过90min → 触发重注入
        _state_age = _t.time() - float(d.get('price_ts', 0) or 0)
        if (sim == 0 or raw == '') and _state_age > 5400:  # 90min
            _log(f'[fangcang] {sym} 数据{_state_age/60:.0f}min旧，触发重注入')
            try:
                import sys as _sys
                _sys.path.insert(0, str(_BASE / 'scripts'))
                import brahma_state_refresh as _bsr
                _bsr._inject_fangcang_to_state()
                d = json.loads(f.read_text(encoding='utf-8'))
                raw = d.get('fc_signal') or ''
                sim = float(d.get('fc_sim') or 0)
                _log(f'[fangcang] {sym} 重注入完成: {raw} sim={sim:.3f}')
            except Exception as _re:
                _log(f'[fangcang] {sym} 重注入失败: {_re}')
        direction = raw.lower() if raw and raw.upper() in ('SHORT','LONG') else None
        return direction, sim
    except Exception:
        return None, 0.0


def _get_atr1h(sym: str) -> float:
    """从 liq_heatmap 或 brahma_state 读取 ATR1H"""
    try:
        f = _BASE / f'data/brahma_state_{sym.lower()}.json'
        d = json.loads(f.read_text(encoding='utf-8'))
        return float(d.get('atr_1h', d.get('atr', 0)) or 0)
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════
#  方向解析（文本推断，临时方案；第2步改为显式传参）
# ══════════════════════════════════════════════════════════

def _parse_direction(msg: str) -> tuple[str | None, str | None]:
    """
    从消息文本推断 (sym, direction)
    direction: 'long' | 'short' | 'neutral' | 'cancel'
    """
    if not msg:
        return None, 'neutral'

    # 标的
    sym = None
    if 'BTC' in msg:
        sym = 'BTC'
    elif 'ETH' in msg:
        sym = 'ETH'

    # 作废
    if '作废' in msg or 'cancel' in msg.lower():
        return sym, 'cancel'

    # neutral：暂无多单+暂无空单 / WAIT / 纯分析
    if '暂无多单' in msg and '暂无空单' in msg:
        return sym, 'neutral'
    if any(x in msg for x in ['WAIT', '等待结构', '🕐', '梵天分析', 'score=']):
        return sym, 'neutral'

    # 空单信号
    SHORT_PATTERNS = ['空单入场区', 'VIP空单', '止损墙', '🔴 空单', '猎杀门槛', '做空', '入场区：$']
    for p in SHORT_PATTERNS:
        if p in msg:
            # 但排除"暂无空单"
            if '暂无空单' not in msg:
                return sym, 'short'

    # 多单信号
    LONG_PATTERNS = ['多单入场区', '接货', '支撑池', '🟢 多单', '做多', '大户LSR单日+']
    for p in LONG_PATTERNS:
        if p in msg:
            if '暂无多单' not in msg:
                return sym, 'long'

    return sym, 'neutral'


# ══════════════════════════════════════════════════════════
#  日志
# ══════════════════════════════════════════════════════════

def _log(msg: str) -> None:
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime('%H:%M:%S', time.gmtime())
        line = f'[{ts}] {msg}\n'
        with open(_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
        print(line.rstrip(), file=sys.stderr)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
#  核心仲裁函数（4层漏斗）
# ══════════════════════════════════════════════════════════

def gate(
    msg: str,
    *,
    direction: str | None = None,   # 显式传入（优先）
    sym: str | None = None,
    source: str = 'unknown',
    rr: float | None = None,
    sl_dist: float | None = None,   # 止损距离（价格点数）
    liq_gap_pct: float | None = None,
    entry_lo: float | None = None,  # [P0 2026-10-29 苏摩111] 入场区下沿（用于时效检查）
    entry_hi: float | None = None,  # 入场区上沿
) -> tuple[bool, str]:
    """
    返回 (允许推送, 原因)
    所有推送到 push_jarvis() 前调用此函数
    """
    now = time.time()

    # ── 解析方向和标的 ──
    if direction is None or sym is None:
        _sym, _dir = _parse_direction(msg)
        if sym is None:
            sym = _sym
        if direction is None:
            direction = _dir or 'neutral'

    # neutral/cancel/无标的 → 直接通过（WAIT/作废通知不受仲裁）
    if direction in ('neutral', 'cancel') or sym is None:
        if direction == 'cancel' and sym:
            # 记录作废事件（同方向静默）
            state = _load_state()
            state[f'{sym}_cancel_dir']  = _extract_cancel_dir(msg)
            state[f'{sym}_cancel_ts']   = now
            _save_state(state)
        return True, 'neutral/cancel 直通'

    # ── 读取上下文 ──
    regime, score   = _get_regime_score(sym)
    fc_dir, fc_sim  = _get_fangcang(sym)
    atr1h           = _get_atr1h(sym)
    state           = _load_state()

    # ══ L0: 宏观硬门控 ══════════════════════════════════════
    if regime == 'BEAR_TREND' and direction == 'long':
        reason = f'L0: BEAR_TREND 禁多（{sym}）'
        _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
        return False, reason

    if regime == 'BULL_TREND' and direction == 'short':
        reason = f'L0: BULL_TREND 谨慎空（{sym}）需额外确认'
        _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
        return False, reason

    if regime == 'CHOP_MID' and score < 140:
        reason = f'L0: CHOP_MID+score={score:.0f}<140 禁方向性推送'
        _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
        return False, reason

    # ══ L1: 方仓先验锚 ══════════════════════════════════════
    if fc_dir and fc_dir != direction:
        if fc_sim >= 0.7:
            reason = f'L1: 方仓强锁 {fc_dir}(sim={fc_sim:.2f}) 逆向 {direction}'
            _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
            return False, reason
        elif fc_sim >= 0.5:
            _log(f'[ARB-WARN]  {source} {sym} {direction} 方仓软锁 {fc_dir}(sim={fc_sim:.2f}) 降权但允许通过')
            # 软锁：记录警告但不拦截

    # ══ L1.5: 入场区时效检查 ══════════════════════════════════
    # [P0 2026-10-29 苏摩111] 推送时有效但到达时入场区已过期
    # 根因：leading_signal在$78,500触发，苏摩收到时价格已$80,154
    if entry_lo is not None and entry_hi is not None and direction in ('long', 'short'):
        # [修复 2026-10-29 苏摩111] 优先读brahma_state缓存价格，不依赖网络
        # 原方案：urllib实时拉取，失败→except→pass→漏过时效检查
        _cur_px = 0.0
        try:
            _sf = _BASE / f'data/brahma_state_{sym.lower()}.json'
            _cur_px = float(json.loads(_sf.read_text(encoding='utf-8')).get('price', 0) or 0)
        except Exception:
            pass
        if _cur_px <= 0:  # state读取失败时才用网络
            try:
                import urllib.request as _ur
                _cur_px = float(json.loads(_ur.urlopen(
                    f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT',
                    timeout=3).read())['price'])
            except Exception:
                _cur_px = 0.0
        if _cur_px > 0:
            if direction == 'long' and _cur_px > entry_hi * 1.005:
                reason = f'L1.5: 入场区已过期 多单上沿${entry_hi:,.0f}<当前价${_cur_px:,.0f}(+{(_cur_px-entry_hi)/entry_hi*100:.1f}%)'
                _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
                return False, reason
            if direction == 'short' and _cur_px < entry_lo * 0.995:
                reason = f'L1.5: 入场区已过期 空单下沿${entry_lo:,.0f}>当前价${_cur_px:,.0f}(-{(entry_lo-_cur_px)/entry_lo*100:.1f}%)'
                _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
                return False, reason

    # ══ L2: 信号质量门 ══════════════════════════════════════
    if rr is not None and rr < 0.8:
        reason = f'L2: RR={rr:.2f}<0.8 质量不达标'
        _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
        return False, reason

    if sl_dist is not None and atr1h > 0 and sl_dist < atr1h * 1.5:
        reason = f'L2: SL距离={sl_dist:.1f}<1.5×ATR1H={atr1h*1.5:.1f} 止损太近'
        _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
        return False, reason

    if liq_gap_pct is not None and liq_gap_pct < 0.3:
        reason = f'L2: 清算位间距={liq_gap_pct:.2f}%<0.3% 目标无效'
        _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
        return False, reason

    # ══ L3: 方向共识窗口 ════════════════════════════════════

    # 作废精准静默（只拦截同方向，不跨向）
    cancel_dir = state.get(f'{sym}_cancel_dir')
    cancel_ts  = float(state.get(f'{sym}_cancel_ts', 0))
    if cancel_ts and (now - cancel_ts) < _COOLDOWN_CANCEL:
        if cancel_dir == direction or cancel_dir is None:
            reason = f'L3: 作废后{int((now-cancel_ts)/60)}min静默（{cancel_dir or "全部"}方向）'
            _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
            return False, reason

    # 30min内反向冲突 + 优先级仲裁
    last_dir = state.get(f'{sym}_last_dir')
    last_ts  = float(state.get(f'{sym}_last_ts', 0))
    last_src = state.get(f'{sym}_last_src', '')

    if last_dir and last_dir != direction and (now - last_ts) < _COOLDOWN_REVERSE:
        last_pri = _SRC_PRIORITY.get(last_src, 0)
        this_pri = _SRC_PRIORITY.get(source,  0)
        if this_pri <= last_pri:
            reason = (f'L3: {int((now-last_ts)/60)}min前已推 {last_dir}'
                      f'({last_src} P{last_pri})，当前 {source} P{this_pri} 无法覆盖')
            _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
            return False, reason
        else:
            _log(f'[ARB-COVER] {source}(P{this_pri}) 覆盖 {last_src}(P{last_pri}) {last_dir}→{direction}')

    # 15min内同源同向去重
    dedup_key = f'{sym}_dedup_{source}_{direction}'
    dedup_ts  = float(state.get(dedup_key, 0))
    if (now - dedup_ts) < _COOLDOWN_DEDUP:
        reason = f'L3: 同源同向{int((now-dedup_ts)/60)}min内去重 ({source} {direction})'
        _log(f'[ARB-BLOCK] {source} {sym} {direction} → {reason}')
        return False, reason

    # ══ 通过 → 更新共识 ══════════════════════════════════════
    state[f'{sym}_last_dir'] = direction
    state[f'{sym}_last_ts']  = now
    state[f'{sym}_last_src'] = source
    state[dedup_key]          = now
    _save_state(state)

    _log(f'[ARB-PASS]  {source} {sym} {direction} RR={rr} → 通过')
    return True, 'OK'


def _extract_cancel_dir(msg: str) -> str | None:
    """从作废通知提取被作废的方向"""
    if '多单' in msg and '空单' not in msg:
        return 'long'
    if '空单' in msg and '多单' not in msg:
        return 'short'
    return None  # 全部作废


# ══════════════════════════════════════════════════════════
#  自测
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print('[signal_govern] 自测模式')
    tests = [
        ('🔴 BTC止损墙 $81,104 触及\n空单入场区：$81,104~$82,004', 'M7', None, None, None),
        ('🟢 梵天副驾·大户接货警报 [ETH]\n多单信号：入场$2,312', 'M4', None, None, None),
        ('⚡ 梵天神经感知 BTC WAIT', 'M8', None, None, None),
    ]
    for msg, src, rr, sl, lgp in tests:
        ok, reason = gate(msg, source=src, rr=rr, sl_dist=sl, liq_gap_pct=lgp)
        print(f'  [{src}] {"✅" if ok else "🚫"} {reason[:60]}')
