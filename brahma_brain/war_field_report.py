#!/usr/bin/env python3
"""
war_field_report.py — 梵天战场简报生成器 L0-L4
[P0-B 2026-08-31 苏摩111封印]

每次分析强制输出L0→L1→L2→L3→L4格式
核心在前，71项详细数据在后
不超过20行主要内容，给苏摩3秒决策
"""

from datetime import datetime, timezone


def build_war_report(
    symbol: str,
    price: float,
    regime: str,
    direction: str,
    score: float,
    # L0宏观
    macro_status: str = '正常',
    macro_events: str = '未来72H无危险级事件',
    dxy_note: str = 'DXY中性',
    vix_note: str = 'VIX正常',
    # L1体制
    can_long: bool = False,
    can_short: bool = True,
    # L2战场
    lsr: float = None,
    oi_chg: float = None,
    taker: float = None,
    gex_dir: str = None,
    hunt_up: float = None,    # 上方猎杀目标
    hunt_up_note: str = '',
    hunt_down: float = None,  # 下方猎杀目标
    hunt_down_note: str = '',
    path_up_pct: int = 42,    # 先触上方概率%
    path_down_pct: int = 58,
    # L3埋伏
    entry_zone_lo: float = None,
    entry_zone_hi: float = None,
    sl: float = None,
    tp1: float = None,
    tp2: float = None,
    rr: float = None,
    war_pos_pct: float = None,  # 战场仓位建议
    trigger_condition: str = '进入区间 + 15M CHoCH',
    # L4持仓
    holding_symbol: str = None,
    holding_dir: str = None,
    holding_entry: float = None,
    holding_price: float = None,
    holding_tp2: float = None,
    holding_tp3: float = None,
    holding_sl: float = None,
) -> str:
    """生成L0-L4战场简报"""

    now = datetime.now(timezone.utc).strftime('%H:%M UTC')
    sym_short = symbol.replace('USDT', '')
    lines = []

    lines.append(f'╔{"═"*52}╗')
    lines.append(f'║  🏛️ 梵天战场 | {sym_short} | ${price:,.1f} | {now}')
    lines.append(f'╠{"═"*52}╣')

    # L0
    macro_ok = macro_status == '正常'
    lines.append(f'║')
    lines.append(f'║ 【L0 宏观守门】{"✅" if macro_ok else "🚨"}')
    lines.append(f'║   {macro_events}')
    lines.append(f'║   {dxy_note} | {vix_note}')

    # L1
    long_str  = '✅可做' if can_long else '⛔禁做'
    short_str = '✅可做' if can_short else '⛔禁做'
    lines.append(f'║')
    lines.append(f'║ 【L1 体制裁决】')
    lines.append(f'║   {regime} | Score={score:.0f}（仓位参考）')
    lines.append(f'║   做多:{long_str} | 做空:{short_str}')

    # L2
    lines.append(f'║')
    lines.append(f'║ 【L2 猎杀地图】← 核心预判')
    if hunt_down:
        lines.append(f'║   下方猎杀: ${hunt_down:,.0f} {hunt_down_note}')
    if hunt_up:
        lines.append(f'║   上方风险: ${hunt_up:,.0f} {hunt_up_note}')
    lines.append(f'║   路径: {path_up_pct}%先触上方 | {path_down_pct}%先触下方')

    # 三角信号
    triangle = []
    if lsr is not None:
        if direction == 'SHORT' and lsr > 65:
            triangle.append(f'LSR={lsr:.1f}%拥挤✅')
        elif direction == 'LONG' and lsr < 45:
            triangle.append(f'LSR={lsr:.1f}%空挤✅')
        else:
            triangle.append(f'LSR={lsr:.1f}%中性')
    if oi_chg is not None:
        arrow = '↓✅' if (direction=='SHORT' and oi_chg<-0.5) else '↑✅' if (direction=='LONG' and oi_chg>0.5) else '→'
        triangle.append(f'OI={oi_chg:+.2f}%{arrow}')
    if taker is not None:
        t_note = '卖✅' if (direction=='SHORT' and taker<0.9) else '买✅' if (direction=='LONG' and taker>1.1) else '均'
        triangle.append(f'Taker={taker:.2f}{t_note}')
    if triangle:
        lines.append(f'║   三角: {" | ".join(triangle)}')
    if gex_dir:
        gex_note = '净空→做空顺风' if gex_dir=='NET_SHORT' else '净多→做多顺风' if gex_dir=='NET_LONG' else '中性'
        lines.append(f'║   GEX: {gex_note}')

    # L3
    lines.append(f'║')
    lines.append(f'║ 【L3 埋伏坐标】')
    if entry_zone_lo and entry_zone_hi:
        in_zone = entry_zone_lo <= price <= entry_zone_hi
        zone_note = '⚡已入区' if in_zone else '等待触及'
        lines.append(f'║   入场区: ${entry_zone_lo:,.0f}~${entry_zone_hi:,.0f} {zone_note}')
    if trigger_condition:
        lines.append(f'║   触发条件: {trigger_condition}')
    if sl and tp1:
        rr_str = f'RR={rr:.1f}' if rr else ''
        lines.append(f'║   SL=${sl:,.0f} | TP1=${tp1:,.0f} {rr_str}')
        if tp2:
            lines.append(f'║   TP2=${tp2:,.0f}')
    if war_pos_pct is not None:
        lines.append(f'║   仓位: {war_pos_pct}%NAV（战场仓位）')

    # L4
    lines.append(f'║')
    lines.append(f'║ 【L4 持仓状态】')
    if holding_symbol and holding_entry and holding_price:
        h_pnl = (holding_entry - holding_price)/holding_entry*100 if holding_dir=='SHORT' else (holding_price-holding_entry)/holding_entry*100
        lines.append(f'║   {holding_symbol.replace("USDT","")} {holding_dir} @${holding_entry:,.2f} | 浮盈{h_pnl:+.2f}%')
        if holding_tp2:
            diff2 = holding_price - holding_tp2 if holding_dir=='SHORT' else holding_tp2 - holding_price
            tp2_status = 'DONE✅' if diff2 <= 0 else f'差${diff2:.0f}'
            lines.append(f'║   TP2=${holding_tp2:,.0f} {tp2_status}')
        if holding_tp3:
            diff3 = holding_price - holding_tp3 if holding_dir=='SHORT' else holding_tp3 - holding_price
            lines.append(f'║   TP3=${holding_tp3:,.0f} 差${diff3:.0f}')
        if holding_sl:
            lines.append(f'║   SL=${holding_sl:,.0f} 保本线')
    else:
        lines.append(f'║   无持仓 | 等待触发条件')

    lines.append(f'║')
    lines.append(f'╚{"═"*52}╝')

    return '\n'.join(lines)


if __name__ == '__main__':
    # 测试输出
    report = build_war_report(
        symbol='ETHUSDT', price=2427.0,
        regime='CHOP_MID', direction='SHORT', score=6.1,
        macro_events='未来72H无危险级事件',
        dxy_note='DXY=99.68偏强', vix_note='VIX正常',
        can_long=False, can_short=True,
        lsr=72.3, oi_chg=-3.22, taker=0.863, gex_dir='NET_SHORT',
        hunt_down=2294, hunt_down_note='$1,463M清算大墙',
        hunt_up=2500, hunt_up_note='GEX PIN上沿',
        path_up_pct=18, path_down_pct=82,
        entry_zone_lo=2460, entry_zone_hi=2475,
        sl=2502, tp1=2294, rr=3.2, war_pos_pct=0.5,
        holding_symbol='ETHUSDT', holding_dir='SHORT',
        holding_entry=2501.11, holding_price=2427.0,
        holding_tp2=2407, holding_tp3=2294, holding_sl=2502,
    )
    print(report)
