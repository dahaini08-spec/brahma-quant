#!/usr/bin/env python3
"""
ETH grade≥80 + CHoCH 解封监控
触发条件: effective_grade ≥ 80 OR CHoCH出现
进场区: $1,863.68 ~ $1,868.23
推送: Jarvis 019f933d 线程
"""
import sys, os, json, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

USER_ID   = "73295708"
THREAD_ID = "019fd9dd-4b0f-71db-87fb-1e192ccb2291"
ENTRY_LO  = 1863.68
ENTRY_HI  = 1868.23
SL        = 1826.98
TP1       = 1894.85
TP2       = 1928.84

def run_analysis():
    try:
        from brahma_brain.brahma_analysis_runner import run_analysis as _ra
        return _ra('ETHUSDT')
    except Exception as e:
        print(f"分析异常: {e}", file=sys.stderr)
        return {}

def main():
    r = run_analysis()
    if not r:
        print("HEARTBEAT_OK")
        return

    grade   = float(str(r.get('effective_grade', 0)).replace('?','0') or 0)
    price   = float(r.get('price', 0))
    smc     = r.get('smc', {})
    choch   = smc.get('structure', {}).get('choch', [])
    has_choch = bool(choch)
    choch_dir = choch[0].get('type','') if has_choch else ''

    grade_ok = grade >= 80
    choch_ok = has_choch and 'BULL' in choch_dir.upper()

    print(f"ETH grade={grade:.1f} price=${price:.2f} CHoCH={choch_dir or '无'}")

    if not grade_ok and not choch_ok:
        print(f"HEARTBEAT_OK（grade={grade:.1f}<80，无CHoCH）")
        return

    # 至少一条触发 → 推送
    triggers = []
    if grade_ok:   triggers.append(f"✅ grade={grade:.1f} ≥ 80 解封！")
    if choch_ok:   triggers.append(f"✅ BULL_CHoCH @ ${choch[0].get('level','?')} 出现！")

    msg = (
        f"🚨 ETH 解封信号触发！\n\n"
        + "\n".join(triggers) +
        f"\n\n现价: ${price:.2f}\n"
        f"进场区: ${ENTRY_LO} — ${ENTRY_HI}\n"
        f"止 损: ${SL}（-2.21%）\n"
        f"TP1:  ${TP1}（+1.42%）\n"
        f"TP2:  ${TP2}（+3.24%）\n"
        f"仓 位: 2% NAV 杠杆5x R:R≈2.0\n\n"
        f"⚠️ 执行前二次确认：\n"
        f"  1. 当前价是否在进场区 ${ENTRY_LO}~${ENTRY_HI}\n"
        f"  2. OBV是否转正\n"
        f"  3. 止损 ${SL} 不动摇"
    )

    subprocess.run([
        "openclaw", "message", "send",
        "--channel", "jarvis",
        "--to", f"{USER_ID}:thread:{THREAD_ID}",
        "--message", msg
    ], check=True)
    print("✅ 执行提醒已推送")

if __name__ == "__main__":
    main()
