"""
brahma_core_replay.py
[N_REPLAY 2026-08-29] 40年经验复盘升级——四修正
铁证: 20392条案例 + 2001笔回测(IS/OOS偏差3%)

从 brahma_core.py confluence_score() 中提取。
接入位置：Block C之后、regime_mult之后、factors之前。
接口：calc_replay(ms, signal_dir, score, breakdown, _result) -> (score, breakdown)
"""
import sys
from typing import Any


def calc_replay(ms: dict, signal_dir: str, score: int, breakdown: dict, _result: dict) -> tuple[int, dict]:
    """
    40年经验复盘升级——四修正
    只修改 score 和 breakdown，不引入新变量到外层
    """
    # ══ [N_REPLAY 2026-08-29 苏摩111] 40年经验复盘升级——四修正 ══════════════
    # 铁证: 20392条案例 + 2001笔回测(IS/OOS偏差3%)
    try:
        _replay_rsi  = float(ms.get('rsi_1h', ms.get('rsi', 50)) or 50)
        # vol×burst: 从ms多路读取，兼容不同数据结构
        _replay_vol  = float(
            ms.get('vol_ratio') or
            (ms.get('volume') or {}).get('vol_ratio') or
            (ms.get('momentum') or {}).get('vol_ratio') or 1.0
        )
        _replay_brst = float(
            ms.get('burst_atr_mult') or
            (ms.get('momentum') or {}).get('burst_atr_mult') or
            (_result.get('fangcang') or {}).get('avg_burst_atr_mult') or 0
        )
        # compress_bars: 从fangcang结果或ms读取
        _replay_bars = int(
            ms.get('compress_bars') or
            ms.get('fangcang_bars') or
            (_result.get('fangcang') or {}).get('avg_squeeze_bars') or 0
        )
        _replay_dir  = signal_dir or _result.get('signal_dir','LONG') or 'LONG'

        # P0: squeeze_bars_w修正 [15-30根最优,60+降权]
        # 铁证: 15-30根 avg_burst=1.95x最高 / 60+根=1.76x能量分散
        if _replay_bars > 0:
            if 15 <= _replay_bars < 30:
                score = min(175, int(score) + 2)
                breakdown['P0_压缩最优窗口'] = f'+2(压缩{_replay_bars}根=15-30根黄金区)'
            elif _replay_bars >= 60:
                score = max(0, int(score) - 2)
                breakdown['P0_长压缩泡压弱'] = f'-2(压缩{_replay_bars}根>=60,能量分散)'

        # P1: vol×burst组合维度 [量能单用无效]
        # 铁证: vol单用WR差异<1% / vol×ATR>3x才是机构出手信号
        if _replay_vol > 0 and _replay_brst > 0:
            _vb_combo = _replay_vol * _replay_brst
            if _vb_combo >= 3.0 and _replay_brst >= 1.5:
                score = min(175, int(score) + 4)
                breakdown['P1_量价齐升'] = f'+4(vol={_replay_vol:.1f}x×burst={_replay_brst:.1f}x={_vb_combo:.1f}≥3.0)'
            elif _vb_combo >= 1.5 and _replay_brst >= 1.0:
                score = min(175, int(score) + 2)
                breakdown['P1_量价有效'] = f'+2(vol×burst={_vb_combo:.1f}≥1.5)'

        # P2: RSI中性区45-55加分 [机构建仓最优区]
        # 铁证: RSI45-55 做多/做空WR=77-78%(最强) n=1202/1277
        if 45 <= _replay_rsi < 55 and 'BULL_TREND' not in _regime_upper:
            score = min(175, int(score) + 4)
            breakdown['P2_RSI中性机构入场'] = f'+4(RSI={_replay_rsi:.0f}在中性区 WR=77%铁证)'

        # P3: BEAR_TREND SHORT RSI精细管控
        # 铁证RSI<45: WR=35%(n=298陷阱) / RSI55-70: WR=49%(最佳做空区)
        if 'BEAR_TREND' in _regime_upper and _replay_dir == 'SHORT':
            if _replay_rsi < 45:
                score = max(0, int(score) - 4)
                breakdown['P3_BEAR_SHORT_RSI陷阱'] = f'-4(BEAR SHORT RSI={_replay_rsi:.0f}<45 WR=35%铁证)'
            elif 55 <= _replay_rsi < 70:
                score = min(175, int(score) + 3)
                breakdown['P3_BEAR_SHORT_RSI最佳'] = f'+3(BEAR SHORT RSI={_replay_rsi:.0f}=55-70最佳做空区)'

    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    # FIX-1: 极低波动率假牛市惩罚（精确版v2）
    _atr_pct_val = float(ms.get('atr_pct', ms.get('atr_1h', 15) / max(ms.get('price', 1), 1)) if ms else 0.01)
    # [潜力释放 P1 2026-07-12] 暴涨猎手豆免通道：FR极度负值 + ATR压缩 = 爆发前元，不应惩罚
    _fr_val = float(ms.get('sentiment', {}).get('funding_rate', 0) if ms else 0)
    _pump_hunter_exempt = (
        _atr_pct_val < 0.005 and          # ATR压缩条件
        _fr_val < -0.0001 and             # FR负值（空头付费）
        signal_dir == 'LONG'              # 做多方向
    )
    if ('BULL_TREND' in _regime_upper and signal_dir == 'LONG'
            and _atr_pct_val < 0.005 and not _direction_block and score > 0):
        if _pump_hunter_exempt:
            breakdown['FIX1_假牛市'] = f'豆免(暴涨猎手) ATR={_atr_pct_val:.4f} FR={_fr_val:.4f}负值压缩=爆发前元'
        else:
            score = int(score * 0.88)
            breakdown['FIX1_假牛市'] = f'×0.88 (ATR_pct={_atr_pct_val:.4f} 极低波动假趋势)'

    # FIX-2: CHOP超卖<25做空惩罚（精确版v2）
    _rsi_chop = float(ms.get('rsi_1h', 50) if ms else 50)
    if ('CHOP' in _regime_upper and not _is_long_signal
            and _rsi_chop < 25 and not _direction_block and score > 0):
        score = int(score * 0.88)
        breakdown['FIX2_CHOP追空'] = f'×0.88 (CHOP RSI={_rsi_chop:.0f}<25 超卖追空)'


    # [UP-NODE-v4] 梵天大脑v4注入
    _atr_v4 = float(ms.get('atr_pct', ms.get('atr_1h', 15) / max(ms.get('price', 1), 1)) if ms else 0.01)
    _rsi_v4 = float(ms.get('rsi_1h', 50) if ms else 50)
    _is_long_v4 = (signal_dir == 'LONG')
    # [潜力释放 P1 2026-07-12] N16暴涨猎手豆免：与FIX1共用同一豆免标记
    _n16_pump_exempt = _pump_hunter_exempt  # 继承FIX1的判断结果

    # [达摩院v2.0 ATR体制过滤器] N16完整版 — 基于 N16_atr_layers 铁证
    # CHOP 0.005~0.015最优(PF=1.44~1.98) | BULL_TREND(牛市趋势) <0.010禁区(PF=0.567)
    _atr_regime_tag = ''
    if 'BULL_TREND' in _regime_upper:
        # BULL_TREND(牛市趋势) ATR禁区：<0.010 PF=0.567（铁证）
        if _atr_v4 < 0.010 and not _direction_block and score > 0:
            if _n16_pump_exempt:
                _atr_regime_tag = f'N16_豁免(暴涨猎手) ATR={_atr_v4:.4f} FR负值压缩=爆发前元'
            else:
                score = int(score * 0.80)
                _atr_regime_tag = f'N16_ATR禁区 ×0.80 (BULL ATR={_atr_v4:.4f}<0.010, PF=0.567)'
        # BULL_TREND(牛市趋势) ATR黄金区：0.010~0.015
        elif 0.010 <= _atr_v4 <= 0.015 and _is_long_v4 and not _direction_block and score > 0:
            score = min(int(score * 1.05), 175)
            _atr_regime_tag = f'N16_ATR黄金 ×1.05 (BULL ATR={_atr_v4:.4f} PF=1.087)'
    elif 'CHOP' in _regime_upper:
        # CHOP最优区：0.005~0.015 PF=1.44~1.98
        if 0.005 <= _atr_v4 <= 0.015 and not _direction_block and score > 0:
            bonus = int((1.98 - max(0, (_atr_v4 - 0.005) / 0.010)) * 2)  # 动态加分
            score = min(score + bonus, 175)
            _atr_regime_tag = f'N16_CHOP优区 +{bonus} (ATR={_atr_v4:.4f} PF≈1.5+)'
        # CHOP大ATR区：>0.015 PF=1.013接近无效
        elif _atr_v4 > 0.020 and not _direction_block and score > 0:
            score = int(score * 0.90)
            _atr_regime_tag = f'N16_CHOP大ATR ×0.90 (ATR={_atr_v4:.4f}>0.020)'
    elif 'BEAR' in _regime_upper:
        # BEAR体制 ATR有效区：0.007~0.025
        if _atr_v4 < 0.007 and not _direction_block and score > 0:
            score = int(score * 0.88)
            _atr_regime_tag = f'N16_BEAR低ATR ×0.88 (ATR={_atr_v4:.4f}<0.007)'
    if _atr_regime_tag:
        breakdown['N16_ATR体制'] = _atr_regime_tag

    # N14: 体制切换时机强化 v2 [设计院P0b封印 2026-06-27]
    # 达摩院铁证：5~10min黄金窗口 PF=1.625，15~25min死亡窗口 PF=0.81
    try:
        import json as _j14, time as _t14
        _dm14 = _j14.loads(open('data/dharma_runtime.json').read())
        _rt14 = _dm14.get('regime_timing', {})
        _rss14_path = __import__('pathlib').Path('data/regime_switch_state.json')
        _n14_delta = 0
        _n14_label = ''
        if _rss14_path.exists():
            _rss14 = _j14.loads(_rss14_path.read_text())
            _last_switch = _rss14.get('last_switch_ts', 0)
            _cur_regime14 = _rss14.get('current_regime', '')
            _dist_min = (_t14.time() - _last_switch) / 60 if _last_switch else 9999
            # 匹配达摩院时段矩阵
            for _window, _wdata in _rt14.items():
                if '~' not in str(_window): continue
                try:
                    _wlo, _whi = [float(x) for x in str(_window).split('~')]
                    if _wlo <= _dist_min < _whi:
                        _n14_delta = int(_wdata.get('delta', 0))
                        _n14_label = _wdata.get('label', '')
                        break
                except Exception as _e:
                        if not isinstance(_e, (TimeoutError, ModuleNotFoundError, ImportError, AttributeError)):
                            pass  # [静默] f'[WARN][brahma_core] {type(_e).__name__}: {str(_e)[:60]}'
        # 当无regime_switch_state时，保d原 N14逻辑
        elif 'BEAR_TREND' in _regime_upper and not _is_long_v4 and _rsi_v4 < 42:
            _n14_delta = 5
            _n14_label = '熊市边界早鸟(fallback)'
        if _n14_delta != 0:
            score = max(0, min(int(score) + _n14_delta, 175))
            breakdown['N14_体制切换时机'] = f'{_n14_delta:+d} ({_n14_label} dist={_dist_min if "_dist_min" in dir() else "?":.0f}min PF={_rt14.get(str(int(_dist_min))+"~"+str(int(_dist_min)+5),{}).get("pf","?")})'
            pass  # [静默] f'[N14-Timing] {_sym}: {_n14_delta:+d}分 {_n14_label}'
    except Exception:
        # 安全回退：保留原 N14逻辑
        if 'BEAR_TREND' in _regime_upper and not _is_long_v4 and _rsi_v4 < 42 and _atr_v4 > 0.012 and not _direction_block and score > 0:
            score = min(int(score) + 5, 175)
            breakdown['N14_熊转边界'] = '+5 (熊市边界早鸟 PF=1.625)'

    # [达摩院v2.0 N15评分分层仓位映射] — 基于 N15_kelly 铁证
    # 150~160分: PF=1.538 Calmar=5.16（最优） | 130~140: PF=1.02（噪声）
    _score_tier_tag = ''
    if score >= 165:
        _kelly_tier = 'S+';  _pos_tier = 0.08  # 极高分：最大仓位
        _score_tier_tag = f'N15_S+层({score}分) pos={_pos_tier:.0%}'
    elif score >= 158:
        _kelly_tier = 'S';   _pos_tier = 0.065  # S1标准仓
        _score_tier_tag = f'N15_S层({score}分) pos={_pos_tier:.0%}'
    elif score >= 150:
        _kelly_tier = 'S2';  _pos_tier = 0.05   # [武曲OOS✅] S2层实盘 WR=66.7% PF=3.575 n=72（实盘运行样本，非离线训练样本，待积累至n≥500增强可信度）
        _score_tier_tag = f'N15_S2层({score}分) pos={_pos_tier:.0%} [武曲认证]'
    elif score >= 130:
        _kelly_tier = 'B';   _pos_tier = 0.02   # 极轻仓观察
        _score_tier_tag = f'N15_B层({score}分) pos={_pos_tier:.0%}'
    else:
        _kelly_tier = 'C';   _pos_tier = 0.0
    # 将仓位分级注入 extra_data 供执行层使用
    if extra_data is not None and isinstance(extra_data, dict):
        extra_data['score_tier'] = _kelly_tier
        extra_data['score_pos']  = _pos_tier
    breakdown['N15_分层仓位'] = _score_tier_tag if _score_tier_tag else f'N15_C层({score}分) 不执行'

    # ── [GAP2 仓位管理器 2026-06-03] 中仓解锁 + 动态仓位 ─────────────────────
    # 武曲Paper 200笔+WR≥75% → 倍数1.5x | 3连胜 → 倍数2.0x
    try:
        import sys as _pm_sys, os as _pm_os
        _pm_root = _pm_os.path.dirname(_pm_os.path.dirname(_pm_os.path.abspath(__file__)))
        if _pm_root not in _pm_sys.path:
            if _pm_root not in _pm_sys.path: _pm_sys.path.insert(0, _pm_root)
        from scripts.position_manager import get_position_multiplier as _get_pm
        _pm_mult = _get_pm()
        if _pm_mult > 1.0 and _pos_tier > 0:
            _pos_tier_adjusted = round(_pos_tier * _pm_mult, 4)
            if extra_data is not None and isinstance(extra_data, dict):
                extra_data['score_pos']   = _pos_tier_adjusted
                extra_data['pos_mult']    = _pm_mult
            breakdown['N15_仓位倍数'] = (
                f'×{_pm_mult} → pos={_pos_tier_adjusted:.1%} '
                f'({"中仓已解锁" if _pm_mult==1.5 else "连胜加仓"})'
            )
    except Exception as _pm_e:
        print(f"[WARN] brahma_core_replay: _pm_e", file=sys.stderr)

    # [达摩院v2.0 M09] 品种×维度权重修正层
    # 来源：full_universe_backtest dim_contrib铁证
    # BTC谐波-0.381/宏观-0.256清零 | ETH背离+0.277→×2.0 | SOL期权-0.093→×0.5
    # 已通过DharmaBus总线写入，此处读取并追溯调整评分
    # 设计院升级 2026-06-27: 无score下限限制，所有体制均触发
    try:
        import os as _os2, sys as _sys2
        _bus_dir2 = _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)), '..')
        if _bus_dir2 not in _sys2.path: _sys2.path.insert(0, _bus_dir2)
        from dharma.dharma_bus import get_dim_weight as _get_dw
        _m09_dims = {
            '关键位精确度': breakdown.get('关键位精确度', 0),
            '形态成熟度':   breakdown.get('形态成熟度',   0),
            '清算/OI':      breakdown.get('清算/OI',      0),
            '谐波+多周期':  breakdown.get('谐波+多周期',  0),
            'L2+贝叶斯+宏观': breakdown.get('L2+贝叶斯+宏观', 0),
            '量能验证':     breakdown.get('量能验证',     0),
            '动量背离':     breakdown.get('动量背离',     0),
            '期权+订单流':  breakdown.get('期权+订单流',  0),
            'LSTM+NLP情绪': breakdown.get('LSTM+NLP情绪', 0),
        }
        _m09_delta = 0
        _m09_log = []
        for _dim, _orig in _m09_dims.items():
            if _orig <= 0: continue
            _w = _get_dw(_sym, _dim)
            if _w == 1.0: continue
            _adjusted = round(_orig * _w)
            _delta = _adjusted - _orig
            _m09_delta += _delta
            if abs(_delta) >= 1:
                _m09_log.append(f'{_dim}:{_orig}→{_adjusted}(×{_w})')
                # 同步更新breakdown实际分数字段
                breakdown[_dim] = _adjusted
        if _m09_delta != 0:
            score = max(0, min(score + _m09_delta, 175))
            breakdown['M09_维度权重'] = f'Δ{_m09_delta:+d}分 [{" | ".join(_m09_log[:4])}]'
            pass  # [静默] f'[M09-DimWeight] {_sym}: {_m09_delta:+d}分 | {" | ".join(_m09_log)}'
    except Exception as _e09:
        print(f"[WARN] brahma_core_replay: _e09", file=sys.stderr)

    # ─── [设计院 2026-06-30 P1-C] WICK_HUNTER 第10因子 ──────────────────────
    # 根因：系统缺乏15m插针信号识别，58,850/58,888极端下影线未被捕捉
    # 铁证：22:45 L:58888 体/影比=0.12（教科书级插针），02:00 L:58850 振幅644
    # 逻辑：下影线主导（>实体+上影线×1.5）+ 触碰近期低点支撑 + 收盘收复 → +20分
    # fail-safe：异常静默，不阻断主流程
    try:
        _k15m = extra_data.get('_klines_15m') if extra_data else None
        if _k15m and len(_k15m.get('c', [])) >= 5:
            _wh_o = _k15m['o'][-1]
            _wh_h = _k15m['h'][-1]
            _wh_l = _k15m['l'][-1]
            _wh_c = _k15m['c'][-1]
            _wh_body  = abs(_wh_c - _wh_o)
            _wh_upper = _wh_h - max(_wh_o, _wh_c)
            _wh_lower = min(_wh_o, _wh_c) - _wh_l
            _wh_total = _wh_h - _wh_l
            _wh_score = 0
            if _wh_total > 0:
                if signal_dir == 'LONG':
                    # 条件1：下影线主导（>实体+上影线的1.5倍）
                    if _wh_lower > (_wh_body + _wh_upper) * 1.5:
                        _support_ref = min(_k15m['l'][-20:]) if len(_k15m['l']) >= 20 else _wh_l
                        # 条件2：触碰近期支撑（±0.3%）
                        if _wh_l <= _support_ref * 1.003:
                            # 条件3：收盘收复支撑上方
                            if _wh_c > _support_ref * 1.004:
                                # [达摩院验证 2026-06-30] LONG插针需额外满足:
                                # 体/影比<0.25(防假插针) + DISCOUNT区(系数由区间路由提供)
                                _in_discount = breakdown.get('区间Zone_v2', '').startswith('DISCOUNT')
                                _extreme_wick = (_wh_body / _wh_total < 0.25)
                                if _in_discount and _extreme_wick:
                                    _wh_score = 25 if (_wh_body / _wh_total < 0.15) else 20
                                    breakdown['WICK_HUNTER_LONG'] = f'+{_wh_score}(下影{_wh_lower:.0f}pts 体影比{_wh_body/_wh_total:.2f} DISCOUNT区联合)'
                                elif _extreme_wick:
                                    # 非DISCOUNT区但是极端插针，小加分
                                    _wh_score = 10
                                    breakdown['WICK_HUNTER_LONG_WEAK'] = f'+{_wh_score}(下影 体影比{_wh_body/_wh_total:.2f} 非DISCOUNT小加分)'
                elif signal_dir == 'SHORT':
                    # 条件1：上影线主导（>实体+下影线的2.0倍）
                    if _wh_upper > (_wh_body + _wh_lower) * 2.0:
                        _resist_ref = max(_k15m['h'][-20:]) if len(_k15m['h']) >= 20 else _wh_h
                        # 条件2：触碰近期阻力（±0.3%）
                        if _wh_h >= _resist_ref * 0.997:
                            # 条件3：收盘回落阻力下方
                            if _wh_c < _resist_ref * 0.996:
                                _wh_score = 15
                                breakdown['WICK_HUNTER_SHORT'] = f'+{_wh_score}(上影{_wh_upper:.0f}pts 体影比{_wh_body/_wh_total:.2f})'
            if _wh_score > 0:
                score += _wh_score
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    return score, breakdown
