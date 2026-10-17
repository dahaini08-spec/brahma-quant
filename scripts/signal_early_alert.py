#!/usr/bin/env python3
"""
signal_early_alert.py — 先行信号预警层
[设计院封印 2026-10-17 苏摩111]

数据依据：
  RSI55~75: 8-9月405次触发 UP=80.7% RR=5.36 期望值+2.26%（无体制依赖）
  LSR散户>72%: 主力猎杀就绪信号
  OI急变>2%: 主力异动15min内
  GEX ZeroFlip穿越: 负Gamma区波动放大

定位：
  - 独立于Step11体制门控
  - 只出预警，不出入场单（苏摩自行判断）
  - 每15min执行，cron接入
  - P0级先行感知 = 体制确认的2~4h前

接入位置：brahma_crontab.txt 每15min
"""

import json, sys, os
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
sys.path.insert(0, str(BASE / 'scripts'))

JARVIS_USER_ID = os.environ.get('JARVIS_USER_ID', '73295708')
THREAD_ID      = os.environ.get('JARVIS_THREAD_ID', '01a12e17-726c-78da-9857-4edab995522b')

# ── 阈值（均有数据依据）
RSI_SIGNAL_LO  = 55   # 先行信号区下界
RSI_SIGNAL_HI  = 75   # 先行信号区上界
LSR_CROWD_THRESH = 72  # 散户拥挤阈值
OI_SURGE_PCT   = 2.0  # OI急变%
GEX_ZF_MARGIN  = 0.003  # ZeroFlip穿越判定（±0.3%）

# ── 去重：避免同一信号重复推送
STATE_FILE = DATA / 'early_alert_state.json'
def load_state():
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text())
        except: pass
    return {}

def save_state(st):
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False))
    tmp.replace(STATE_FILE)

def push_alert(msg: str):
    """推送到Jarvis"""
    try:
        from pathlib import Path as _P
        import subprocess
        target = f'{JARVIS_USER_ID}:thread:{THREAD_ID}'
        cmd = ['openclaw', 'message', 'send', '--channel', 'jarvis',
               '--to', target, '--message', msg]
        subprocess.run(cmd, timeout=15, capture_output=True)
        print(f'[early_alert] 推送: {msg[:60]}...')
    except Exception as e:
        print(f'[early_alert] 推送失败: {e}')


