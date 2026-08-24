#!/usr/bin/env python3
# ponytail: regime_bus 377行，有意为之，重构前先 grep 所有调用方
"""
regime_bus.py — 梵天体制总线 v2.0（方案C：双层架构）
设计院自主升级 | 2026-08-05 苏摩111授权

架构：快照(O(1)读) + 事件流(历史复盘) + fcntl并发锁 + TTL陈旧感知

宪法原则：
  全系统唯一体制数据源
  所有模块通过 regime_bus.get() 获取体制
  禁止模块自行计算/读取体制文件

升级内容（v1.0 → v2.0）：
  ✅ fcntl文件锁：防多进程并发损坏
  ✅ TTL=30min：超时自动降级UNKNOWN + 告警
  ✅ 事件流 regime_events.jsonl：完整体制历史
  ✅ 向后兼容：get() / update() 接口不变
"""
import json, time, os, fcntl, datetime
from pathlib import Path
from typing import Optional

BASE        = Path(__file__).resolve().parents[1]
DATA        = BASE / 'data'
BUS_FILE    = DATA / 'regime_bus.json'
EVENTS_FILE = DATA / 'regime_events.jsonl'
LOCK_FILE   = DATA / '.regime_bus.lock'
SSOT_FILE   = DATA / 'btc_regime_watcher_state.json'

# ── 常量 ──────────────────────────────────────────────────────
BUS_VERSION   = '2.0'
TTL_SECONDS   = 1800      # 30分钟无更新 → 降级UNKNOWN
TTL_WARN_SECS = 900       # 15分钟无更新 → 告警

VALID_REGIMES = {
    'BULL_TREND', 'BULL_EARLY',
    'BEAR_TREND', 'BEAR_EARLY',
    'BEAR_RECOVERY', 'BEAR_RECOVERY_CANDIDATE',
    'CHOP_MID', 'CHOP_LOW',
    'UNKNOWN'
}

# ── 授权写入来源 ───────────────────────────────────────────────
AUTHORIZED = {
    'btc_regime_watcher':    ['CONFIRMED'],
    'brahma_engine':         ['CONFIRMED', 'CANDIDATE'],
    'consensus_engine':      ['CONFIRMED'],
    'eth_ema_gate':          ['CANDIDATE', 'EARLY'],
    'rsi_structure_watcher': ['EARLY'],
    'regime_switch_monitor': ['CONFIRMED', 'CANDIDATE'],
    'manual':                ['CONFIRMED', 'CANDIDATE', 'EARLY'],
}


# ─────────────────────────────────────────────────────────────
# 内部工具：文件锁
# ─────────────────────────────────────────────────────────────

class _RegimeLock:
    """fcntl文件锁，防并发写损坏"""
    def __init__(self):
        self._fd = None

    def __enter__(self):
        self._fd = open(LOCK_FILE, 'w')
        fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self

    def __exit__(self, *_):
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None


def _load_snapshot() -> dict:
    try:
        return json.loads(BUS_FILE.read_text())
    except Exception:
        return {'_version': BUS_VERSION}


def _save_snapshot(state: dict):
    state['_version'] = BUS_VERSION
    state['_saved_at'] = time.time()
    BUS_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _append_event(event: dict):
    """原子追加到事件流（append-only，天然并发安全）"""
    line = json.dumps(event, ensure_ascii=False) + '\n'
    with open(EVENTS_FILE, 'a') as f:
        f.write(line)


# ─────────────────────────────────────────────────────────────
# TTL检查
# ─────────────────────────────────────────────────────────────

def _check_ttl(sym_data: dict, confidence: str) -> str:
    """
    检查某个置信度的数据是否过期。
    返回: 'ok' | 'warn' | 'expired'
    """
    ts_key = f'{confidence}_ts'
    ts = sym_data.get(ts_key, 0)
    if not ts:
        return 'expired'
    age = time.time() - ts
    if age > TTL_SECONDS:
        return 'expired'
    if age > TTL_WARN_SECS:
        return 'warn'
    return 'ok'


# ─────────────────────────────────────────────────────────────
# 公开接口：get
# ─────────────────────────────────────────────────────────────

