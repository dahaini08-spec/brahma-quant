#!/usr/bin/env python3
# [2026-08-18 苏摩封印] OpenBLAS/OMP线程锁死 — 必须在所有import之前
import os as _os_blas
_os_blas.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
_os_blas.environ.setdefault('OMP_NUM_THREADS',      '1')
_os_blas.environ.setdefault('MKL_NUM_THREADS',      '1')
_os_blas.environ.setdefault('NUMEXPR_NUM_THREADS',  '1')
_os_blas.environ.setdefault('VECLIB_MAXIMUM_THREADS','1')
"""
梵天1号工程 · 35维全量矩阵分析引擎
固化版本 2026-07-17 苏摩111封印

架构：
  - 统一调用 brahma_engine.analyze() → 35维矩阵
  - 删除V3.0简化版（curl+人工计算路径）
  - 支持双币（BTC+ETH）并行分析
  - 输出格式：专业合约衍生品深度分析报告
"""
# ── 内存门控（设计院2026-08-11修复封印）───────────────────
# [修复] 顶层mem_gate改为懒加载，import时不sys.exit，只在__main__时门控
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'scripts') if '/scripts/' not in __file__ else _os.path.dirname(_os.path.abspath(__file__)))
_MEM_OK_FLAG = True
try:
    from brahma_mem_manager import _available_mb as _avail_mb
    _cur_avail = _avail_mb()
    if _cur_avail < 400:
        _MEM_OK_FLAG = False
        import logging as _mg_log
        _mg_log.getLogger('brahma_1hao_analysis').warning(f'[brahma_1hao_analysis] 内存危险{_cur_avail:.0f}MB<400MB，降级运行')
except ImportError:
    pass
# ── 进程内存上限硬封（设计院P3 2026-08-05）──────────────
try:
    import resource as _resource
    _RLIMIT_1500MB = 1500 * 1024 * 1024
    _resource.setrlimit(_resource.RLIMIT_AS, (_RLIMIT_1500MB, _RLIMIT_1500MB))
except Exception:
    pass  # 容器环境不支持时静默跳过
# ─────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────

import sys, os, time, re as _re
# ── [2026-07-28 设计院全局修复] safe_float: 处理emoji/字符串/None → float ────
def _safe_float(v, default=0.0):
    """安全float转换，处理emoji字符串如'🔵中等'/'🔴神级'等"""
    if v is None: return default
    if isinstance(v, (int, float)): return float(v)
    import re as _sfre
    nums = _sfre.findall(r'\d+\.?\d*', str(v))
    return float(nums[0]) if nums else default
# ─────────────────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# [FIX 2026-08-10] 并行race condition修复：统一在模块顶部加brahma_brain到path
# 避免各层try块里重复sys.path.insert导致并行时fangcang/hcme/decision_engine丢失
_BRAHMA_BRAIN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'brahma_brain')
if _BRAHMA_BRAIN_PATH not in sys.path:
    sys.path.insert(0, _BRAHMA_BRAIN_PATH)

from brahma_brain.brahma_engine import analyze
from datetime import datetime, timezone
try:
    from config import fmt_beijing
except ImportError:
    def fmt_beijing(): import datetime as _d; return _d.datetime.now(_d.timezone(_d.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")+" CST"

# ============================================================
# 35维矩阵格式化输出
# ============================================================

def fmt_breakdown(bd: dict) -> str:
    """格式化35维矩阵breakdown，按维度分层展示"""
    if not bd:
        return "  (无breakdown数据)"

    sections = {
        "趋势层": ["趋势一致性", "多周期对齐", "OBV方向", "动量背离", "QEW权重"],
        "结构层": ["关键位精确度", "SMC结构", "区间结构", "区间Zone", "区间Zone_v2", "区间底部做多"],
        "RSI层":  ["RSI状态描述", "RSI极端加分_v2", "Phase2c_RSI中性偏强_v2", "RSI极值_v2", "布林带偏离_v2"],
        "量能层": ["量能验证", "量能衰竭+背离共振", "VolProfile", "成交量比率", "形态成熟度"],
        "衍生品层": ["清算/OI", "情绪/费率", "VolSkew", "期权+订单流", "_options_pc", "_options_pc_v56"],
        "外部扩展层": ["鲸鱼+微观", "_smart_money", "_miner_pressure", "_cross_fr_basis", "_causal_regime"],
        "AI/ML层": ["s23_kronos", "ML+在线贝叶斯+滑点", "LSTM+NLP情绪", "HMM乘数", "研究增强层"],
        "宏观层": ["L2+贝叶斯+宏观", "宏观+事件"],
        "时段/体制层": ["时段权重", "N03时段奖励", "N08_牛市RSI中性", "N10_全覆盖奖励",
                       "N15_分层仓位", "N16_ATR体制", "_regime", "_regime_mult"],
    }

    lines = []
    for section, keys in sections.items():
        section_lines = []
        for k in keys:
            if k in bd:
                v = bd[k]
                # 数值类：加符号
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    vstr = f"{v:+g}" if v != 0 else "0"
                else:
                    vstr = str(v)
                # 高亮强信号
                flag = ""
                if isinstance(v, (int, float)):
                    if v >= 15: flag = " 🔥"
                    elif v <= -8: flag = " ⚠️"
                section_lines.append(f"  {k:<22} {vstr}{flag}")
        if section_lines:
            lines.append(f"\n── {section} {'─'*(40-len(section))}")
            lines.extend(section_lines)

    # 剩余未分类字段
    classified = set(k for keys in sections.values() for k in keys)
    extras = [(k,v) for k,v in bd.items()
              if k not in classified and not k.startswith('_T01')]
    if extras:
        lines.append(f"\n── 其他 {'─'*46}")
        for k,v in extras:
            lines.append(f"  {k:<22} {v}")

    return "\n".join(lines)


def fmt_smc(smc: dict, price: float) -> str:
    """格式化SMC结构分析"""
    if not smc:
        return "  (无SMC数据)"

    lines = []
    st = smc.get('structure', {})
    lines.append(f"  市场结构: {st.get('structure', '?')}")

    bos = st.get('bos', [])
    if bos:
        for b in bos[:2]:
            lines.append(f"  BOS: {b['type']} @ {b['level']} — {b.get('note','')}")
    else:
        lines.append("  BOS: 无")

    choch = st.get('choch', [])
    if choch:
        for c in choch[:2]:
            lines.append(f"  CHoCH: {c.get('type','?')} @ {c.get('level','?')} ← 趋势转换信号！🟢")
    else:
        lines.append("  CHoCH: 无（未出现趋势转换信号）")

    sh = st.get('last_sh'); sl2 = st.get('last_sl')
    lines.append(f"  最后摆动高点: {sh}  最后摆动低点: {sl2}")

    # OB
    obs = smc.get('order_blocks', {})
    bull_obs = obs.get('bull_obs', [])
    bear_obs = obs.get('bear_obs', [])
    lines.append(f"\n  [Order Blocks]")
    lines.append(f"  Bull OB: {len(bull_obs)}个  Bear OB: {len(bear_obs)}个")
    if not bull_obs:
        lines.append("  ⚠️ 无Bull OB（当前价格下方无机构成本锚定区）")
    # [苏摩111批准 2026-07-25] OB新鲜度标注升级
    _AGE_MULT = {(0,3):(1.00,'🔥最新鲜'), (4,6):(0.75,'🟡较新鲜'),
                 (7,10):(0.50,'⚠️中等'), (11,49):(0.30,'⚠️较老'), (50,9999):(0.00,'❌已过期')}
    def _age_tag(age):
        for (lo,hi),(mult,label) in _AGE_MULT.items():
            if lo <= age <= hi: return f"age={age}bars ×{mult} {label}"
        return f"age={age}bars"
    for ob in bull_obs[:3]:
        age = ob.get('age_bars', ob.get('age', 0))
        age_str = _age_tag(age)
        lines.append(f"    ▲Bull OB: {ob['low']}~{ob['high']} (距{ob['dist_pct']}%) [{age_str}]")
    for ob in bear_obs[:3]:
        flag = " ← 最近阻力" if ob == bear_obs[0] else ""
        lines.append(f"    ▼Bear OB: {ob['low']}~{ob['high']} (距{ob['dist_pct']}%){flag}")

    # FVG
    fvg = smc.get('fvg', {})
    bull_fvg = fvg.get('bull_fvg', [])
    bear_fvg = fvg.get('bear_fvg', [])
    lines.append(f"\n  [FVG 价格缺口]")
    if not bull_fvg and not bear_fvg:
        lines.append("  无FVG（价格已完全填满所有缺口）")
    for f in bull_fvg[:2]:
        filled = "已填" if f.get('filled') else "未填满 🧲"
        # [P1修复 2026-07-24] FVG主动填充警告
        fill_warn = ""
        if f.get('active_fill_down'):
            fill_warn = f" ⚠️ 正在向下填充！目标FVG底={f.get('fill_target','?')}"
        lines.append(f"    ▲Bull FVG: {f['bottom']}~{f['top']} gap={f['gap_pct']}% {filled}{fill_warn}")
    for f in bear_fvg[:2]:
        filled = "已填" if f.get('filled') else "未填满 🧲"
        lines.append(f"    ▼Bear FVG: {f['bottom']}~{f['top']} gap={f['gap_pct']}% {filled}")
    mg_up = fvg.get('magnet_up'); mg_dn = fvg.get('magnet_down')
    if mg_up: lines.append(f"    FVG磁铁(上方目标): {mg_up}")
    if mg_dn: lines.append(f"    FVG磁铁(下方目标): {mg_dn}")

    # 流动性
    liq = smc.get('liquidity', {})
    lines.append(f"\n  [流动性猎杀区]")
    # [苏摩111批准 2026-07-25] 极近止损池警告（<0.5%触发）
    _near_warn = []
    _eq_highs = liq.get('equal_highs', [])
    _eq_lows  = liq.get('equal_lows', [])
    for x in _eq_highs[:3]:
        dist = abs(float(str(x.get('dist_pct','99')).replace('%','').replace('+','').replace('-','')))
        flag = ' 🚨极近！双边猎杀风险' if dist < 0.5 else ''
        lines.append(f"    等高止损池(上): {x['level']}U  dist={x['dist_pct']}%{flag}")
        if dist < 0.5: _near_warn.append(f"上${x['level']}(+{x['dist_pct']}%)")
    for x in _eq_lows[:3]:
        dist = abs(float(str(x.get('dist_pct','99')).replace('%','').replace('+','').replace('-','')))
        flag = ' 🚨极近！双边猎杀风险' if dist < 0.5 else ''
        lines.append(f"    等低止损池(下): {x['level']}U  dist={x['dist_pct']}%{flag}")
        if dist < 0.5: _near_warn.append(f"下${x['level']}(-{x['dist_pct']}%)")
    if _near_warn:
        lines.append(f"  ⚡ 极近止损池警告：{' / '.join(_near_warn)} → 价格正在猎杀双边止损，方向选择即将发生！")

    # PD Zone
    pd = smc.get('pd_zone', {})
    lines.append(f"\n  [PD Zone]")
    lines.append(f"  Zone={pd.get('zone')}  Bias={pd.get('bias')}  Position={pd.get('position')}  Mid={pd.get('mid')}")
    lines.append(f"  {pd.get('note', '')}")

    # SMC综合评分
    ss = smc.get('score', {})
    lines.append(f"\n  [SMC综合评分: {ss.get('score')}/{ss.get('max')} ({ss.get('grade')})]")
    for d in ss.get('details', []):
        lines.append(f"    {d}")

    return "\n".join(lines)


def fmt_entry(r: dict) -> str:
    """格式化入场参数"""
    lines = []
    cf = r.get('confluence', {})
    price = r.get('price', 0)

    # VIP入场参数
    for k in ['entry', 'entry_lo', 'entry_hi', 'sl', 'tp1', 'tp2', 'tp3', 'rr', 'size_pct']:
        if k in r and r[k]:
            lines.append(f"  {k}: {r[k]}")

    # 计算默认止损 [设计院 2026-08-01 修复] 区分方向，SHORT TP在下方
    if not lines and price:
        _dir = r.get('direction', 'LONG')
        if _dir == 'SHORT':
            sl  = round(price * 1.02, 2)   # 做空SL在上方
            tp1 = round(price * 0.98, 2)   # 做空TP1在下方 -2%
            tp2 = round(price * 0.96, 2)   # 做空TP2在下方 -4%
            sl_label  = '+2.0%'
            tp1_label = '-2.0%'
            tp2_label = '-4.0%'
        else:
            sl  = round(price * 0.98, 2)   # 做多SL在下方
            tp1 = round(price * 1.02, 2)   # 做多TP1在上方 +2%
            tp2 = round(price * 1.04, 2)   # 做多TP2在上方 +4%
            sl_label  = '-2.0%'
            tp1_label = '+2.0%'
            tp2_label = '+4.0%'
        atr = r.get('atr_1h', 400)
        sl_atr = round(abs(price - sl) / max(atr, 1), 2) if atr else '?'
        lines.append(f"  入场区: 等待解封条件满足")
        lines.append(f"  参考止损: {sl}U ({sl_label}, {sl_atr}x ATR)")
        lines.append(f"  参考TP1: {tp1}U ({tp1_label})")
        lines.append(f"  参考TP2: {tp2}U ({tp2_label})")
        del _dir, sl_label, tp1_label, tp2_label

    # [2026-08-21 设计院修正] 清算集群双轨展示
    # 轨道A: OI杠杆分布大级别清算地图（主展示，有真实交易意义）
    # 轨道B: 短期强平历史±0.25%（辅助，仅标注双边猎杀状态）
    try:
        import sys as _sys_liq_tp; _sys_liq_tp.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent / 'brahma_brain'))
        from liq_density_engine import get_liq_density as _get_ld_tp
        _sym = r.get('symbol', '')
        _p   = float(r.get('price', 0))
        if _sym and _p > 0:
            _ld_tp     = _get_ld_tp(_sym, _p)
            _oi_levels = _ld_tp.get('oi_liq_levels', [])
            _top_long  = _ld_tp.get('top_long_liq', {})
            _ab_walls  = _ld_tp.get('above_walls', [])
            _bl_walls  = _ld_tp.get('below_walls', [])

            # ── 轨道A: OI大级别清算地图 ──
            if _oi_levels:
                lines.append(f"  --- 🗺️ OI清算地图（大级别·有交易意义） ---")
                lines.append(f"  📉 多头清算墙（价格下跌→多头被清，真实踩踏风险）:")
                for _lv in sorted(_oi_levels, key=lambda x: x['long_dist_pct'], reverse=True):
                    _lp = _lv['long_liq_price']; _la = _lv['long_liq_usd']
                    _ld = _lv['long_dist_pct'];  _lev = _lv['leverage']
                    _flag = '⚡主力清算层' if _lev == 10 else ('⚠️次级' if _lev == 20 else '')
                    lines.append(f"    {_lev:>3d}x: ${_lp:>10,.1f} ({_ld:.2f}%)  ${_la/1e6:>6.0f}M  {_flag}")
                lines.append(f"  📈 空头清算墙（价格上涨→空头被清，轧空动力）:")
                for _lv in sorted(_oi_levels, key=lambda x: x['short_dist_pct']):
                    _sp = _lv['short_liq_price']; _sa = _lv['short_liq_usd']
                    _sd = _lv['short_dist_pct'];  _lev = _lv['leverage']
                    _flag = '⚡主力清算层' if _lev == 10 else ('⚠️次级' if _lev == 20 else '')
                    lines.append(f"    {_lev:>3d}x: ${_sp:>10,.1f} (+{_sd:.2f}%)  ${_sa/1e6:>6.0f}M  {_flag}")
                if _top_long and _top_long.get('long_liq_price'):
                    _tlp = _top_long['long_liq_price']; _tla = _top_long['long_liq_usd']
                    _tld = (_tlp - _p) / _p * 100
                    lines.append(f"  🎯 最大多头清算集群: ${_tlp:,.1f} ({_tld:.2f}%) = ${_tla/1e6:.0f}M ← 做空核心TP参考")

            # ── 轨道B: 近距强平历史（双边猎杀状态标注）──
            if _ab_walls or _bl_walls:
                _d_up = (_ab_walls[0][0] - _p) / _p * 100 if _ab_walls else 99
                _d_dn = (_p - _bl_walls[0][0]) / _p * 100 if _bl_walls else 99
                if _d_up < 1.0 and _d_dn < 1.0:
                    lines.append(f"  ⚡ 双边猎杀状态: 上方+{_d_up:.2f}% / 下方-{_d_dn:.2f}% 均<1% → 主力正在双向扫描，不宜入场")
                elif _d_up < 1.0:
                    lines.append(f"  ⚡ 上方止损猎杀: +{_d_up:.2f}%极近，注意被扫")
                elif _d_dn < 1.0:
                    lines.append(f"  ⚡ 下方止损猎杀: -{_d_dn:.2f}%极近，注意被砸")
    except Exception as _liq_err:
        pass

    # [P1修复 2026-07-24 klines聚类降级] 清算集群→自动TP/SL优化建议（当三所数据不足时）
    try:
        import urllib.request as _ur, json as _jx
        _sym = r.get('symbol', '')
        _p   = float(r.get('price', 0))
        if _sym and _p > 0:
            _r4h = _jx.loads(_ur.urlopen(
                f'https://fapi.binance.com/fapi/v1/klines?symbol={_sym}&interval=4h&limit=14',
                timeout=5).read())
            _highs = [float(k[2]) for k in _r4h[:-1]]
            _lows  = [float(k[3]) for k in _r4h[:-1]]
            # 聚类（±0.3%容差）
            def _cluster(vals, tol=0.003):
                s = sorted(vals)
                cs = []
                for v in s:
                    placed = False
                    for c in cs:
                        if abs(v - c[0]) / c[0] <= tol: c.append(v); placed = True; break
                    if not placed: cs.append([v])
                return [(round(sum(c)/len(c), 2), len(c)) for c in cs]
            _hc = sorted(_cluster(_highs), key=lambda x: x[0], reverse=True)
            _lc = sorted(_cluster(_lows),  key=lambda x: x[0], reverse=True)
            # 上方密集止损山（做空止损=多头TP目标）
            _tp_hints = [(p,n) for p,n in _hc if p > _p*1.005 and n >= 2][:2]
            # 下方密集止损池（做多止损=SL应在其下方）
            _sl_hints = [(p,n) for p,n in _lc if p < _p*0.995 and n >= 2][:2]
            if _tp_hints or _sl_hints:
                lines.append("  --- 清算集群地图（苏摩111升级 2026-07-25） ---")
            if _tp_hints:
                lines.append("  上方(空头止损山 → 多头TP目标):")
                for p, n in _tp_hints:
                    dist = (p - _p) / _p * 100
                    near = " ⭐最近" if p == _tp_hints[0][0] else ""
                    lines.append(f"    💡 TP参考(止损山{n}次密集): {p:.2f} (+{dist:.2f}%){near}")
            if _sl_hints:
                # [设计院 Fix 2026-07-26] 区分方向：LONG的SL在下方，SHORT的SL在上方
                _direction = r.get('direction', r.get('signal_dir', 'LONG'))
                if _direction == 'SHORT':
                    # SHORT做空：下方止损池 = 止盈目标参考，不是止损！
                    lines.append("  下方密集区(做空TP参考 · ⚠️非做空止损):")
                    for p, n in _sl_hints:
                        dist = (_p - p) / _p * 100
                        near = " ⭐最近" if p == _sl_hints[0][0] else ""
                        lines.append(f"    🎯 TP参考(多头止损池{n}次密集): {p:.2f} (-{dist:.2f}%){near}")
                    # 提示正确的做空止损位置
                    if _tp_hints:
                        real_sl = round(_tp_hints[0][0] * 1.01, 2)
                        real_sl_dist = (real_sl - _p) / _p * 100
                        lines.append(f"  ⚠️ 做空真实止损(应在上方阻力区外): ≈{real_sl:.2f} (+{real_sl_dist:.2f}%)")
                    else:
                        lines.append(f"  ⚠️ 做空止损应在入场区上方约2-2.5%处")
                else:
                    # LONG做多：下方止损池 = 真实SL参考
                    lines.append("  下方(多头止损池 → SL应在其下方):")
                    for p, n in _sl_hints:
                        dist = (_p - p) / _p * 100
                        sl_rec = round(p * 0.985, 2)
                        sl_dist = (_p - sl_rec) / _p * 100
                        near = " ⭐最近" if p == _sl_hints[0][0] else ""
                        lines.append(f"    💡 SL参考(止损池{n}次密集下方): {sl_rec:.2f} (-{sl_dist:.2f}%){near}")
    except Exception:
        pass

    return "\n".join(lines) if lines else "  (等待体制确认后计算)"


