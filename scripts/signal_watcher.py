#!/usr/bin/env python3
"""
# ── 全局内存优化（工程师建议 P1）──
import gc as _gc_mod
import psutil as _psutil_mod
_gc_mod.enable()
_gc_mod.set_threshold(700, 10, 10)

def _check_and_gc():
    _gc_mod.collect()
    if _psutil_mod.virtual_memory().percent > 75:
        _gc_mod.collect(2)
# ─────────────────────────────────────
signal_watcher.py — 梵天信号实时感知 + Jarvis推送 v1.0
设计院 · 星枢引擎 · 2026-06-09

职责：
  1. 检测 live_signal_log 新写入的有效信号（<10分钟）
  2. 立刻推送 Jarvis 信号卡片（Layer 0 · <30秒延迟）
  3. 对已有信号监控价格贴近度，触发预警推送
     - gap < 0.5% → ⚡ 即将触发
     - gap < 0.1% 或价格进入区间 → 🚨 已触发/进入入场区

调用方式：
  python3 scripts/signal_watcher.py          # 正常运行
  python3 scripts/signal_watcher.py --test   # 测试模式（不推送）

被 brahma-commander cron 在每次扫描后调用
"""

import json
import os
import sys
import time
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

# ── brahma_bus 总线接入（设计院 2026-06-29）──
try:
    from brahma_brain.brahma_bus import bus as _brahma_bus
except Exception:
    _brahma_bus = None

def _bus_price(symbol: str) -> float:
    """统一价格获取：优先走 brahma_bus 缓存，降级走直接HTTP"""
    if _brahma_bus:
        return _brahma_bus.price(symbol)
    import requests
    r = requests.get(f'https://fapi.binance.com/fapi/v1/ticker/price',
                     params={'symbol': symbol}, timeout=5)
    return float(r.json()['price'])



# ── 路径（必须在 push_hub import 之前设置）────────────────────────
_DIR      = Path(__file__).parent.parent
sys.path.insert(0, str(_DIR))
sys.path.insert(0, str(_DIR / 'brahma_brain'))

# from push_hub import _jarvis  # [设计院2026-06-18] 禁用内部推送，改由AI announce
LOG_PATH  = _DIR / "data" / "live_signal_log.jsonl"
STATE_PATH = _DIR / "data" / "signal_watcher_state.json"
FAPI      = "https://fapi.binance.com"

# ── 推送目标（从 system_config 读取）─────────────────────────────
try:
    sys.path.insert(0, str(_DIR))
    from scripts.system_config import JARVIS_USER_ID
    _USER_ID = JARVIS_USER_ID
except Exception:
    _USER_ID = os.environ.get("JARVIS_USER_ID", "YOUR_USER_ID")  # fallback, see system_config

# 当前对话线程（主线程）
_THREAD_ID = "019ed32f-c46d-72ab-9d5e-92e47b4bdcc5"  # fallback, see system_config
_JARVIS_TO = f"{_USER_ID}:t:{_THREAD_ID}"

TEST_MODE = "--test" in sys.argv


def _now_ts() -> float:
    return time.time()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()[:19] + "Z"


def _fetch_price(symbol: str) -> float:
    try:
        url = f"{FAPI}/fapi/v1/ticker/price?symbol={symbol}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return float(json.loads(r.read())["price"])
    except Exception:
        return 0.0



def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"notified": {}, "warned": {}}


def _save_state(state: dict):
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as _e_ignored:
        print(f'[WARN][signal_watcher] {type(_e_ignored).__name__}: {_e_ignored}')


def _load_valid_signals() -> list:
    """加载所有有效信号（valid=True, grade≥70, score≥140）
    [v24.2] grade门槛 50→70：B级TO率=73%，仅A/S级(≥70)有意义
    """
    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text().strip().split("\n")
    signals = []
    for l in lines:
        if not l.strip():
            continue
        try:
            s = json.loads(l)
            if not s.get("valid"):
                continue
            grade = s.get("grade", 0)
            score = s.get("score", 0)
            if isinstance(grade, str):  # grade存了emoji字符串，fallback到structure_grade
                grade = s.get("structure_grade", 0) or 0
                s["grade"] = grade  # 修正为数字，供后续使用
            if grade >= 70 and score >= 140:  # [v24.2] 50→70
                signals.append(s)
        except Exception:
            continue
    return signals