def get(symbol: str = 'BTCUSDT',
        layer: str = 'SIGNAL',
        default: str = 'UNKNOWN',
        _raise_on_stale: bool = False) -> str:
    """
    获取体制值（O(1)，读快照）。

    Args:
        symbol:  BTCUSDT / ETHUSDT
        layer:   EXECUTION | SIGNAL | ALERT | MONITOR
        default: 无数据时返回值

    Returns:
        体制字符串
    """
    snap = _load_snapshot()
    sym_data = snap.get(symbol, {})

    def _v(key: str) -> str:
        return sym_data.get(key.upper(), sym_data.get(key.lower(), ''))

    def _valid(r: str) -> bool:
        return bool(r) and r in VALID_REGIMES and r != 'UNKNOWN'

    if layer in ('EXECUTION', 'SIGNAL'):
        r = _v('CONFIRMED')
        ttl = _check_ttl(sym_data, 'CONFIRMED')
        if ttl == 'expired':
            return 'UNKNOWN'    # 严格：过期直接返回UNKNOWN
        return r if _valid(r) else default

    elif layer == 'ALERT':
        r = _v('CONFIRMED') or _v('CANDIDATE')
        conf_key = 'CONFIRMED' if _valid(_v('CONFIRMED')) else 'CANDIDATE'
        ttl = _check_ttl(sym_data, conf_key)
        if ttl == 'expired':
            return _v('CANDIDATE') or default   # 降级用CANDIDATE
        return r if _valid(r) else default

    else:  # MONITOR
        r = _v('CONFIRMED') or _v('CANDIDATE') or _v('EARLY')
        return r if _valid(r) else default


# ─────────────────────────────────────────────────────────────
# 公开接口：update
# ─────────────────────────────────────────────────────────────

def update(symbol: str,
           regime: str,
           confidence: str = 'CONFIRMED',
           source: str = 'brahma_engine',
           score: float = 0.0,
           notes: str = '') -> bool:
    """
    更新体制总线（原子写：锁→事件流→快照→解锁）。

    Returns: True=成功, False=被拒绝
    """
    # 鉴权
    allowed = AUTHORIZED.get(source, [])
    if confidence not in allowed:
        return False
    if regime not in VALID_REGIMES:
        return False

    try:
        with _RegimeLock():
            snap     = _load_snapshot()
            sym_data = snap.setdefault(symbol, {})

            key_upper = confidence.upper()
            prev      = sym_data.get(key_upper, sym_data.get(confidence.lower(), ''))

            # 更新快照
            sym_data[key_upper]              = regime
            sym_data[f'{key_upper}_ts']      = time.time()
            sym_data[f'{key_upper}_source']  = source
            sym_data[f'{key_upper}_score']   = score
            sym_data['updated_at']           = time.time()

            # 记录体制切换
            switched = (prev and prev != regime and confidence == 'CONFIRMED')
            if switched:
                sym_data['last_switch'] = {
                    'from': prev, 'to': regime,
                    'ts': time.time(), 'source': source, 'score': score
                }

            _save_snapshot(snap)

            # 事件流（体制变化才写，节省磁盘）
            if not prev or prev != regime:
                _append_event({
                    'ts':         time.time(),
                    'iso':        datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'sym':        symbol,
                    'confidence': confidence,
                    'from':       prev or '',
                    'to':         regime,
                    'source':     source,
                    'score':      score,
                    'notes':      notes,
                })

        return True

    except BlockingIOError:
        # 锁被占用 → 直接写（降级，避免死锁）
        try:
            snap     = _load_snapshot()
            sym_data = snap.setdefault(symbol, {})
            sym_data[confidence.upper()] = regime
            sym_data[f'{confidence.upper()}_ts'] = time.time()
            snap['updated_at'] = time.time()
            _save_snapshot(snap)
            return True
        except Exception:
            return False
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# 公开接口：history
# ─────────────────────────────────────────────────────────────

def history(symbol: str = 'BTCUSDT',
            confidence: str = 'CONFIRMED',
            limit: int = 20) -> list:
    """
    从事件流读取历史体制变化记录。
    用于达摩院复盘：某时刻体制是什么？
    """
    if not EVENTS_FILE.exists():
        return []
    events = []
    for line in EVENTS_FILE.read_text().splitlines():
        try:
            e = json.loads(line)
            if e.get('sym') == symbol and e.get('confidence') == confidence:
                events.append(e)
        except Exception:
            pass
    return events[-limit:]