def run_analysis(symbol: str, direction: str = 'LONG', compact: bool = False) -> str:
    """
    执行单币种35维全量分析，返回格式化报告字符串
    compact=True: 压缩输出（节省~35% token），用于cron/auto触发场景
    """
    # [2026-08-18 苏摩封印] 分析开始前强制刷新价格缓存，确保使用币安期货合约实时价格
    # 根因：brahma_bus TTL=30s导致跨会话价格复用，报告价格与实时价格最多差30s
    try:
        import urllib.request as _ur_pf, json as _jj_pf
        _pt_pf = _jj_pf.loads(_ur_pf.urlopen(
            f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}',
            timeout=3).read())
        _realtime_price = float(_pt_pf['price'])
        print(f'[分析开始] {symbol} 实时价格: ${_realtime_price:,.2f}')
        # 刷新brahma_bus价格缓存
        try:
            import sys as _sys_pf
            _bd = str(Path(__file__).parent.parent / 'brahma_brain')
            if _bd not in _sys_pf.path: _sys_pf.path.insert(0, _bd)
            from brahma_bus import _BUS as _bus_pf
            _key_pf = f'price:{symbol}'
            _bus_pf._mem.pop(_key_pf, None)  # 删除旧缓存
            _bus_pf._mem[_key_pf] = {'v': _realtime_price, 'exp': __import__('time').time() + 5}
        except Exception:
            pass
    except Exception:
        pass
    # [P0-FIX 2026-08-15 苏摩111] Kronos预加载：cache→full
    # [D6修复 2026-08-17 苏摩111] sys.path.insert移出try块，避免并行race condition
    import sys as _ke_sys, os as _ke_os
    _ke_brain_path = _ke_os.path.join(_ke_os.path.dirname(__file__), '..', 'brahma_brain')
    if _ke_brain_path not in _ke_sys.path:
        _ke_sys.path.insert(0, _ke_brain_path)
    try:
        import kronos_engine as _ke
        if not _ke._model_loaded:
            _ke._load_model()
    except Exception:
        pass  # fail-safe: Kronos不可用时降级cache，不阻断主流程

    t0 = time.time()
    r = analyze(symbol, signal_dir=direction, deep=True)
    elapsed = round(time.time() - t0, 1)

    # [设计院 2026-07-20] params展平修复：entry_lo/entry_hi在r['params']子dict里
    # brahma_1hao_analysis直接调用analyze()绕过了brahma_analysis_runner的展平逻辑
    _p = r.get('params', {}) or {}
    for _k in ['entry_lo','entry_hi','sl','tp1','tp2','rr','rr1','sl_pct','stop_loss']:
        if not r.get(_k) and _p.get(_k):
            r[_k] = _p[_k]

    cf = r.get('confluence', {})
    bd = cf.get('breakdown', {}) if isinstance(cf, dict) else {}
    smc = r.get('smc', {})
    price = r.get('price', 0)

    score_final = r.get('score_final', cf.get('score', '?'))
    score_raw   = r.get('score_final_raw', '?')
    grade_num   = cf.get('grade_num', r.get('grade', '?'))
    grade_label = cf.get('grade', '')
    eff_grade   = r.get('effective_grade', r.get('grade', '?'))
    regime      = r.get('regime_cn', r.get('regime', '?'))
    regime_key  = r.get('regime', '')
    regime_mult = bd.get('_regime_mult', '?')

    # 是否解封
    # 修复：eff_grade可能包含如『🔵中等』的emoji字符，提取纯数字部分
    import re as _re
    _eg_str = str(eff_grade) if eff_grade is not None else '0'
    _eg_nums = _re.findall(r'\d+\.?\d*', _eg_str)
    _eg_num = float(_eg_nums[0]) if _eg_nums else 0.0
    gate_pass = _eg_num >= 80
    gate_str  = "✅ StructureGate 通过 → 可入场" if gate_pass else \
                f"⛔ StructureGate 封禁（grade={eff_grade} < 80）"

    # 新宪法检查
    ema20_1h_note = ""
    if direction == 'LONG':
        # 从breakdown中取EMA信息
        ema_note = bd.get('RSI状态描述', '')
        ema20_1h_note = "⚠️ 新宪法：价格<EMA20_1H → 做多需等站稳确认"

    now_str = fmt_beijing()

    sep = "═" * 58
    sep2 = "─" * 58

    lines = [
        "",
        sep,
        f"  🏛️ 梵天1号工程 · 35维全量矩阵分析",
        f"  {symbol}  {price}U  {now_str}",
        f"  分析耗时: {elapsed}s",
        sep,
        "",
        "▌ GATE-0 · 体制与门控",
        f"  Regime:        {regime}（{regime_key}）× mult={regime_mult}",
        f"  score_final:   {score_final}（raw={score_raw}）",
        "  grade_num:     " + str(grade_num) + "  structure_grade=" + str(cf.get("structure_grade","?")) + "  effective_grade=" + str(eff_grade) + "  grade_mult=" + str(cf.get("grade_mult","?")),  # #6 fix 2026-08-02
        f"  {gate_str}",
    ]
    if ema20_1h_note:
        lines.append(f"  {ema20_1h_note}")

    # [Fix 2026-07-26] BULL_CHOCH + SHORT 矛盾检测
    _choch_list_fx = smc.get('structure', {}).get('choch', [])
    _has_bull_choch = any('BULL' in str(c).upper() for c in _choch_list_fx)
    _choch_conflict_warn = ''
    if direction == 'SHORT' and _has_bull_choch:
        _choch_conflict_warn = (
            '  WARNING: BULL_CHoCH + SHORT = 结构逆势！\n'
            '  BULL_CHoCH表明趋势正在转多，做空面临结构对抗\n'
            '  建议: 等CHoCH回测失败确认后再入场，或仓位减半'
        )

    lines += [
        "",
        "▌ 35维评分矩阵",
        fmt_breakdown(bd),
        "",
    ]

    # ── [结构触碰层渲染 2026-08-15 苏摩111封印] ────────────────────────────────
    _touch_score_bd  = bd.get('结构触碰事件', 0)
    _touch_detail_bd = bd.get('结构触碰详情', '')
    _liq_sweep_bd    = bd.get('清算清扫事件', 0)
    if _touch_score_bd or _liq_sweep_bd:
        _touch_parts = []
        if _touch_score_bd:  _touch_parts.append(f'结构触碰+{_touch_score_bd}')
        if _liq_sweep_bd:    _touch_parts.append(f'清算清扫+{_liq_sweep_bd}')
        if _touch_detail_bd: _touch_parts.append(_touch_detail_bd)
        lines.append(f'▌ 结构触碰事件层 ── {" | ".join(_touch_parts)}')
        lines.append('')
    # ── [END 结构触碰层渲染] ──────────────────────────────────────────────────

    # ── [B/C类模块状态输出 2026-08-15 苏摩111封印] ────────────────────────────
    # 标准输出文档要求: ssi / mode_c / integrity_gate / vol_regime 必须输出
    _bc_lines = []
    _ssi_res = r.get('ssi', {})
    if _ssi_res and isinstance(_ssi_res, dict):
        _ssi_level = _ssi_res.get('level', 'NORMAL')
        _ssi_icon  = '❗' if _ssi_level == 'EXTREME' else '⚠️' if _ssi_level == 'HIGH' else '✅'
        _sr_pct = _ssi_res.get('short_ratio_pct', None)
        _sr_str = f' ({_sr_pct:.1f}%空头占比)' if isinstance(_sr_pct, (int,float)) else ''
        _bc_lines.append(f'  ssi={_ssi_level}{_sr_str} {_ssi_icon}')
    else:
        _bc_lines.append('  ssi=N/A')
    _mc_res = r.get('mode_c', {})
    if _mc_res and isinstance(_mc_res, dict):
        _mc_flag = 'MODE_C ⚠️' if _mc_res.get('is_mode_c') else 'NORMAL ✅'
        _mc_score = _mc_res.get('score', 0)
        _bc_lines.append(f'  mode_c={_mc_flag}  score={_mc_score}')
    else:
        _bc_lines.append('  mode_c=N/A')
    _ig_res = r.get('integrity_gate', {})
    if _ig_res and isinstance(_ig_res, dict):
        _ig_icon   = '✅' if _ig_res.get('passed', True) else '❌'
        _ig_reason = str(_ig_res.get('reason', ''))[:40]
        _bc_lines.append(f'  integrity_gate={_ig_icon}  {_ig_reason or "pass"}')
    else:
        _bc_lines.append('  integrity_gate=N/A')
    _vc_res = (r.get('extra') or {}).get('vol_context', {})
    _vr = _vc_res.get('vol_regime', 'NORMAL') if isinstance(_vc_res, dict) else 'N/A'
    _bc_lines.append(f'  vol_regime={_vr}')
    _kronos_src  = (r.get('extra') or {}).get('kronos_src', 'cache')
    _p_up_val    = r.get('s23_p_up', '?')
    _kronos_icon = '✅' if _kronos_src in ('engine','kronos_full') else '⚠️cache'
    _bc_lines.append(f'  Kronos src={_kronos_src} {_kronos_icon}  p_up={_p_up_val}')
    lines.append('▌ B/C类模块状态')
    lines.extend(_bc_lines)
    lines.append('')
    # ── [END B/C类模块状态输出] ─────────────────────────────────────────────

    # ── [外部智慧层强制输出 2026-08-19 设计院封印] ──────────────────────────────
    # 不管加不加分，全能力分析必须显示所有外部增强层的运行状态
    _ext_lines = []
    # 1. s_macro_v2 — 宏观层 DXY/NQ/BTC.D
    _mv2 = (r.get('extra') or {}).get('macro_v2') or bd.get('_macro_v2_raw') or {}
    if not _mv2:
        # 从extra_data里取（通过r._extra_data路径）
        _mv2 = {}
    _mv2_addon = _mv2.get('score_addon', bd.get('_macro_v2_addon', 0)) if isinstance(_mv2, dict) else 0
    _mv2_notes = _mv2.get('notes', []) if isinstance(_mv2, dict) else []
    _dxy = _mv2.get('dxy', {}) if isinstance(_mv2, dict) else {}
    _dxy_str = f'DXY={_dxy.get("price","?"):.2f}({_dxy.get("direction","?")})' if isinstance(_dxy, dict) and _dxy.get('price') else 'DXY=N/A'
    _nq = _mv2.get('nasdaq', {}) if isinstance(_mv2, dict) else {}
    _nq_str = f'NQ={_nq.get("price","?"):.0f}({_nq.get("direction","?")})' if isinstance(_nq, dict) and _nq.get('price') else 'NQ=N/A'
    _mv2_note_str = ' | '.join(_mv2_notes[:2]) if _mv2_notes else ('score_addon=0 宏观中性' if _mv2 else '未运行')
    _ext_lines.append(f'  s_macro_v2({_mv2_addon:+d}): {_dxy_str} {_nq_str} | {_mv2_note_str}')
    # 2. s_cross — 跨所FR+Basis
    _cfb = (r.get('extra') or {}).get('cross_fr_basis') or {}
    _cfb_adj = bd.get('_cross_fr_basis', '')
    _cfb_score = _cfb.get('score_adj', 0) if isinstance(_cfb, dict) else 0
    _cfb_note = _cfb.get('note', '') if isinstance(_cfb, dict) else ''
    _cfb_fr = _cfb.get('fr_avg', None)
    _cfb_str = f'FR均值={_cfb_fr:.4f}%' if isinstance(_cfb_fr, float) else ''
    _ext_lines.append(f'  s_cross({_cfb_score:+d}): {_cfb_str} {_cfb_note[:60] if _cfb_note else "未运行或无数据"}')
    # 3. CausalVerifier — 体制因果验证
    _cv = (r.get('extra') or {}).get('causal_verifier') or {}
    _cv_verdict = _cv.get('verdict', '?') if isinstance(_cv, dict) else '?'
    _cv_adj2 = _cv.get('score_adj', 0) if isinstance(_cv, dict) else 0
    _cv_reason = _cv.get('reason', '') if isinstance(_cv, dict) else ''
    _ext_lines.append(f'  CausalVerifier({_cv_adj2:+d}): verdict={_cv_verdict} {_cv_reason[:50]}')
    # 4. GEX — 期权Gamma暴露
    _gex_bd = bd.get('s22_gex', '') or bd.get('GEX', '') or ''
    _gex_bd2 = ''
    for _k, _v in bd.items():
        if 'gex' in str(_k).lower() or 'GEX' in str(_k):
            _gex_bd2 = f'{_k}={_v}'
            break
    _ext_lines.append(f'  GEX(s22): {_gex_bd2 or _gex_bd or "数据存在,当前价区无调整"}')
    lines.append('▌ 外部智慧层（完整覆盖审计）')
    lines.extend(_ext_lines)
    lines.append('')
    # ── [END 外部智慧层强制输出] ─────────────────────────────────────────────

    lines += [
        "▌ SMC结构 · FVG · OB · 流动性",
        fmt_smc(smc, price),
        "",
        "▌ 入场参数",
        _choch_conflict_warn,
        fmt_entry(r),
    ]

    # 封印结论
    lines += ["", sep2, "▌ 封印结论"]
    if gate_pass:
        # [FIX 2026-08-02 #3] BULL_CHoCH + SHORT = 结构对抗，降级标注
        if _has_bull_choch and direction == 'SHORT':
            lines += [
                f"  ⚠️ 信号有效（结构对抗）  score={score_final}  grade={eff_grade}",
                f"  方向: {direction}  入场条件具备，但存在BULL_CHoCH结构风险",
                f"  建议: 半仓入场，等CHoCH回测失败后加仓",
            ]
        else:
            lines += [
                f"  ✅ 信号有效  score={score_final}  grade={eff_grade}",
                f"  方向: {direction}  入场条件具备",
            ]
    else:
        # 给出解封条件
        smc_st = smc.get('structure', {})
        has_choch = bool(smc_st.get('choch', []))
        bull_obs = smc.get('order_blocks', {}).get('bull_obs', [])
        bear_obs_nearest = smc.get('order_blocks', {}).get('nearest_bear_ob', {})
        fvg_bull = smc.get('fvg', {}).get('bull_fvg', [])

        lines.append(f"  ⛔ 当前封禁 — 等待解封条件（score={score_final} grade={eff_grade}）：")
        _eg2 = _re.findall(r'\d+\.?\d*', str(eff_grade) if eff_grade is not None else '0')
        grade_gap = round(80.0 - (float(_eg2[0]) if _eg2 else 0.0), 1)
        if not has_choch:
            lines.append(f"    ① CHoCH出现（趋势结构转换信号）")
        if bear_obs_nearest:
            nearest_high = bear_obs_nearest.get('high','')
            nearest_dist = bear_obs_nearest.get('dist_pct', '?')
            lines.append(f"    ② 突破Bear OB: {nearest_high}U（当前距+{nearest_dist}%）")
        if grade_gap > 0:
            lines.append(f"    ③ grade还差{grade_gap}分解封（{eff_grade}→目标≥〈80〉")
            # 分析grade皮颉是哪个模块
            causal = bd.get('_causal_regime', '')
            if 'BLOCKED' in str(causal):
                lines.append(f"       └ 主要阻碍: _causal_regime BLOCKED（-25分，体制因果封锁）")
            _rm_nums = _re.findall(r'\d+\.?\d*', str(regime_mult) if regime_mult else '1')
            bear_mult = float(_rm_nums[0]) if _rm_nums else 1.0
            if bear_mult <= 0.35:
                lines.append(f"       └ 体制乘数{regime_mult}重击，所有分数不足原始得分的{int(bear_mult*100)}%")
        if fvg_bull:
            f0 = fvg_bull[0]
            lines.append(f"    ④ FVG磁吸目标: {f0['bottom']}~{f0['top']}U（未填满{f0['gap_pct']}%）")
        # OB刷新目标
        if bull_obs:
            ob0 = bull_obs[0]
            age0 = ob0.get('age_bars', ob0.get('age', 0))
            lines.append(f"    ① 当前Bull OB锁定: {ob0['low']}~{ob0['high']}（age={age0}，待价格回踩确认）")

    lines.append(sep)

    # ── [P1 LLM Council 2026-08-06 设计院] 本地裁决层——————————————————
    try:
        from brahma_brain.llm_council import council_verdict, format_verdict_line as _fmt_vl
        _liq_d = None
        try:
            import json as _ljson
            _liq_p = __import__('pathlib').Path(__file__).parent.parent / f'data/liq_heatmap_{symbol}.json'
            if _liq_p.exists():
                _liq_d = _ljson.loads(_liq_p.read_text())
        except Exception:
            pass
        _verdict = council_verdict(bd, direction, regime, score_final, _liq_d)
        lines.insert(-1, f"  {_fmt_vl(_verdict, symbol)}")
    except Exception as _lce:
        lines.insert(-1, f"  LLM: 跡过 ({_lce})")
    # ───────────────────────────────────────────────────

    # ── [方案B 第三视角快速审查 2026-08-15 苏摩111] ────────────────────────
    # score≥155时，用 standard(Qwen) 做快速独立审查
    # 答AVOID → 扣10分+写入breakdown；不新建文件、不阻塞主流程
    if score_final >= 155:
        try:
            from brahma_brain.reasoning_client import call_reasoning as _call_r
            _hcme_wr_v = bd.get('HCME相似案例WR', '') or ''
            _kron_v    = bd.get('s23_kronos', '') or ''
            _q = (
                f"梵天信号审查: {symbol} {direction} score={score_final:.0f} "
                f"体制={regime} HCME={_hcme_wr_v} Kronos={_kron_v}\n"
                f"仅用一个英文单词回答: ENTER / WAIT / AVOID"
            )
            _third = _call_r(_q, max_tokens=10, timeout=12, model='standard')
            if _third and 'AVOID' in str(_third).upper():
                score_final += -10
                bd['第三视角_否决'] = -10
                lines.insert(-1, f"  🤖第三视角(Qwen): AVOID 扣分-10")
            elif _third:
                lines.insert(-1, f"  🤖第三视角(Qwen): {str(_third).strip().upper()[:8]}")
        except Exception:
            pass
    # ──────────────────────────────────────────────────────────────────────

    # ══ [设计院 2026-08-08] 方仓铁证层注入 ══════════════════════════════
    try:
        from brahma_brain.fangcang_engine import get_fangcang_context as _get_fc
        _fc_regime = r.get('regime') or None
        _fc_data = _get_fc(symbol=symbol, current_regime=_fc_regime)
        if _fc_data and _fc_data.get('status') == 'ok':
            _pm = _fc_data.get('prob_matrix', {})
            _mf = _fc_data.get('main_force_intent', {})
            _ms2 = _fc_data.get('micro_structure', {})
            _top3 = _fc_data.get('top3_summary', '')
            _ev = _pm.get('ev', 0)
            _p_up = _pm.get('p_up', 0)
            _p_dn = _pm.get('p_down', 0)
            _n = _pm.get('n', 0)
            _max_up = _pm.get('max_upside', 0)
            _max_dn = _pm.get('max_downside', 0)
            _intent = _mf.get('intent', 'N/A')
            _conf = _mf.get('confidence', 0)
            _trap = _fc_data.get('trap_alert', False)
            _evidence = _mf.get('evidence', [])
            _fc_lines = [
                "",
                "╬" + "═"*58,
                "  🗃️ 方仓铁证层 (FangCang · 6.5年历史相似案例)",
                "╬" + "═"*58,
                f"  体制: {_fc_regime}  相似案例数: {_n}  置信度: {_conf:.0%}",
                f"  概率矩阵: ↑{_p_up:.0%} ↓{_p_dn:.0%}  EV={_ev:+.2f}%",
                f"  历史最大涨幅: +{_max_up:.1f}%  历史最大跌幅: {_max_dn:.1f}%",
                f"  主力意图: {_intent}  {'⚠️ 陷阱预警' if _trap else '✅ 无陷阱'}",
            ]
            if _evidence:
                for _ev_item in _evidence:
                    _fc_lines.append(f"  · {_ev_item}")
            if _top3:
                _fc_lines.append("  Top3历史案例:")
                for _tl in _top3.strip().split('\n'):
                    _fc_lines.append(f"  {_tl}")
            # [2026-08-20] 阶段2：展示周月线锚定 + Elliott + VPA
            _htf = _fc_data.get('htf_anchor', {})
            _ew  = _fc_data.get('elliott_wave', {})
            _vpa = _fc_data.get('vpa', {})
            if _htf and _htf.get('_anchor_summary'):
                _fc_lines.append(f"  \u3010HTF周月线锚定】{_htf['_anchor_summary']}")
                w3_pos = _htf.get('weekly_pos', 0.5)
                w_trend = _htf.get('weekly_trend', 0)
                m_trend = _htf.get('monthly_trend', 0)
                htf_conf = _htf.get('htf_confluence', 0.5)
                _w52h = _htf.get('_w52_high', 0)
                _w52l = _htf.get('_w52_low', 0)
                _fc_lines.append(f"  52W区间: ${_w52l:,.0f}~${_w52h:,.0f} | 周线位置:{w3_pos*100:.0f}% | HTF共振:{htf_conf:.2f}")
            if _ew and _ew.get('wave_type') != 'UNKNOWN':
                _fc_lines.append(f"  \u3010Elliott波浪】{_ew.get('summary','')}")
                if _ew.get('fib_levels'):
                    _fibs = _ew['fib_levels']
                    _fib_str = '  '.join(f"{k}=${v:,.0f}" for k,v in list(_fibs.items())[:3])
                    _fc_lines.append(f"    波浪目标位: {_fib_str}")
            if _vpa and _vpa.get('vpa_signal') and _vpa.get('vpa_signal') != '无信号':
                _fc_lines.append(f"  \u3010VPA成交量\u3011{_vpa.get('summary','')}")
            lines += _fc_lines
    except Exception as _fc_err:
        lines.append(f"  [方仓层] 跳过: {_fc_err}")
    # ══ [END 方仓层] ══════════════════════════════════════════════════

    # ══ [阶段3 2026-08-20] 跨品种宏观相关性层 ═══════════════════════════════
    try:
        from brahma_brain.cross_asset_correlator import get_cross_asset_context as _get_cross
        _cross = _get_cross(symbol=symbol, current_price=float(r.get('price', 0) or 0))
        if _cross:
            _vix_r   = _cross.get('vix', {})
            _rate_r  = _cross.get('rates', {})
            _dxy3_r  = _cross.get('dxy', {})
            _btcd_r  = _cross.get('btcd', {})
            _total_addon = _cross.get('score_addon_total', 0)
            _cross_lines = [
                "",
                "╬" + "═"*58,
                "  🌐 跨品种宏观层（阶段3 · VIX+利率+DXY+BTC.D）",
                "╬" + "═"*58,
                f"  VIX={_vix_r.get('vix_now','N/A')} [{_vix_r.get('vix_regime','N/A')}] {_vix_r.get('vix_trend','')} | 影响:{_vix_r.get('btc_impact','')} | 加成:{_vix_r.get('score_addon',0):+d}",
                f"  US10Y={_rate_r.get('rate_now','N/A')}% [{_rate_r.get('rate_regime','N/A')}] {_rate_r.get('rate_trend','')} | 加成:{_rate_r.get('score_addon',0):+d}",
                f"  DXY={_dxy3_r.get('dxy_now','N/A')} [{_dxy3_r.get('dxy_signal','N/A')}] 90日相关:{_dxy3_r.get('corr_90d','N/A')} | 加成:{_dxy3_r.get('score_addon',0):+d}",
                f"  BTC.D代理[{_btcd_r.get('signal','N/A')}] BTC_90日:{_btcd_r.get('btc_90d_pct','N/A')}%({_btcd_r.get('percentile',0)*100:.0f}%分位) 山寨季:{'✅' if _btcd_r.get('altcoin_season') else '❌'} | 加成:{_btcd_r.get('score_addon',0):+d}",
                f"  宏观层总加成: {_total_addon:+d}",
            ]
            lines += _cross_lines
    except Exception as _cross_err:
        lines.append(f"  [跨品种宏观层] 跳过: {_cross_err}")
    # ══ [END 跨品种宏观层] ══════════════════════════════════════════════


    # ══ [设计院 2026-08-08] HCME情境匹配层注入 ═══════════════════════════════
    try:
        from hcme_matcher import get_hcme_matcher as _get_hcme
        _hcme = _get_hcme()
        _hcme_signal = {
            'symbol': symbol,
            'direction': direction,
            'regime': r.get('regime', ''),
            'score': float(str(r.get('score_final', 0)).split()[0]) if r.get('score_final') else 0,
            'rsi_1h': float(str(r.get('rsi_1h', 50)).split()[0]) if r.get('rsi_1h') else 50,
            'rsi_4h': float(str(r.get('rsi_4h', 50)).split()[0]) if r.get('rsi_4h') else 50,
            'bbw': float(str(r.get('bbw', 2.0)).split()[0]) if r.get('bbw') else 2.0,
            'price': float(r.get('price', 0)),
        }
        _hcme_result = _hcme.find_similar(_hcme_signal, top_k=5)
        if _hcme_result and isinstance(_hcme_result, dict):
            _hwr = _hcme_result.get('historical_wr', 0)
            _hconf = _hcme_result.get('confidence', 0)
            _hadj = _hcme_result.get('hcme_score_adj', 0)
            _hsims = _hcme_result.get('similar_cases', [])
            _hcme_lines = [
                "",
                "╬" + "═"*58,
                "  🔍 HCME情境匹配 (hcme_matcher · 历史相似信号对标)",
                "╬" + "═"*58,
                f"  相似案例数: {len(_hsims)}  历史WR: {_hwr:.1%}  置信度: {_hconf:.2f}",
                f"  HCME评分调整: {'+' if _hadj>=0 else ''}{_hadj}分",
            ]
            for _hs in _hsims[:3]:
                _outcome = _hs.get('outcome', '?')
                _pnl = _hs.get('pnl_pct', 0)
                _sim = _hs.get('similarity', 0)
                _icon = '✅' if 'TP' in str(_outcome) or 'WIN' in str(_outcome) else '❌' if 'SL' in str(_outcome) or 'LOSS' in str(_outcome) else '⏳'
                _hcme_lines.append(f"  {_icon} sim={_sim:.3f} outcome={_outcome} pnl={_pnl:+.2f}%")
            lines += _hcme_lines
    except Exception as _hcme_err:
        lines.append(f"  [HCME匹配层] 跳过: {_hcme_err}")
    # ══ [END HCME层] ══════════════════════════════════════════════════════

    # ══ [设计院 2026-08-08] 决策树层注入 ════════════════════════════════
    try:
        from brahma_brain.brahma_decision_engine import get_decision_engine as _get_de
        _de = _get_de()
        _de_signal = {
            'symbol': symbol,
            'direction': direction,
            'regime': r.get('regime', ''),
            'score': float(str(r.get('score_final', 0)).split()[0]) if r.get('score_final') else 0,
            'grade': float(str(r.get('effective_grade', 0)).split()[0]) if r.get('effective_grade') else 0,
            'sl_pct': float(str(r.get('sl_pct', 2.0)).split()[0]) if r.get('sl_pct') else 2.0,
            'timing': r.get('timing', ''),
            'price': float(r.get('price', 0)),
        }
        _de_result = _de.decide(_de_signal)
        _de_action = _de_result.get('action', 'SKIP')
        _de_reason = _de_result.get('reason', '')
        _de_step = _de_result.get('step_passed', 0)
        _de_icon = '✅' if _de_action == 'EXECUTE' else '⏸️' if _de_action in ('WAIT_15M','WAIT_ENTRY') else '⛔'
        # [升级 2026-08-11 苏摩111] 决策树权威司令部格式
        _STEP_NAMES = {
            1: 'Step1 体制死穴门控',
            2: 'Step2 StructureGate(grade≥80)',
            3: 'Step3 15m结构确认',
            4: 'Step4 RR门控(≥1.0)',
            5: 'Step5 时机门控(READY)',
        }
        _ACTION_LABELS = {
            'EXECUTE':    ('🟢 ENTER',  '信号解锁 — 满足全部条件，建议开仓'),
            'WATCH':      ('🟡 WATCH',  '候补观察 — 接近触发，等待最终确认'),
            'SKIP':       ('🔴 SKIP',   '本轮放弃 — 条件未满足，不入场'),
            'WAIT_15M':   ('⏸️ WAIT',   '等待15m确认 — 结构待验证'),
            'WAIT_ENTRY': ('⏸️ WAIT',   '等待入场区 — 价格未到位'),
        }
        _al = _ACTION_LABELS.get(_de_action, ('⛔ SKIP', _de_reason))
        _action_label, _action_desc = _al

        # 构造步骤漏斗
        _steps_detail = _de_result.get('steps', {})
        _step_lines = []
        for _sn in range(1, 6):
            _skey = f'step{_sn}'
            _sval = _steps_detail.get(_skey)
            if _sn <= _de_step:
                _step_lines.append(f"  {'✅'} {_STEP_NAMES[_sn]}")
            elif _sval is not None:
                _step_lines.append(f"  {'❌'} {_STEP_NAMES[_sn]} — {str(_sval)[:50]}")
            elif _sn == _de_step + 1:
                _step_lines.append(f"  {'❌'} {_STEP_NAMES[_sn]} — {_de_reason[:50]}")
            else:
                _step_lines.append(f"  {'⬜'} {_STEP_NAMES[_sn]}")

        # 入场参数
        _ep = _de_result.get('entry_plan', {})
        _ep_line = ''
        if _ep and _de_action in ('EXECUTE','WATCH'):
            _ep_line = (f"  📐 开仓参数: {_ep.get('size_pct','5')}%NAV × {_ep.get('leverage','5')}x | "
                        f"SL={_ep.get('sl_pct','2.0')}% | TP1={_ep.get('tp1_pct','?')}%")
        elif _de_action == 'EXECUTE':
            _sl_pct = _de_signal.get('sl_pct', 2.0)
            _rr = _de_result.get('rr', 0)
            _ep_line = f"  📐 开仓参数: 5%NAV × 5x | SL={_sl_pct}% | RR={_rr:.2f}x"

        # RR详情（Step4相关）
        _rr_val = _de_result.get('rr', 0)
        _tp_price = _de_result.get('tp_price', 0)
        _sl_price = _de_result.get('sl_price', 0)
        _rr_detail = ''
        if _rr_val or _tp_price:
            _rr_detail = (f"  📊 RR计算: TP={_tp_price:,.1f}({'+' if _de_signal.get('price',0) and _tp_price > _de_signal['price'] else ''}{((float(_tp_price)-float(_de_signal.get('price',1)))/float(_de_signal.get('price',1))*100):.2f}%) "
                         f"SL={_sl_price:,.1f}(-{_de_signal.get('sl_pct',2.0)}%) RR={_rr_val:.2f}x {'✅' if _rr_val >= 1.0 else f'❌(需≥1.0，差{1.0-_rr_val:.2f})'}") if _tp_price and _sl_price else f"  RR={_rr_val:.2f}x"

        _de_lines = [
            "",
            "╔" + "═"*58 + "╗",
            "║  🏛️ 决策树司令部 — 梵天最终裁决                        ║",
            "╠" + "═"*58 + "╣",
            f"  {_action_label}  {_action_desc}",
            "  " + "─"*56,
            "  漏斗5步:",
        ] + _step_lines + [
            "  " + "─"*56,
        ]
        if _rr_detail:
            _de_lines.append(_rr_detail)
        if _ep_line:
            _de_lines.append(_ep_line)
        if _de_action == 'SKIP' and _de_step < 5:
            _fail_step = _STEP_NAMES.get(_de_step+1, f'Step{_de_step+1}')
            _de_lines.append(f"  🔓 解封条件: 突破 [{_fail_step}] 门控")
        _de_lines.append("╚" + "═"*58 + "╝")
        lines += _de_lines
    except Exception as _dt_err:
        lines.append(f"  [决策树层] 跳过: {_dt_err}")
    # ══ [END 决策树层] ════════════════════════════════════════════════

    full_report = "\n".join(lines)
    if compact:
        compact_lines = [
            sep,
            f"  🏛️ {symbol} · 梵天1号工程 · compact模式",
            f"  体制: {regime}({regime_key}) mult={regime_mult}",
            f"  score={score_final} grade={eff_grade} {'✅通过' if gate_pass else '⛔封禁'}",
            f"  价格: {price}",
        ]
        # v5.1调整
        v51 = r.get('v51_reason')
        if v51:
            compact_lines.append(f"  v5.1: {v51}")
        # 入场参数（核心）
        entry_lo = r.get('entry_lo'); entry_hi = r.get('entry_hi')
        sl = r.get('stop_loss'); tp1 = r.get('tp1'); tp2 = r.get('tp2')
        if entry_lo:
            # [Fix 2026-07-26] 验证SL方向：SHORT的SL应在入场区上方
            _d = r.get('direction', r.get('signal_dir', 'LONG'))
            _sl_ok = True
            if _d == 'SHORT' and sl and entry_hi and _safe_float(sl) < _safe_float(entry_hi):
                _sl_ok = False
                _sl_warn = f"⚠️SL方向错误(应>{entry_hi})"
            elif _d == 'LONG' and sl and entry_lo and _safe_float(sl) > _safe_float(entry_lo):
                _sl_ok = False
                _sl_warn = f"⚠️SL方向错误(应<{entry_lo})"
            else:
                _sl_warn = ''
            compact_lines.append(f"  入场: {entry_lo}~{entry_hi}  SL:{sl}{' '+_sl_warn if not _sl_ok else ''}  TP1:{tp1}  TP2:{tp2}")

            # ── [P0/P1/P2 VIP策略校验层 2026-08-15 苏摩111封印] ─────────────────
            # P0: 价格量级验证 | P1: 参数来源=engine | P2: 妖币时效性门控
            try:
                from brahma_brain.vip_validator import validate_vip_strategy
                _chg24 = float(r.get('chg24', r.get('change_24h', 0)) or 0)
                _oi_cached = float(r.get('oi_change_1h', 0) or 0)
                _ls_cached = float(r.get('long_ratio', 50) or 50)
                _vip_check = validate_vip_strategy(
                    symbol=symbol,
                    direction=_d,
                    entry_lo=float(entry_lo),
                    entry_hi=float(entry_hi or entry_lo),
                    sl=float(sl or 0),
                    tp1=float(tp1 or 0),
                    chg_24h_pct=_chg24,
                    cached_oi_change=_oi_cached,
                    cached_long_pct=_ls_cached,
                    source='engine',  # 参数严格来自engine，非AI推算
                )
                compact_lines.append(f"  ─── VIP校验 ───")
                compact_lines.append(f"  {_vip_check['summary']}")
                for _vl in _vip_check['vip_header'].split('\n')[1:]:
                    if _vl.strip():
                        compact_lines.append(f"  {_vl}")
                if not _vip_check['valid']:
                    compact_lines.append(f"  ❌ 策略参数已失效，禁止发帖，需重新分析")
            except Exception:
                pass  # fail-safe
            # ── [END VIP策略校验层] ────────────────────────────────────────────
        # CHoCH状态
        smc_st2 = smc.get('structure', {})
        choch2 = smc_st2.get('choch', [])
        if choch2:
            compact_lines.append(f"  CHoCH: {choch2[0] if choch2 else 'None'}")
        compact_lines.append(sep)
        return "\n".join(compact_lines)

    # [2026-07-22] TradFi补充层注入（美股代币专属）
    if symbol.upper() in _TRADFI_SYMBOLS:
        tradfi_lines = _build_tradfi_supplement(symbol, r)
        full_report = full_report + "\n" + "\n".join(tradfi_lines)

    # [2026-07-24 设计院自主决策] OB+清算集群MIX层注入（全标的通用）
    try:
        current_price = float(r.get('mark_price', r.get('price', 0)))
        if current_price <= 0:
            import urllib.request as _ur, json as _jj
            _pt = _jj.loads(_ur.urlopen(
                f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}',
                timeout=5).read())
            current_price = float(_pt['price'])
        ob_liq_lines = _build_ob_liquidation_layer(symbol, current_price, engine_result=r)
        full_report = full_report + "\n" + "\n".join(ob_liq_lines)
    except Exception as _e:
        full_report = full_report + f"\n  [OB清算层] 跳过: {_e}"

    # ── [P0~P4 设计院封印 2026-07-24 苏摩111批准] ──────────────────────────────
    try:
        from brahma_brain.anomaly_guards import (
            detect_vol_price_anomaly, detect_correlation_alert,
            detect_regime_switch_warning, fmt_no_bull_ob_template
        )
        from brahma_brain.position_guard import fmt_position_guard

        _price  = float(r.get('price', 0))
        _regime = r.get('regime', '')
        _smc    = r.get('smc', {})
        _bull_obs = _smc.get('order_blocks', {}).get('bull_obs', [])
        _bear_obs = _smc.get('order_blocks', {}).get('bear_obs', [])
        _choch_list = _smc.get('structure', {}).get('choch', [])
        _choch_dir  = _choch_list[0] if _choch_list else ''
        _eg3 = _re.findall(r'\d+\.?\d*', str(r.get('effective_grade', 0) or 0))
        _grade  = float(_eg3[0]) if _eg3 else 0.0
        _eg4 = _re.findall(r'\d+\.?\d*', str(r.get('score_final', 0) or 0))
        _score  = float(_eg4[0]) if _eg4 else 0.0

        # P0: 持仓风控
        _pos_guard = fmt_position_guard(symbol, _price, _regime)
        if _pos_guard:
            full_report = full_report + "\n" + _pos_guard

        # P1: 量价异常检测
        _vol_anom = detect_vol_price_anomaly(symbol)
        if _vol_anom.get('anomaly'):
            full_report = full_report + (
                f"\n\n▌ P1 · 量价异常预警\n  {_vol_anom['message']}")

        # P2: 多币联动预警（1H跌幅估算）
        try:
            import urllib.request as _ur2, json as _jj2
            _kl1h = _jj2.loads(_ur2.urlopen(
                f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=3',
                timeout=5).read())
            _1h_chg = (float(_kl1h[-2][4]) - float(_kl1h[-2][1])) / float(_kl1h[-2][1])
        except Exception:
            _1h_chg = 0.0
        _corr = detect_correlation_alert(symbol, _1h_chg)
        if _corr.get('alert'):
            full_report = full_report + (
                f"\n\n▌ P2 · 联动预警\n  {_corr['message']}")

        # P3: 框架切换机制
        _sw = detect_regime_switch_warning(_regime, str(_choch_dir), _grade, _score)
        if _sw.get('warning'):
            full_report = full_report + (
                f"\n\n▌ P3 · 框架切换\n  {_sw['message']}")

        # P4: Bull OB=0 模板重写（替换进场区外推建议）
        if len(_bull_obs) == 0 and _price > 0:
            # 从已有MIX层提取止损池信息（简化：直接给Bear OB）
            _liq_pools = {}
            _p4_note = fmt_no_bull_ob_template(symbol, _price, _bear_obs, _liq_pools)
            full_report = full_report + f"\n\n▌ P4 · 结构真空区提示\n  {_p4_note}"

    except Exception as _pg_e:
        pass  # P0~P4异常不阻断主输出
    # ── [P0~P4 END] ────────────────────────────────────────────────────────

    # ── [P3信号生命周期+P5实时审计 2026-07-26 苏摩授权封印] ──────────
    try:
        from brahma_brain.signal_lifecycle import tick_signal_lifecycle, audit_score_with_realtime
        # P3: 生命周期检查
        _lc_alerts = tick_signal_lifecycle(symbol, _price)
        if _lc_alerts:
            _lc_lines = []
            for _a in _lc_alerts:
                lvl = _a.get('level', 'INFO')
                _icon = '🚨' if lvl=='CRITICAL' else '✅' if lvl=='SUCCESS' else '⏰'
                _lc_lines.append(f"  {_icon} {_a['msg']}")
            full_report = full_report + (
                f"\n\n\u258c P3 \u00b7 \u4fe1\u53f7\u751f\u547d\u5468\u671f\n" + '\n'.join(_lc_lines))
        # P5: \u5173\u952e\u7ef4\u5ea6\u5b9e\u65f6\u6570\u636e\u5ba1\u8ba1
        _bd = r.get('breakdown', {})
        if _bd:
            _p5 = audit_score_with_realtime(symbol, _bd)
            _rt = _p5.get('_P5_realtime', {})
            if _rt and 'error' not in _rt:
                _vol_data = _rt.get('\u91cf\u80fd\u8870\u7aed_\u5b9e\u6d4b', {})
                _div_data = _rt.get('\u5e95\u80cc\u79bb_\u5b9e\u6d4b', {})
                _k_cur1h = '\u5f53\u524d1H\u91cf'
                _k_ma5 = 'MA5\u5747\u91cf'
                _k_decay = '\u8870\u51cf\u7387'
                _k_obv = 'OBV\u65b9\u5411'
                _k_valid = '\u8bc4\u5206\u662f\u5426\u5408\u7406'
                _k_rsi1h = '\u5f53\u524dRSI_1H'
                _k_plow_cur = '\u4ef7\u683c\u4f4e\u70b9_\u5f53\u524d'
                _k_plow_prev = '\u4ef7\u683c\u4f4e\u70b9_\u524d\u671f'
                _k_div_ok = '\u5e95\u80cc\u79bb_\u662f\u5426\u6210\u7acb'
                _p5_lines = [
                    (f"  \u91cf\u80fd\u5b9e\u6d4b({_rt.get('ts','')}): "
                     f"\u5f53\u524d1H\u91cf={_vol_data.get(_k_cur1h,'?')} "
                     f"MA5={_vol_data.get(_k_ma5,'?')} "
                     f"\u8870\u51cf\u7387={_vol_data.get(_k_decay,'?')} "
                     f"OBV={_vol_data.get(_k_obv,'?')} "
                     f"[{_vol_data.get(_k_valid,'?')}]"),
                    (f"  \u5e95\u80cc\u79bb\u5b9e\u6d4b: RSI1H={_div_data.get(_k_rsi1h,'?')} "
                     f"\u4ef7\u683c\u4f4e\u70b9({_div_data.get(_k_plow_cur,'?')} vs {_div_data.get(_k_plow_prev,'?')}) "
                     f"[{_div_data.get(_k_div_ok,'?')}]"),
                ]
                full_report = full_report + (
                    f"\n\n\u258c P5 \u00b7 \u8bc4\u5206\u5b9e\u65f6\u5ba1\u8ba1\n" + '\n'.join(_p5_lines))
    except Exception as _lc_e:
        pass  # P3/P5\u5f02\u5e38\u4e0d\u963b\u65ad\u4e3b\u8f93\u51fa
    # ── [P3/P5 END] ───────────────────────────────────────────────────

    return full_report