def check_symbol(sym: str, state: dict, now_ts: float) -> list:
    """检查单个标的的先行信号，返回告警列表"""
    alerts = []
    try:
        d = json.loads((DATA / f'brahma_state_{sym.lower()}.json').read_text())
    except:
        return alerts

    price    = float(d.get('price', 0) or 0)
    rsi_1h   = float(d.get('rsi_1h', 50) or 50)
    rsi_4h   = float(d.get('rsi_4h', 50) or 50)
    lsr_r    = float(d.get('lsr_retail', 0) or 0)
    lsr_b    = float(d.get('lsr_big', 0) or 0)
    oi_dir   = str(d.get('oi_direction', '') or '')
    oi_seq   = d.get('oi_sequence', [])
    zero_flip= float(d.get('zero_flip', 0) or 0)
    cvd_1h   = float(d.get('cvd_1h', 0) or 0)
    regime   = str(d.get('regime', 'CHOP_MID') or 'CHOP_MID')
    liq_s    = float(d.get('liq_short', 0) or 0)
    liq_l    = float(d.get('liq_long', 0) or 0)
    fc_long  = float(d.get('fc_long_pct', 0) or 0)
    fc_n     = int(d.get('fc_n_similar', 0) or 0)

    if price <= 0:
        return alerts

    sym_state = state.get(sym, {})

    # ── 信号1：RSI先行区触发
    rsi_sig_key = f'{sym}_rsi_signal'
    rsi_was_in  = sym_state.get('rsi_in_signal', False)
    rsi_now_in  = RSI_SIGNAL_LO <= rsi_1h <= RSI_SIGNAL_HI
    if rsi_now_in and not rsi_was_in:
        # 新进入信号区
        fc_note = f'方仓n={fc_n}多={fc_long:.0%}' if fc_n >= 10 else '方仓数据不足'
        alert = (
            f'📡 【{sym}先行信号】RSI_1H={rsi_1h:.1f}进入偏强区[55~75]\n'
            f'历史依据：8-9月同类405次 → UP=80.7% RR=5.36\n'
            f'当前：@${price:,.0f}  体制={regime}  {fc_note}\n'
            f'4H RSI={rsi_4h:.1f}  CVD={cvd_1h:+.3f}  LSR散户={lsr_r:.1f}%\n'
            f'⚠️ 先行信号≠入场单，等OI确认+K线收阳后决策'
        )
        alerts.append(('rsi_entry', alert))
        state.setdefault(sym, {})['rsi_in_signal'] = True
    elif not rsi_now_in:
        state.setdefault(sym, {})['rsi_in_signal'] = False

    # ── 信号2：LSR散户极度拥挤（猎杀就绪）
    lsr_key = f'{sym}_lsr_crowd'
    lsr_last = sym_state.get('lsr_retail_last', 0)
    if lsr_r >= LSR_CROWD_THRESH and lsr_last < LSR_CROWD_THRESH:
        alert = (
            f'🎯 【{sym}猎杀信号】LSR散户={lsr_r:.1f}%超过{LSR_CROWD_THRESH}%\n'
            f'解读：散户极度偏多 = 主力准备反向清算多头止损池\n'
            f'当前：@${price:,.0f}  大户={lsr_b:.1f}%  下方清算池=${liq_l:,.0f}\n'
            f'OI={oi_dir}  RSI1H={rsi_1h:.1f}\n'
            f'⚠️ 可能先跌破${liq_l:,.0f}扫多头，再反弹'
        )
        alerts.append(('lsr_crowd', alert))
    state.setdefault(sym, {})['lsr_retail_last'] = lsr_r

    # ── 信号3：OI急变>2%（主力异动）
    if len(oi_seq) >= 3:
        oi_chg_pct = (oi_seq[-1] - oi_seq[-3]) / max(abs(oi_seq[-3]), 1) * 100
        oi_last_chg = sym_state.get('oi_last_chg', 0)
        if abs(oi_chg_pct) >= OI_SURGE_PCT and abs(oi_last_chg) < OI_SURGE_PCT:
            direction = '做多增仓' if oi_chg_pct > 0 else '主力减仓/做空'
            alert = (
                f'⚡ 【{sym}主力异动】OI 15min急变{oi_chg_pct:+.1f}%（{direction}）\n'
                f'当前：@${price:,.0f}  OI={oi_dir}  RSI1H={rsi_1h:.1f}\n'
                f'CVD={cvd_1h:+.3f}  上方清算=${liq_s:,.0f}  下方=${liq_l:,.0f}\n'
                f'⚠️ 主力异动≠方向确认，结合RSI+CVD判断'
            )
            alerts.append(('oi_surge', alert))
        state.setdefault(sym, {})['oi_last_chg'] = oi_chg_pct

    # ── 信号4：GEX ZeroFlip穿越
    if zero_flip > 0 and price > 0:
        zf_dist_pct = (price - zero_flip) / zero_flip
        zf_last_side = sym_state.get('zf_side', 0)   # +1=在上方, -1=在下方
        zf_cur_side  = 1 if zf_dist_pct > 0 else -1
        if zf_cur_side != zf_last_side and zf_last_side != 0 and abs(zf_dist_pct) < GEX_ZF_MARGIN:
            cross_dir = '向上穿越ZF(负→正Gamma)' if zf_cur_side > 0 else '向下穿越ZF(正→负Gamma)'
            alert = (
                f'🌀 【{sym} GEX穿越】价格{cross_dir}\n'
                f'ZeroFlip=${zero_flip:,.0f}  现价@${price:,.0f}  偏离={zf_dist_pct:+.2%}\n'
                f'含义：{"正Gamma区→价格被做市商稳定" if zf_cur_side>0 else "负Gamma区→波动放大，方向加速"}\n'
                f'RSI1H={rsi_1h:.1f}  OI={oi_dir}'
            )
            alerts.append(('gex_cross', alert))
        state.setdefault(sym, {})['zf_side'] = zf_cur_side

    # ── 信号5：清算密集区±0.5%（即将触发）
    if liq_s > 0 and abs(price - liq_s) / liq_s < 0.005:
        liq_key = f'{sym}_liq_s_near'
        if not sym_state.get('liq_s_near', False):
            alert = (
                f'💣 【{sym}即将触发上方清算】距${liq_s:,.0f}仅{abs(price-liq_s)/price*100:.2f}%\n'
                f'现价@${price:,.0f}  触发后→逼空加速\n'
                f'RSI={rsi_1h:.1f}  OI={oi_dir}  CVD={cvd_1h:+.3f}'
            )
            alerts.append(('liq_near', alert))
            state.setdefault(sym, {})['liq_s_near'] = True
    else:
        state.setdefault(sym, {})['liq_s_near'] = False

    if liq_l > 0 and abs(price - liq_l) / liq_l < 0.005:
        if not sym_state.get('liq_l_near', False):
            alert = (
                f'💣 【{sym}即将触发下方清算】距${liq_l:,.0f}仅{abs(price-liq_l)/price*100:.2f}%\n'
                f'现价@${price:,.0f}  触发后→猎杀多头止损→可能反转\n'
                f'RSI={rsi_1h:.1f}  OI={oi_dir}  CVD={cvd_1h:+.3f}'
            )
            alerts.append(('liq_near', alert))
            state.setdefault(sym, {})['liq_l_near'] = True
    else:
        state.setdefault(sym, {})['liq_l_near'] = False

    return alerts


def main():
    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    print(f'[early_alert] {now.strftime("%H:%M UTC")} 检查先行信号...')

    state = load_state()
    all_alerts = []

    for sym in ['BTC', 'ETH']:
        try:
            alerts = check_symbol(sym, state, now_ts)
            all_alerts.extend(alerts)
        except Exception as e:
            print(f'[early_alert] {sym} 检查失败: {e}')

    if all_alerts:
        # 多条合并推送
        lines = [f'🏛️ 梵天先行信号 · {now.strftime("%m/%d %H:%M UTC")}']
        for sig_type, msg in all_alerts:
            lines.append('')
            lines.append(msg)
        push_alert('\n'.join(lines))
        print(f'[early_alert] 推送{len(all_alerts)}条信号')
    else:
        print('[early_alert] 无新先行信号')

    save_state(state)


if __name__ == '__main__':
    main()
