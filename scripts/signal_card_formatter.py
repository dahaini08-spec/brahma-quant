import os
#!/usr/bin/env python3
"""
signal_card_formatter.py — Brahma 信号卡片格式化器
设计院 · 2026-06-26

职责:
  1. 读取 live_signal_log.jsonl 中的有效信号
  2. 筛选 score >= 138 且 valid=True 的信号
  3. 格式化为 VIP 卡片格式
  4. 输出卡片内容，若无有效信号则输出 HEARTBEAT_OK

调用方式:
  python3 scripts/signal_card_formatter.py
"""

import json
from pathlib import Path
from datetime import datetime, timezone

_DIR = Path(__file__).parent.parent
LOG_PATH = _DIR / "data" / "live_signal_log.jsonl"

# 信号门槛
MIN_SCORE = 138
MIN_GRADE = 70  # grade 数值门槛 (对应 🟠极强/🔴神级)


def parse_grade(grade_val):
    """解析 grade 字段，返回数值
    🔴神级=100, 🟠极强=85, 🟡强=70, 其他=0
    """
    if isinstance(grade_val, (int, float)):
        return grade_val
    if isinstance(grade_val, str):
        if "神级" in grade_val or "🔴" in grade_val:
            return 100
        if "极强" in grade_val or "🟠" in grade_val:
            return 85
        if "强" in grade_val or "🟡" in grade_val:
            return 70
    return 0


def load_valid_signals():
    """加载有效信号 (valid=True, grade>=70, score>=138, 未过期)"""
    if not LOG_PATH.exists():
        return []
    
    lines = LOG_PATH.read_text().strip().split("\n")
    signals = []
    now = datetime.now(timezone.utc)
    
    for l in lines:
        if not l.strip():
            continue
        try:
            s = json.loads(l)
            if not s.get("valid"):
                continue
            
            score = s.get("score", 0)
            grade_raw = s.get("grade", 0)
            grade = parse_grade(grade_raw)
            
            # 检查过期
            expires_at = s.get("expires_at", "")
            if expires_at:
                try:
                    exp_ts = datetime.fromisoformat(
                        expires_at.replace("Z", "+00:00")
                    )
                    if now > exp_ts:
                        continue  # 已过期
                except:
                    pass
            
            if score >= MIN_SCORE and grade >= MIN_GRADE:
                signals.append(s)
        except:
            continue
    
    return signals


def format_vip_card(s):
    """格式化为 VIP 策略卡片"""
    sym = s.get("symbol", "").replace("USDT", "").upper()
    direction = s.get("direction", "LONG")
    score = s.get("score", 0)
    grade_raw = s.get("grade", "N/A")
    regime = s.get("regime_cn", s.get("regime", ""))
    
    entry_lo = float(s.get("entry_lo", 0) or 0)
    entry_hi = float(s.get("entry_hi", 0) or 0)
    sl = float(s.get("stop_loss", 0) or 0)
    tp1 = float(s.get("tp1", 0) or 0)
    tp2 = float(s.get("tp2", 0) or 0)
    
    # 价格格式化
    def p(v):
        if v > 1000:
            return f"${v:,.0f}"
        if v > 10:
            return f"${v:,.2f}"
        return f"${v:.4f}"
    
    # 方向标签
    if direction == "SHORT":
        dir_label = "🔴 空单"
        entry_desc = f"等 {p(entry_lo)} ~ {p(entry_hi)} 反弹入场"
    else:
        dir_label = "🟢 多单"
        entry_desc = f"等 {p(entry_lo)} ~ {p(entry_hi)} 接筹"
    
    # RR 计算
    if entry_lo > 0 and sl > 0 and tp1 > 0:
        entry_mid = (entry_lo + entry_hi) / 2 if entry_hi > 0 else entry_lo
        if direction == "SHORT":
            risk = abs(sl - entry_mid)
            reward = abs(entry_mid - tp1)
        else:
            risk = abs(entry_mid - sl)
            reward = abs(tp1 - entry_mid)
        rr = round(reward / risk, 1) if risk > 0 else 0
    else:
        rr = 0
    
    ts = s.get("ts_iso", "")[:16] if s.get("ts_iso") else s.get("ts", "")
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()[:16]
    
    signal_id = s.get("signal_id", "-")
    
    card = f"""🏯 梵天信号 · {sym}/USDT {dir_label}
━━━━━━━━━━━━━━━━━━━
🆔 {signal_id}
评分: {score:.0f}  等级: {grade_raw}  体制: {regime}

{entry_desc}
止损:   {p(sl)}
T1:     {p(tp1)}  R:R={rr}x
T2:     {p(tp2)}
━━━━━━━━━━━━━━━━━━━
信号时间: {ts} UTC
⚠️ 仅供参考，注意风控"""
    
    return card


def run():
    signals = load_valid_signals()

    if not signals:
        # 无信号 → 静默退出，systemEvent模式下不打印HEARTBEAT_OK减少噪音
        return

    # 按 score 降序排序，取最高分信号
    signals.sort(key=lambda x: x.get("score", 0), reverse=True)
    best = signals[0]

    card = format_vip_card(best)
    print(card)
    print()
    print(f"[SignalCardFormatter] 找到 {len(signals)} 个有效信号，输出最高分: {best.get('symbol')} score={best.get('score')}")

    # 自推送：systemEvent模式下直接推送，无需AI中间人
    import subprocess as _sp
    _sp.run(
        ['openclaw', 'message', 'send',
         '--channel', 'jarvis',
         '--target', os.environ.get('JARVIS_TARGET', 'YOUR_USER_ID:thread:YOUR_THREAD_ID'),
         '--message', card],
        capture_output=True, timeout=15
    )
    print(f'[signal-watcher] 推送完成: {best.get("symbol")} score={best.get("score")}')


if __name__ == "__main__":
    run()