# ============================================================
# [2026-07-22 苏摩111封印] 美股代币专属维度层
# ============================================================

_TRADFI_SYMBOLS = {
    # 半导体存储（核心）
    'MUUSDT','SNDKUSDT','SKHYNIXUSDT','SKHYUSDT','SOXLUSDT','SOXSUSDT',
    'DRAMUSDT','AMDUSDT','NVDAUSDT','INTCUSDT','MRVLUSDT','SNXXUSDT',
    'SAMSUNGUSDT','TSMUSDT',
    # 贵金属
    'XAUUSDT','XAGUSDT',
    # 原油
    'CLUSDT','BZUSDT',
    # 指数/ETF
    'SPCXUSDT','SPXUSDT','QQQUSDT','SPYUSDT','KORUUSDT','EWYUSDT','IWMUSDT',
    # 科技巨头
    'TSLAUSDT','METAUSDT','MSFTUSDT','GOOGLUSDT','COINUSDT','MSTRUSDT',
    'HOODUSDT','PLTRUSDT','CRWDUSDT','NFLXUSDT','AMZNUSDT','AAPLUSDT',
}

# 基本面数据映射（仍保留对链上代币的支持）
# TRADIFI_PERPETUAL合约无需RWA地址，直接读取PE数据通过其他渠道
_RWA_CONTRACTS = {}  # TRADIFI_PERPETUAL合约不依赖RWA合约地址