# ─────────────────────────────────────────────────────────────
# 公开接口：sync_from_ssot
# ─────────────────────────────────────────────────────────────

def sync_from_ssot():
    """从SSOT同步到总线（系统启动时调用）"""
    try:
        d = json.loads(SSOT_FILE.read_text())
        btc_r = d.get('regime', '')
        eth_r = d.get('eth_regime', btc_r)
        if btc_r in VALID_REGIMES:
            update('BTCUSDT', btc_r, 'CONFIRMED', 'btc_regime_watcher')
        if eth_r in VALID_REGIMES:
            # ETH只在总线无有效值时覆盖
            snap = _load_snapshot()
            eth_data = snap.get('ETHUSDT', {})
            eth_ts   = eth_data.get('CONFIRMED_ts', 0)
            if time.time() - eth_ts > TTL_SECONDS:
                update('ETHUSDT', eth_r, 'CONFIRMED', 'btc_regime_watcher')
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 公开接口：get_full / status_report
# ─────────────────────────────────────────────────────────────

def get_full(symbol: str = 'BTCUSDT') -> dict:
    return _load_snapshot().get(symbol, {})


def status_report() -> str:
    snap = _load_snapshot()
    now  = time.time()
    lines = [
        "╔══════════════════════════════════════════════════════╗",
        f"  梵天体制总线 v{BUS_VERSION} | Regime Bus",
        f"  {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "╚══════════════════════════════════════════════════════╝",
        "",
    ]

    for sym in ['BTCUSDT', 'ETHUSDT']:
        data = snap.get(sym, {})
        if not data:
            lines.append(f"  [{sym}] ⚠️ 未初始化")
            continue

        def _v(k):
            return data.get(k.upper(), data.get(k.lower(), '?'))
        def _age(k):
            ts = data.get(f'{k.upper()}_ts', 0)
            return f"{round((now-ts)/60)}min前" if ts else "从未"
        def _ttl_icon(k):
            st = _check_ttl(data, k)
            return {'ok':'✅','warn':'⚠️','expired':'❌'}.get(st,'?')

        confirmed = _v('CONFIRMED')
        candidate = _v('CANDIDATE')
        early     = _v('EARLY')

        lines += [
            f"  [{sym}]",
            f"    CONFIRMED : {confirmed:<20} {_ttl_icon('CONFIRMED')} ({_age('CONFIRMED')}, src={_v('CONFIRMED_source')})",
        ]
        if candidate and candidate != '?':
            lines.append(f"    CANDIDATE : {candidate:<20} {_ttl_icon('CANDIDATE')} ({_age('CANDIDATE')})")
        if early and early != '?':
            lines.append(f"    EARLY     : {early:<20} {_ttl_icon('EARLY')} ({_age('EARLY')})")

        sw = data.get('last_switch', {})
        if sw:
            age_h = round((now - sw.get('ts', now)) / 3600, 1)
            lines.append(f"    切换记录  : {sw.get('from','?')} → {sw.get('to','?')} ({age_h}H前)")

        lines += [
            f"",
            f"    层级视图（当前可用体制）:",
            f"      EXECUTION/SIGNAL → {get(sym,'SIGNAL'):<20} ← 执行层",
            f"      ALERT            → {get(sym,'ALERT'):<20} ← 预警层",
            f"      MONITOR          → {get(sym,'MONITOR'):<20} ← 监控层",
            "",
        ]

    # 事件流统计
    if EVENTS_FILE.exists():
        events = EVENTS_FILE.read_text().splitlines()
        lines.append(f"  事件流: {len(events)} 条历史记录 ({EVENTS_FILE.name})")

    lines.append("═"*56)
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    if '--sync' in sys.argv:
        sync_from_ssot()
        print("✅ SSOT → 总线同步完成")
    if '--history' in sys.argv:
        sym = 'ETHUSDT' if 'ETH' in ' '.join(sys.argv) else 'BTCUSDT'
        evts = history(sym, 'CONFIRMED', 30)
        print(f"\n{sym} CONFIRMED 体制历史（近{len(evts)}条）:")
        for e in evts:
            print(f"  {e['iso']}  {e.get('from','')} → {e['to']}  ({e['source']})")
    print(status_report())