def _format_signal_card(s: dict, price: float, gap_pct: float) -> str:
    """格式化信号推送卡片"""
    sym      = s.get("symbol", "").replace("USDT", "")
    direct   = "▼ 做空" if s.get("direction") == "SHORT" else "▲ 做多"
    score    = s.get("score", 0)
    grade    = s.get("grade", 0)
    entry_lo = float(s.get("entry_lo", 0) or 0)
    entry_hi = float(s.get("entry_hi", 0) or 0)
    sl       = float(s.get("stop_loss", 0) or 0)
    tp1      = float(s.get("tp1", 0) or 0)
    tp2      = float(s.get("tp2", 0) or 0)
    ts       = s.get("ts", "")[:16]
    regime   = s.get("regime", "")
    signal_id = s.get("signal_id", "-")

    # RR 计算
    if entry_lo > 0 and sl > 0 and tp1 > 0:
        entry_mid = (entry_lo + entry_hi) / 2 if entry_hi > 0 else entry_lo
        if s.get("direction") == "SHORT":
            risk   = abs(sl - entry_mid)
            reward = abs(entry_mid - tp1)
        else:
            risk   = abs(entry_mid - sl)
            reward = abs(tp1 - entry_mid)
        rr = round(reward / risk, 1) if risk > 0 else 0
    else:
        rr = 0

    # 价格格式
    def p(v):
        if v > 1000: return f"${v:,.0f}"
        if v > 10:   return f"${v:,.2f}"
        return f"${v:.4f}"

    # 贴近度标签
    if gap_pct < 0:
        gap_label = f"🔴 价格在入场区内 ({abs(gap_pct):.2f}%)"
    elif gap_pct < 0.1:
        gap_label = f"🚨 极度贴近 ({gap_pct:.2f}%)"
    elif gap_pct < 0.5:
        gap_label = f"⚡ 贴近 ({gap_pct:.2f}%)"
    else:
        gap_label = f"📏 距入场区 {gap_pct:.2f}%"

    text = f"""🏯 梵天信号 · {sym}/USDT {direct}
━━━━━━━━━━━━━━━━━━━
🆔 {signal_id}
现价: {p(price)}  体制: {regime}
评分: {score:.0f}  grade: {grade}  {gap_label}

入场区: {p(entry_lo)} ~ {p(entry_hi)}
止损:   {p(sl)}
T1:     {p(tp1)}  R:R={rr}x
T2:     {p(tp2)}
━━━━━━━━━━━━━━━━━━━
信号时间: {ts} UTC
⚠️ 仅供参考，注意风控"""
    return text


# ── CHOP体制预检（P1优化 · 设计院2026-06-21）─────────────────────
def _is_chop_regime() -> bool:
    """读取brahma_state.json，判断当前是否全部标的处于CHOP体制
    全部CHOP → True（跳过信号扫描，节省token）
    有任一非CHOP → False（正常扫描）
    """
    try:
        state_file = _DIR / 'data' / 'brahma_state.json'
        if not state_file.exists():
            return False
        bstate = json.loads(state_file.read_text())
        # 支持两种结构：顶层 regime 字段 或 per_symbol 结构
        if isinstance(bstate, dict):
            # 结构1: {"BTCUSDT": {"regime": "..."}, ...}
            regimes = []
            for sym_key, sym_val in bstate.items():
                if isinstance(sym_val, dict):
                    r = sym_val.get('regime', '')
                    if r: regimes.append(r)
            if regimes:
                non_chop = [r for r in regimes if not r.startswith('CHOP')]
                return len(non_chop) == 0  # 全CHOP才返回True
        return False
    except Exception:
        return False


