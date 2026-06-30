#!/usr/bin/env python3
"""
vip_format.py — VIP策略固定格式生成器
设计院 · 2026-06-20 封印

七条规则：
1. 开头永远 🌿 姓赵不宣
2. 不出现"梵天量化"等系统名称
3. 空单在前主力，多单副方向轻仓
4. TP至少三档（空单TP1/TP2/TP3）
5. 多单网格接筹：先写猎杀位，再写接筹区
6. 杠杆与仓位必须同行标注
7. 末行固定：⚠️ 主方向做空，多单博反弹严守止损

调用方式：
    from vip_format import build_vip_card
    card = build_vip_card(
        symbol      = 'ETH',
        short_entry = ('$1,774', '$1,783'),
        short_sl    = '$1,801',
        short_tps   = ('$1,742', '$1,710', '$1,690'),
        short_lev   = 3, short_pos = 2,
        hunt_level  = '$1,720',
        long_entry  = ('$1,725', '$1,731'),
        long_sl     = '$1,709',
        long_tps    = ('$1,762', '$1,790'),
        long_lev    = 3, long_pos  = 1.5,
    )
    print(card)
"""


def build_vip_card(
    symbol: str,
    # 空单参数
    short_entry: tuple,      # (低, 高)
    short_sl: str,
    short_tps: tuple,        # 至少3档
    short_lev: int,
    short_pos: float,
    # 多单参数
    hunt_level: str,         # 猎杀位
    long_entry: tuple,       # (低, 高)
    long_sl: str,
    long_tps: tuple,         # 至少2档
    long_lev: int,
    long_pos: float,
    # 可选
    label: str = '今日布局',
    warning: str = '主方向做空，多单博反弹严守止损',
) -> str:
    """生成标准VIP格式策略卡片"""

    # ── 规则校验 ──────────────────────────────────────
    assert len(short_tps) >= 3, '规则4: 空单TP至少三档'
    assert len(long_tps)  >= 2, '多单TP至少两档'
    assert hunt_level,          '规则5: 必须提供猎杀位'

    # ── 格式化 ────────────────────────────────────────
    sym = symbol.replace('USDT', '').replace('usdt', '').upper()

    short_tp_str = ' / '.join(short_tps)
    long_tp_str  = ' / '.join(long_tps)

    card = (
        f'🌿 姓赵不宣  |  {sym} {label}\n'
        f'\n'
        f'🔴  空单\n'
        f'等 {short_entry[0]} ~ {short_entry[1]} 反弹入场\n'
        f'止损 {short_sl}   目标 {short_tp_str}\n'
        f'杠杆{short_lev}x   仓{short_pos}%\n'
        f'\n'
        f'🟢  多单（轻仓）\n'
        f'等 {hunt_level} 猎杀位被扫后 {long_entry[0]} ~ {long_entry[1]} 接\n'
        f'止损 {long_sl}   目标 {long_tp_str}\n'
        f'杠杆{long_lev}x   仓{long_pos}%\n'
        f'\n'
        f'⚠️ {warning}'
    )
    return card


# ── 自检 ──────────────────────────────────────────────
if __name__ == '__main__':
    # ETH示例
    eth = build_vip_card(
        symbol      = 'ETH',
        short_entry = ('$1,774', '$1,783'),
        short_sl    = '$1,801',
        short_tps   = ('$1,742', '$1,710', '$1,690'),
        short_lev   = 3, short_pos = 2.0,
        hunt_level  = '$1,720',
        long_entry  = ('$1,725', '$1,731'),
        long_sl     = '$1,709',
        long_tps    = ('$1,762', '$1,790'),
        long_lev    = 3, long_pos  = 1.5,
    )
    print(eth)
    print()

    # BTC示例
    btc = build_vip_card(
        symbol      = 'BTC',
        short_entry = ('$65,336', '$65,663'),
        short_sl    = '$66,200',
        short_tps   = ('$63,900', '$62,500', '$61,000'),
        short_lev   = 3, short_pos = 2.0,
        hunt_level  = '$63,500',
        long_entry  = ('$63,820', '$63,945'),
        long_sl     = '$62,730',
        long_tps    = ('$65,200', '$66,500'),
        long_lev    = 3, long_pos  = 1.5,
    )
    print(btc)