def _get_rwa_fundamentals(symbol: str) -> dict:
    """获取美股代币基本面（PE/52W高低/市值）"""
    import urllib.request, json as _json
    chain_id, contract = _RWA_CONTRACTS.get(symbol, ('', ''))
    if not chain_id:
        return {}
    try:
        url = (f'https://www.binance.com/bapi/defi/v2/public/wallet-direct/'
               f'buw/wallet/market/token/rwa/dynamic/ai'
               f'?chainId={chain_id}&contractAddress={contract}')
        req = urllib.request.Request(url, headers={'Accept-Encoding': 'identity',
                                                    'User-Agent': 'brahma/2.0'})
        d = _json.loads(urllib.request.urlopen(req, timeout=8).read())
        si = (d.get('data') or {}).get('stockInfo') or {}
        ti = (d.get('data') or {}).get('tokenInfo') or {}
        return {
            'pe':          si.get('priceToEarnings'),
            'h52w':        si.get('priceHigh52w'),
            'l52w':        si.get('priceLow52w'),
            'mktcap_b':    round(float(si.get('marketCap') or 0) / 1e9, 2),
            'div_yield':   si.get('dividendYield'),
            'stock_price': si.get('price'),
        }
    except Exception:
        return {}


def _build_ob_liquidation_layer(symbol: str, price: float, engine_result: dict = None) -> list:
    """一号工程 MIX层：OB + 清算集群 + OI变化 + 多空比
    [2026-07-24 设计院自主决策 苳天一号工程 MIX 封印]
    """
    import urllib.request, json as _json, time
    from datetime import datetime, timezone
    lines = []
    lines.append("╬" + "═" * 58)
    lines.append("  🎯 OB + 清算集群 + OI异动层 (MIX增强)")
    lines.append("╬" + "═" * 58)

    # ── 1. L2订单簿[P2修复:3快照均值防失真 2026-07-24] ─────────────
    try:
        import time as _time
        _ratios = []; _bids_last = []; _asks_last = []
        for _snap in range(3):
            _r = urllib.request.urlopen(
                f'https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=20', timeout=5)
            _bk = _json.loads(_r.read())
            _b = [(float(p), float(q)) for p,q in _bk['bids']]
            _a = [(float(p), float(q)) for p,q in _bk['asks']]
            _tb = sum(q for _,q in _b[:10]); _ta = sum(q for _,q in _a[:10])
            _ratios.append(_tb / max(_ta, 0.001))
            _bids_last, _asks_last = _b, _a
            if _snap < 2: _time.sleep(0.35)
        ba_ratio = round(sum(_ratios)/len(_ratios), 2)
        ba_vol   = round(max(_ratios)-min(_ratios), 2)
        bids, asks = _bids_last, _asks_last
        ratio_tag = '✅ 多头占优' if ba_ratio > 1.3 else ('⚠️ 空头占优' if ba_ratio < 0.8 else '中性')
        vol_note  = f' (波动幅{ba_vol:.2f},高波动)' if ba_vol > 0.5 else ''
        max_bid = max(bids[:10], key=lambda x: x[1], default=(0,0))
        big_ask = max(asks[:10], key=lambda x: x[1], default=(0,0))
        lines2 = []
        lines2.append(f"  L2买卖比(3快照均值): {ba_ratio}x {ratio_tag}{vol_note}")
        lines2.append(f"  最大买墙: ${max_bid[0]:.2f} ({max_bid[1]:.2f}张)  "
                     f"最大卖墙: ${big_ask[0]:.2f} ({big_ask[1]:.2f}张)")
        lines.extend(lines2)
    except Exception as e:
        lines.append(f"  L2订单簿: 获取失败 ({e})")
        bids, asks = [], []

    # ── 2. OI历叵8H变化 ──────────────────────────────────────────
    try:
        r_oi = urllib.request.urlopen(
            f'https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=8',
            timeout=5)
        oi_hist = _json.loads(r_oi.read())
        oi_vals = [float(x['sumOpenInterest']) for x in oi_hist]
        if len(oi_vals) >= 2:
            oi_chg1h = (oi_vals[-1] - oi_vals[-2]) / oi_vals[-2] * 100
            oi_chg8h = (oi_vals[-1] - oi_vals[0])  / oi_vals[0]  * 100
            oi_trend = '\u25b2OI流入' if oi_chg1h > 0.5 else ('\u25bcOI减仓' if oi_chg1h < -0.5 else '\u2500OI扁平')
            # [P1修复 2026-07-24] 1H连续验测OI斜率（解决ETH OI滞后问题）
            # 若连续3H OI下降且总幅度>1%，即使1H变化扁平也发出警告
            if len(oi_vals) >= 4:
                oi_3h_slope = [oi_vals[-(i+1)] - oi_vals[-(i+2)] for i in range(3)]
                oi_3h_all_down = all(v < 0 for v in oi_3h_slope)
                oi_3h_total = (oi_vals[-1] - oi_vals[-4]) / oi_vals[-4] * 100
                if oi_3h_all_down and oi_3h_total < -1.0:
                    oi_trend += f'  ⚠️连续3H下降斜率={oi_3h_total:.2f}%(持仓耗尽预警)'
            lines.append(f"  OI 1H变化: {oi_chg1h:+.2f}% {oi_trend}  8H: {oi_chg8h:+.2f}%")
        else:
            lines.append(f"  OI: 当前持仓量={oi_vals[-1]:.0f}张")
    except Exception as e:
        lines.append(f"  OI层: 获取失败 ({e})")
        oi_vals = []

    # ── 3. 多空比(LSR) ─ 优先使用引擎缓存，降低双源时间差 ────────────────────
    # [P0修复 2026-07-24] 统一LSR数据源：先尝试从engine_result读取，再实时拉
    try:
        # 优先从外层engine_result获取（与N20同源，无时间差）
        _cached_long = None
        if engine_result is not None:
            _sent = engine_result.get('sentiment', {})
            _cached_long = _sent.get('long_short_ratio')

        if _cached_long is not None:
            long_pct  = float(_cached_long)
            short_pct = round(100 - long_pct, 1)
            lsr_val   = round(long_pct / max(short_pct, 0.01), 3)
            _src_tag  = '(引擎同源)'
        else:
            # 回退：实时拉取
            r_lsr = urllib.request.urlopen(
                f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio'
                f'?symbol={symbol}&period=1h&limit=2', timeout=5)
            lsr_data = _json.loads(r_lsr.read())
            latest   = lsr_data[-1] if lsr_data else {}
            long_pct  = float(latest.get('longAccount', 0.5)) * 100
            short_pct = float(latest.get('shortAccount', 0.5)) * 100
            lsr_val   = float(latest.get('longShortRatio', 1.0))
            _src_tag  = '(实时拉取)'

        lsr_tag = ''
        if long_pct > 65:   lsr_tag = ' ⚠️ 多头拥挤(>65%), 踩踏风险升'
        elif long_pct < 40: lsr_tag = ' ✅ 多头种实, 反弹动能充足'
        lines.append(f"  多空比: {lsr_val:.3f}  多={long_pct:.1f}% 空={short_pct:.1f}%{lsr_tag} {_src_tag}")
    except Exception as e:
        lines.append(f"  多空比: 获取失败 ({e})")

    # ── 4. 清算集群估算(基于4H高低点+杠杆分布) ────────────────────────────────
    try:
        r_4h = urllib.request.urlopen(
            f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit=14',
            timeout=5)
        kl4h = _json.loads(r_4h.read())
        highs4h = [float(k[2]) for k in kl4h[:-1]]  # 除当前未完成根
        lows4h  = [float(k[3]) for k in kl4h[:-1]]
        # 识别止损池：重复出现的高点/低点簇(±0.3%)
        def find_clusters(vals, tol_pct=0.003):
            vals_s = sorted(vals)
            clusters = []
            for v in vals_s:
                found = False
                for c in clusters:
                    if abs(v - c[0]) / c[0] <= tol_pct:
                        c.append(v); found = True; break
                if not found: clusters.append([v])
            return [(round(sum(c)/len(c),2), len(c)) for c in clusters if len(c)>=1]

        high_clusters = sorted(find_clusters(highs4h), key=lambda x: x[0], reverse=True)
        low_clusters  = sorted(find_clusters(lows4h),  key=lambda x: x[0], reverse=True)

        # 按价格分返上方/下方
        above = [(p, n) for p, n in high_clusters if p > price * 1.002][:4]
        below = [(p, n) for p, n in low_clusters  if p < price * 0.998][:4]

        lines.append("  《清算集群地图》")
        if above:
            lines.append("  上方(空头止损山):")
            for p, n in above:
                dist = (p - price) / price * 100
                density = '🔴密集' if n >= 3 else ('⚠️中等' if n == 2 else '')
                lines.append(f"    \${p:,.2f} (+{dist:.2f}%) 出现{n}次 {density}")
        if below:
            lines.append("  下方(多头止损池):")
            for p, n in below:
                dist = (price - p) / price * 100
                density = '🟢密集' if n >= 3 else ('⚠️中等' if n == 2 else '')
                lines.append(f"    \${p:,.2f} (-{dist:.2f}%) 出现{n}次 {density}")
    except Exception as e:
        lines.append(f"  清算集群: 计算失败 ({e})")

    # ── 5. 多档杠杆清算价位矩阵 [Bug2修复 2026-08-05] ─────────────────────
    # 修复前：只算5x（±20%，完全超出交易视野）
    # 修复后：10x/20x/50x/100x 全矩阵（覆盖±1%~±10%市场主流杠杆区间）
    try:
        lines.append("  《高杠杆清算价位矩阵（市场真实清算区间）》")
        lines.append("  上方空头清算（空头被扫→轧空）:")
        for lev in [100, 50, 20, 10]:
            liq = price * (1 + 0.95 / lev)
            dist = (liq - price) / price * 100
            bar = '▓' * max(1, 8 - lev // 15)
            lines.append(f"    {lev:>3}x 空头清算: \${liq:>10,.1f} (+{dist:.2f}%) {bar}")
        lines.append("  下方多头清算（多头被砸→洗盘）:")
        for lev in [100, 50, 20, 10]:
            liq = price * (1 - 0.95 / lev)
            dist = (price - liq) / price * 100
            bar = '▓' * max(1, 8 - lev // 15)
            lines.append(f"    {lev:>3}x 多头清算: \${liq:>10,.1f} (-{dist:.2f}%) {bar}")
    except Exception as e:
        lines.append(f"  杠杆清算矩阵: 计算失败 ({e})")

    lines.append("╬" + "═" * 58)
    return lines


def _build_tradfi_supplement(symbol: str, r: dict) -> list:
    """美股代币专属补充维度层（梵天35维之后注入，苏摩111封印）"""
    import urllib.request, json as _json
    lines = []
    fund = _get_rwa_fundamentals(symbol)
    price = r.get('price', 0)

    lines.append("")
    lines.append("╌╌ 美股代币专属层（TradFi补充） " + "╌" * 18)

    # 1. 基本面门控
    if fund:
        pe = fund.get('pe')
        h52w = float(fund.get('h52w') or 0)
        l52w = float(fund.get('l52w') or 0)
        mktcap = fund.get('mktcap_b', 0)
        pos_52w = round((price - l52w) / max(h52w - l52w, 1) * 100, 1) if h52w > l52w else 50
        pe_flag = ''
        if pe:
            pe_f = float(pe)
            pe_flag = ' ✅ 估值合理' if pe_f < 30 else (' ⚠️ 偏高' if pe_f < 60 else ' ❌ 远超合理')
        lines.append(f"  PE估值: {pe}x{pe_flag}  市值: ${mktcap}B")
        lines.append(f"  52W区间: ${l52w}~${h52w}  当前位置: {pos_52w}%")
        if pos_52w < 30:
            lines.append(f"  🔥 52W低位区域（<30%），历史价值投资区间")
        elif pos_52w > 75:
            lines.append(f"  ⚠️ 52W高位区域（>75%），机构出货压力区")

    # 2. Fib回调级别（20日高低自动计算）
    try:
        from brahma_brain.data_cache import get_klines
        kl1d = get_klines(symbol, '1d', 30)
        if kl1d and len(kl1d) >= 6:
            closes = [float(k[4]) for k in kl1d]
            highs  = [float(k[2]) for k in kl1d[-20:]] if len(kl1d) >= 20 else [float(k[2]) for k in kl1d]
            lows   = [float(k[3]) for k in kl1d[-20:]] if len(kl1d) >= 20 else [float(k[3]) for k in kl1d]
            h20, l20 = max(highs), min(lows)
            diff = h20 - l20
            fibs = [
                ('23.6%', round(h20 - diff * 0.236, 2)),
                ('38.2%', round(h20 - diff * 0.382, 2)),
                ('50.0%', round(h20 - diff * 0.500, 2)),
                ('61.8%', round(h20 - diff * 0.618, 2)),
                ('78.6%', round(h20 - diff * 0.786, 2)),
            ]
            fib_pos = f'100%支撑({l20})下方 ⚠️'
            for fname, fval in fibs:
                if price >= fval:
                    fib_pos = f"{fname}({fval})上方 ✅"
                    break
            chg5d  = round((closes[-1] - closes[-6])  / closes[-6]  * 100, 2) if len(closes) >= 6  else 0
            chg20d = round((closes[-1] - closes[-21]) / closes[-21] * 100, 2) if len(closes) >= 21 else 0
            lines.append(f"  Fib当前位置: {fib_pos}")
            lines.append(f"  20D区间: 高={h20}  低={l20}  5D{chg5d:+.2f}%  20D{chg20d:+.2f}%")
    except Exception:
        pass

    # 3. 盘口深度
    try:
        ob = _json.loads(urllib.request.urlopen(
            f'https://api.binance.com/api/v3/depth?symbol={symbol}&limit=5', timeout=5).read())
        bid_vol = sum(float(b[1]) for b in ob['bids'][:5])
        ask_vol = sum(float(a[1]) for a in ob['asks'][:5])
        ratio = round(bid_vol / max(ask_vol, 0.001), 2)
        ratio_flag = '✅ 买盘主导' if ratio > 1.5 else ('⚠️ 卖盘占优' if ratio < 0.8 else '中性')
        lines.append(f"  盘口买卖比: {ratio}x {ratio_flag}")
    except Exception:
        pass

    # 4. 宏观联动验证（SPXUSDT + XAUTUSDT）
    try:
        from brahma_brain.data_cache import get_ticker
        spx = get_ticker('SPXUSDT')
        xau = get_ticker('XAUTUSDT')
        spx_chg = float((spx or {}).get('priceChangePercent', 0))
        xau_chg = float((xau or {}).get('priceChangePercent', 0))
        macro_ok = spx_chg > 0
        xau_warn = f' 🟡 避险情绪上升' if xau_chg > 0.5 else ''
        lines.append(f"  宏观门控: SPX{spx_chg:+.2f}% {'✅ 宏观多头' if macro_ok else '❌ 宏观失速'}  XAUT{xau_chg:+.2f}%{xau_warn}")
    except Exception:
        pass

    # 5. 加密体制联动
    btc_regime = r.get('regime', 'UNKNOWN')
    btc_note = {
        'BULL_TREND':    '加密牛市共振，科技/加密概念股往往同强',
        'BEAR_TREND':    '加密熊市承压，美股反弹需更高确认门槛',
        'CHOP_MID':      '加密震荡体制，美股各自找升降逻辑',
        'BEAR_RECOVERY': '加密复苏早期，可在超卖美股适度配置',
    }.get(btc_regime, '')
    if btc_note:
        lines.append(f"  加密体制: {btc_regime} → {btc_note}")

    lines.append("╌" * 58)
    return lines


# ============================================================
# 双币并行入口
# ============================================================

def run_dual_analysis(symbols=None, direction='LONG'):
    """运行双币35维全量分析，输出完整报告"""
    if symbols is None:
        symbols = ['BTCUSDT', 'ETHUSDT']

    print("=" * 60)
    print("  🏛️ 梵天设计院 · 双币35维全量矩阵分析启动")
    print(f"  时间: {fmt_beijing()}")
    print("=" * 60)

    # [SIGSEGV修复 2026-08-15 苏摩111] 子进程隔离模式
    # 根因：多符号顺序执行内存累积超过RLIMIT_AS=1500MB → SIGSEGV(139)
    # 修复：每个symbol独立subprocess，内存隔离，不跨symbol累积
    import subprocess as _sp, sys as _sys, os as _os
    _script = _os.path.abspath(__file__)
    _base   = _os.path.dirname(_os.path.dirname(_script))
    # libgomp预加载（lightgbm/Kronos依赖）
    _gomp = _os.path.join(_base, 'venv/lib/python3.11/site-packages/torch/lib/libgomp.so.1')
    _env  = dict(_os.environ)
    if _os.path.exists(_gomp):
        _env['LD_PRELOAD'] = _gomp
    # [2026-08-18 苏摩封印] 限制OpenBLAS/OMP线程数，防止子进程并发时内存叠加OOM
    # 根因：每个subprocess默认启动N个BLAS线程，多标的并发时内存×N倍叠加崩溃
    _env['OPENBLAS_NUM_THREADS'] = '1'
    _env['OMP_NUM_THREADS']      = '1'
    _env['MKL_NUM_THREADS']      = '1'
    _env['NUMEXPR_NUM_THREADS']  = '1'
    _env['VECLIB_MAXIMUM_THREADS'] = '1'

    results = {}
    for sym in symbols:
        print(f"\n[{sym}] 分析中（子进程隔离）...", flush=True)
        try:
            proc = _sp.run(
                [_sys.executable, _script,
                 '--symbols', sym,
                 '--direction', direction,
                 '--_single'],
                capture_output=False,
                timeout=300,
                cwd=_base,
                env=_env,
            )
            results[sym] = f'exit={proc.returncode}'
            if proc.returncode not in (0, None):
                print(f"[{sym}] 子进程退出码={proc.returncode}")
        except _sp.TimeoutExpired:
            print(f"[{sym}] 超时(300s)")
        except Exception as _e:
            print(f"[{sym}] 子进程失败: {_e}")

    return results


if __name__ == '__main__':
    # cron独立运行时的内存门控
    try:
        from brahma_mem_manager import mem_gate as _mg, get_mem_mode, _available_mb as _avail_mb
        # [P2修复 2026-08-15 苏摩111] 降级门控：526MB不再被拦戈
        # full≥650MB / degraded 550~650MB / light 400~550MB / blocked<400MB
        _avail_now = _avail_mb()
        _mem_mode  = get_mem_mode(_avail_now)
        if _mem_mode == 'blocked':
            print(f'HEARTBEAT_OK — 内存危险({_avail_now:.0f}MB<400MB) [brahma_1hao_analysis跳过]')
            raise SystemExit(0)
        # degraded/light模式注入到全局，让run_analysis可读取
        import builtins as _bi
        _bi._BRAHMA_MEM_MODE = _mem_mode
        _bi._BRAHMA_MEM_AVAIL = _avail_now
        if _mem_mode != 'full':
            print(f'[内存降级] {_avail_now:.0f}MB → {_mem_mode}模式（跳过{"HCME/方仓" if _mem_mode=="degraded" else "Kronos/HCME/方仓"}）')
    except (ImportError, SystemExit) as _mge:
        if isinstance(_mge, SystemExit): raise
    import argparse
    parser = argparse.ArgumentParser(description='梵天1号工程 · 35维全量矩阵分析')
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT'])
    parser.add_argument('--direction', default='LONG', choices=['LONG', 'SHORT'])
    parser.add_argument('--_single', action='store_true', help='单符号直接执行模式（子进程调用，不递归）')
    parser.add_argument('--mode', default='standard', choices=['standard', 'altcoin'],
                        help='altcoin模式: 跳过HCME层，32维分析山寨币')
    args = parser.parse_args()
    # altcoin模式自动扩展候选池
    if args.mode == 'altcoin' and args.symbols == ['BTCUSDT', 'ETHUSDT']:
        args.symbols = ['XRPUSDT','DOGEUSDT','ADAUSDT','LINKUSDT',
                        'CHZUSDT','OPUSDT','ARBUSDT','UNIUSDT','ZECUSDT']
    # [SIGSEGV修复 2026-08-15] --_single标志：单符号直接执行，不走子进程路径
    # 子进程调用时传入此标志，防止无限递归
    if getattr(args, '_single', False) or len(args.symbols) == 1:
        # 单符号直接运行（子进程模式或单符号调用）
        sym = args.symbols[0]
        print(f'\n[{sym}] 分析中...', flush=True)
        result = run_analysis(sym, args.direction)
        print(result)

        # ── MTF全周期FVG/OB地图（2026-08-20 苏摩指令封印）─────────────────────
        try:
            from brahma_multiframe import scan as _mtf_scan
            _mtf = _mtf_scan(sym, direction=args.direction)
            print(_mtf.get('mtf_summary', ''))
            # MTF偏向注入到后续分析上下文
            _mtf_bias = _mtf.get('mtf_bias', 'NEUTRAL')
            _mtf_adj  = _mtf.get('mtf_score_adj', 0)
        except Exception as _mtf_e:
            _mtf_bias, _mtf_adj = 'NEUTRAL', 0
            print(f'[MTF] 全周期扫描跳过: {_mtf_e}')
        # ── end MTF ──────────────────────────────────────────────────────────────

        # ── P2 三线策略 + P3 连续记忆（2026-08-18 太极封印）──────────────────
        try:
            # brahma_brain已在模块顶部加入sys.path，无需重复insert（P5 race条件修复 2026-08-18）
            from brahma_intel_layer import (
                identify_pattern, generate_three_line_strategy,
                record_analysis, summarize_intent
            )
            # 获取分析数据
            _rd = analyze(sym, signal_dir=args.direction, deep=False) or {}
            _p2 = _rd.get('params', {}) or {}
            # [BUG修复 2026-08-19 设计院封印] OI需从brahma_bus直接获取，analyze()不返回oi_change字段
            # 根因：全天intraday_memory记录oi_1h=0，pattern永远「方向待定」
            try:
                from brahma_bus import bus as _brahma_bus_inst
                _oi_hist = _brahma_bus_inst.oi_history(sym, period='1h', limit=9) or []
                if len(_oi_hist) >= 2:
                    _ov = [float(x.get('sumOpenInterest', 0)) for x in _oi_hist]
                    _oi1h = (_ov[-1] - _ov[-2]) / _ov[-2] * 100 if _ov[-2] else 0.0
                    _oi8h = (_ov[-1] - _ov[0])  / _ov[0]  * 100 if _ov[0] else 0.0
                else:
                    _oi1h, _oi8h = 0.0, 0.0
            except Exception:
                _oi1h = float(_rd.get('oi_change_1h', 0) or 0)
                _oi8h = float(_rd.get('oi_change_8h', 0) or 0)
            # [BUG修复 2026-08-19 设计院封印] hcme_wr不在analyze()顶层，在fangcang子dict里
            # 根因：全天intraday_memory记录hcme_wr=50%（默认值），智慧层pattern永远「方向待定」
            _fc = _rd.get('fangcang', {}) or {}
            _hcme_raw = _fc.get('hcme_context', '') or ''
            _hcme_wr_extracted = 50.0
            if 'WR=' in _hcme_raw:
                try:
                    import re as _re
                    _m = _re.search(r'WR=(\d+)%', _hcme_raw)
                    if _m: _hcme_wr_extracted = float(_m.group(1))
                except: pass
            # 也尝试从top-level hcme_adj反推（adj>0→WR偏高，adj<0→WR偏低）
            _hcme_adj_val = float(_rd.get('hcme_adj', 0) or 0)
            _hcme_wr  = _hcme_wr_extracted  # 修复后使用真实WR而非默认50
            _l2       = float(_rd.get('l2_ratio', 1) or 1)
            _long_pct = float(_rd.get('long_pct', 50) or 50)
            _pd_zone  = str(_rd.get('pd_zone', 'NEUTRAL') or 'NEUTRAL')
            _bos      = str(_rd.get('bos_type', '') or '')
            _regime   = str(_rd.get('regime', '') or '')
            _price    = float(_rd.get('price', 0) or 0)
            _ema_pos  = 'ABOVE' if _rd.get('price_above_ema20_1h') else 'BELOW'
            _grade    = float(_rd.get('grade_num', 0) or 0)
            _ob_lo    = float(_rd.get('bull_ob_lo', _p2.get('entry_lo', 0)) or 0)
            _ob_hi    = float(_rd.get('bull_ob_hi', _p2.get('entry_hi', 0)) or 0)
            _bOB_lo   = float(_rd.get('bear_ob_lo', 0) or 0)
            _bOB_hi   = float(_rd.get('bear_ob_hi', 0) or 0)
            _fvg      = float(_rd.get('fvg_target', 0) or 0)
            _l100u    = float(_rd.get('liq_100x_short', 0) or 0)
            _l50u     = float(_rd.get('liq_50x_short', 0) or 0)
            _l20u     = float(_rd.get('liq_20x_short', 0) or 0)
            _l100d    = float(_rd.get('liq_100x_long', 0) or 0)
            _lstop    = float(_rd.get('liq_stop_pool', 0) or 0)
            _decision = str(_rd.get('action', 'SKIP') or 'SKIP')

            # 获取上次HCME WR用于突变检测
            _timeline = record_analysis.__module__ and []
            from brahma_intel_layer import get_today_timeline
            _prev_timeline = get_today_timeline(sym)
            _hcme_prev = _prev_timeline[-1]['hcme_wr'] if _prev_timeline else _hcme_wr

            # 情境识别
            _pattern = identify_pattern(
                oi_1h=_oi1h, oi_8h=_oi8h,
                hcme_wr=_hcme_wr, hcme_wr_prev=_hcme_prev,
                l2_ratio=_l2, long_pct=_long_pct,
                pd_zone=_pd_zone, bos_type=_bos,
                regime=_regime, price_vs_ema=_ema_pos,
            )

            # P3 记录今日轨迹
            record_analysis(sym, _price, _hcme_wr, _oi1h,
                            _pattern['pattern'], _decision)

            # 主力意图总结
            _intent_summary = summarize_intent(sym)

            # P2 三线策略生成
            if _ob_lo > 0 and _l20u > 0:
                _three_lines = generate_three_line_strategy(
                    symbol=sym, direction=args.direction,
                    price=_price, pattern=_pattern,
                    bull_ob_lo=_ob_lo, bull_ob_hi=_ob_hi,
                    bear_ob_lo=_bOB_lo, bear_ob_hi=_bOB_hi,
                    fvg_target=_fvg, liq_100x_up=_l100u,
                    liq_50x_up=_l50u, liq_20x_up=_l20u,
                    liq_100x_dn=_l100d, liq_stop_pool=_lstop,
                    hcme_wr=_hcme_wr, grade=_grade, regime=_regime,
                )
                print(_three_lines)

            # 主力意图追踪输出
            print(f'\n📡 主力意图追踪: {_intent_summary}')

        except Exception as _p2e:
            pass  # 智慧层不阻断主流程
        # ── end P2/P3 ─────────────────────────────────────────────────────────

        # 写入信号池
        try:
            r_raw = analyze(sym, signal_dir=args.direction, deep=True)
            _p = r_raw.get('params', {}) or {}
            for _k in ['entry_lo','entry_hi','sl','tp1','tp2','rr','rr1','sl_pct','stop_loss']:
                if not r_raw.get(_k) and _p.get(_k):
                    r_raw[_k] = _p[_k]
            score = r_raw.get('score_final', r_raw.get('score', 0))
            grade = r_raw.get('grade', 0)
            # [设计院封印 2026-08-20 苏摩指令] 屏蔻score120~139区间（实测WR=0%，63条信号全部EXPIRED）
            _regime_raw = r_raw.get('regime', '')
            if 120 <= float(score or 0) <= 139 and 'BULL_TREND' in str(_regime_raw):
                print(f'[1hao→信号池] {sym} score={score:.0f} 在120~139毒区间，丢弃(实测WR=0%) ⛔')
            # [Phase2 2026-08-21 苏摩111] EV驱动动态阈值计算
            # 替代静态138门槛，营星赶势行情时自动降低阈值
            _rsi_4h_ev   = float(r_raw.get('rsi_4h', r_raw.get('rsi_1h', 50)) or 50)
            _regime_str  = str(_regime_raw)
            _momentum_ev = r_raw.get('momentum_level', '')
            # 计算动态阈值
            _base_thresh = 138  # 默认
            if _momentum_ev == 'MOMENTUM_STRONG' and _rsi_4h_ev <= 90:
                _base_thresh = int(_base_thresh * 0.80)   # +20%行情键入降至110
            elif _momentum_ev == 'MOMENTUM_BULL' and _rsi_4h_ev <= 85:
                _base_thresh = int(_base_thresh * 0.88)   # +12%降至121
            elif 'BULL_TREND' in _regime_str and _rsi_4h_ev <= 75:
                _base_thresh = int(_base_thresh * 0.92)   # 简单BULL_TREND小增强
            if _rsi_4h_ev > 90:
                _base_thresh = int(_base_thresh * 1.15)   # 极度追高防护
            _dyn_label = f'dyn_thresh={_base_thresh}(rsi4h={_rsi_4h_ev:.0f} mom={_momentum_ev})'
            _score_ok = float(score or 0) >= _base_thresh and float(grade or 0) >= 80
            if _score_ok:
                from brahma_brain.dharma_data_bridge import log_signal
                r_raw['symbol'] = sym
                r_raw['direction'] = args.direction
                r_raw['source'] = 'brahma_1hao_auto'
                # [2026-08-18 苏摩封印] 修复valid字段：result['valid']=False但params['valid']=True
                # 根因：brahma_engine对result和params的valid赋值不一致
                # 修复：优先使用params.valid（SL/score通过后设为True的权威来源）
                _p_valid = bool(_p.get('valid') or r_raw.get('valid_signal'))
                if _p_valid and not r_raw.get('valid'):
                    r_raw['valid'] = True
                wrote = log_signal(r_raw)
                if wrote:
                    print(f'[1hao→信号池] {sym} score={score:.0f} grade={grade:.0f} 已写入 ✅ [{_dyn_label}]')
                else:
                    print(f'[1hao→信号池] {sym} 去重拦截')
            else:
                print(f'[1hao→信号池] {sym} score={score:.0f} grade={grade:.0f} < 阈值[{_dyn_label}]，不写入')

            # ── P0 强信号人工入场窗口推送（2026-08-18 太极封印）────────────────
            # 规则：HCME WR≥80% + grade≥80 + 系统因SL过宽/其他原因无法自动执行
            # → 立即推送苏摩人工入场窗口通知，不让最佳窗口无声错失
            try:
                _hcme_wr   = float(r_raw.get('hcme_wr', 0) or 0)
                _grade_num = float(r_raw.get('grade_num', grade) or 0)
                _action    = str(r_raw.get('action', '')).upper()
                _sl_pct    = float(r_raw.get('sl_pct', 99) or 99)
                _price     = float(r_raw.get('price', 0) or 0)
                _entry_lo  = float(r_raw.get('entry_lo', 0) or 0)
                _entry_hi  = float(r_raw.get('entry_hi', 0) or 0)
                _tp1       = float(r_raw.get('tp1', 0) or 0)
                _tp2       = float(r_raw.get('tp2', 0) or 0)
                _sl_price  = float(r_raw.get('stop_loss', 0) or 0)
                _regime    = str(r_raw.get('regime', '') or '')
                # 触发条件：HCME WR≥80% + grade≥80 + ENTER意图 + SL过宽被拦截
                _is_strong = (_hcme_wr >= 80 and _grade_num >= 80 and _action == 'ENTER')
                _sl_blocked = (_sl_pct > 5.0)  # SL过宽是主要拦截原因
                _score_blocked = (float(score or 0) < 138)  # 评分不足
                if _is_strong and (_sl_blocked or _score_blocked):
                    from scripts.push_hub import _jarvis
                    _block_reason = f'SL={_sl_pct:.1f}%过宽' if _sl_blocked else f'score={score:.0f}<138'
                    _msg = (
                        f'🎯 **人工入场窗口！** {sym}\n'
                        f'梵天强信号：HCME WR={_hcme_wr:.0f}% | grade={_grade_num:.0f} | 体制={_regime}\n'
                        f'系统拦截原因：{_block_reason}，无法自动执行\n'
                        f'━━━━━━━━━━━━━━━━━━━━\n'
                        f'当前价：${_price:.2f}\n'
                        f'入场区：${_entry_lo:.2f}~${_entry_hi:.2f}\n'
                        f'止损：  ${_sl_price:.2f}（{_sl_pct:.1f}%）\n'
                        f'TP1：   ${_tp1:.2f} | TP2：${_tp2:.2f}\n'
                        f'建议苏摩手动评估是否入场'
                    )
                    _jarvis(_msg, dedup_key=f'human_window_{sym}_{int(_price)}', dedup_ttl=1800)
                    print(f'[P0→人工窗口] {sym} 已推送苏摩 HCME={_hcme_wr:.0f}% grade={_grade_num:.0f}')
            except Exception as _p0e:
                print(f'[P0→人工窗口] 推送异常: {_p0e}')
            # ── end P0 ────────────────────────────────────────────────────────
        except Exception as _e:
            print(f'[1hao→信号池] 写入失败: {_e}')
    else:
        # 多符号：走子进程隔离路径
        run_dual_analysis(args.symbols, args.direction)