def run():
    # ── CHOP预检门控 ─────────────────────────────────────────────
    if _is_chop_regime():
        print('[SignalWatcher] 全标的CHOP体制，跳过信号扫描（0 token节省）')
        return

    state   = _load_state()
    signals = _load_valid_signals()
    now     = _now_ts()

    if not signals:
        print("[SignalWatcher] 无有效信号")
        return

    # 清理24H前的已通知记录
    state["notified"] = {k: v for k, v in state["notified"].items()
                         if now - v < 86400}
    state["warned"]   = {k: v for k, v in state["warned"].items()
                         if now - v < 86400}  # 预警24H内不重复（之前1H导致每小时轰炸）

    for s in signals:
        sym    = s.get("symbol", "")
        ts_str = s.get("ts", "")
        # [Fix-TypeError 2026-06-14] ts可能是float时间戳，转成str再切片
        if isinstance(ts_str, (int, float)):
            from datetime import timezone
            ts_str = datetime.fromtimestamp(float(ts_str), tz=timezone.utc).isoformat()
            s["ts"] = ts_str
        sig_id = f"{sym}_{str(ts_str)[:16]}"

        # 解析信号时间
        try:
            sig_ts = datetime.fromisoformat(
                ts_str.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            continue

        age_min = (now - sig_ts) / 60

        # ── [Fix-TTL 2026-06-11] expires_at优先判断 ─────────────
        expires_at = s.get('expires_at', '')
        if expires_at:
            try:
                exp_ts = datetime.fromisoformat(
                    expires_at.replace('Z', '+00:00')
                ).timestamp()
                if now > exp_ts:
                    continue  # 已过TTL，僵尸信号，跳过
            except Exception as _e_ignored:
                print(f'[WARN][signal_watcher] {type(_e_ignored).__name__}: {_e_ignored}')
        elif age_min > 2880:  # 无expires_at时fallback：48H过期
            continue

        # ── Layer 0：新信号检测（<10分钟，且未通知过）───────────
        if age_min < 10 and sig_id not in state["notified"]:
            price = _fetch_price(sym)
            if price <= 0:
                continue

            entry_lo = float(s.get("entry_lo", 0) or 0)
            entry_hi = float(s.get("entry_hi", entry_lo) or entry_lo)

            if entry_lo > 0:
                if s.get("direction") == "SHORT":
                    gap_pct = (entry_lo - price) / price * 100
                else:
                    gap_pct = (price - entry_lo) / price * 100
            else:
                gap_pct = 999

            card = _format_signal_card(s, price, gap_pct)
            # 唯一推送出口：push_hub._jarvis（dedup_ttl=86400，同信号24H内不重复）
            try:
                from push_hub import _jarvis as _pj_sw; _pj_sw(f"🔔 新信号\n{card}", dedup_ttl=86400)
            except Exception: pass
            state["notified"][sig_id] = now
            print(f"[SignalWatcher] ✅ 新信号推送: {sym} score={s.get('score')} grade={s.get('grade')}")
            continue

        # ── Layer 1：价格贴近预警（只对24H内信号）────────────────
        if age_min > 1440:  # 无expires_at时的最终兜底（已在上方expires_at处理过）
            continue

        price = _fetch_price(sym)
        if price <= 0:
            continue

        entry_lo = float(s.get("entry_lo", 0) or 0)
        entry_hi = float(s.get("entry_hi", entry_lo) or entry_lo)

        if entry_lo <= 0:
            continue

        if s.get("direction") == "SHORT":
            gap_pct = (entry_lo - price) / price * 100
        else:
            gap_pct = (price - entry_lo) / price * 100

        warn_key_05  = f"{sig_id}_warn_05"
        warn_key_01  = f"{sig_id}_warn_01"
        warn_key_in  = f"{sig_id}_warn_in"

        # 进入入场区（gap < 0）→ 自动执行（苏摩宪法第六条）
        if gap_pct < 0 and warn_key_in not in state["warned"]:
            card = _format_signal_card(s, price, gap_pct)
            state["warned"][warn_key_in] = now
            # 尝试自动执行
            try:
                import sys as _sys; _sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
                from auto_execute_gate import auto_execute
                exec_result = auto_execute(s, dry_run=False)
                if exec_result['executed']:
                    _push_msg = f"✅ 梵天自动执行\n{card}\n订单: {exec_result['order']}"
                else:
                    _push_msg = f"🚨 价格进入入场区（未执行: {exec_result['reason']}）\n{card}"
            except Exception as _ex:
                import traceback as _tb2
                _err_detail = _tb2.format_exc()[-300:]
                _push_msg = f"🚨 价格已进入入场区！\n{card}\n(自动执行异常: {_ex})"
                # 写入执行异常日志，防止被吞噬
                try:
                    _elog = __import__('pathlib').Path(__file__).parent.parent / 'data' / 'auto_execute_log.jsonl'
                    with open(_elog, 'a') as _ef:
                        import datetime as _dt
                        _ef.write(__import__('json').dumps({
                            'ts': _dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                            'event': 'ERROR', 'symbol': s.get('symbol'), 'reason': f'signal_watcher异常: {_ex}',
                            'traceback': _err_detail
                        }, ensure_ascii=False) + '\n')
                except Exception: pass
            try:
                from push_hub import _jarvis as _pj; _pj(_push_msg, dedup_ttl=14400)  # 4H去重
            except Exception: pass
            print(f"[SignalWatcher] 🚨 入场区触发: {sym} price={price:.2f}")

        # gap < 0.1%（极度贴近）
        elif 0 <= gap_pct < 0.1 and warn_key_01 not in state["warned"]:
            card = _format_signal_card(s, price, gap_pct)
            try:
                from push_hub import _jarvis as _pj_sw; _pj_sw(f"⚡ 价格极度贴近入场区！\n{card}", dedup_ttl=3600)
            except Exception: pass
            state["warned"][warn_key_01] = now
            print(f"[SignalWatcher] ⚡ 极度贴近: {sym} gap={gap_pct:.3f}%")

        # gap < 0.5%（即将触发）
        elif 0.1 <= gap_pct < 0.5 and warn_key_05 not in state["warned"]:
            card = _format_signal_card(s, price, gap_pct)
            try:
                from push_hub import _jarvis as _pj_sw; _pj_sw(f"📡 信号即将触发\n{card}", dedup_ttl=3600)
            except Exception: pass
            state["warned"][warn_key_05] = now
            print(f"[SignalWatcher] 📡 即将触发: {sym} gap={gap_pct:.3f}%")

    _save_state(state)
    print(f"[SignalWatcher] 完成  有效信号={len(signals)}  通知记录={len(state['notified'])}")

    # [Zone Watcher v1.0 2026-06-10] Layer 2: 高分低级信号待触达监控
    try:
        _zw = Path(__file__).parent / 'zone_watcher.py'
        if _zw.exists():
            subprocess.Popen(
                ['python3', str(_zw)] + (['--test'] if TEST_MODE else []),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
    except Exception as _ze:
        print(f'[SignalWatcher] ZoneWatcher调用失败: {_ze}')


if __name__ == "__main__":
    run()