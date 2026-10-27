#!/usr/bin/env python3
"""
step11_mandatory_judge.py — 梵天11步强制决策裁判（SSOT）
[设计院封印 2026-10-01 苏摩111]

40年实战视角的核心设计原则：
  1. 无方向 = 直接输出WAIT，不猜测
  2. 矛盾维度 = 强制标记，降权不穿透
  3. 七维共振 < 3 = 硬WAIT（数据说了算）
  4. 体制封禁 = 零容忍，一票否决
  5. EV ≤ 0 = 不入场（系统说了算）

接入位置：scripts/brahma_manual_analysis.py run_analysis() 末尾
         替代原 step10_vip 的分散门控逻辑

输出格式（强制唯一）：
  {
    'verdict':  'ENTER' | 'WATCH' | 'WAIT',
    'direction': 'LONG' | 'SHORT' | 'NONE',
    'reason':   [str, ...],         # 决策链，逐条说明
    'blocked_by': str | None,       # 哪一闸阻断
    'vip_card': str,                # 最终输出卡片
    'confidence': 'HIGH'|'MED'|'LOW',
    'entry_lo': float,
    'entry_hi': float,
    'sl': float,
    'tp1': float,
    'tp2': float,
    'rr': float,
  }
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import Any

# [设计院封印 2026-10-01] 常量SSOT，消灭魔法数字
try:
    from analysis_constants import (
        ATR_SL_MIN_MULT, RR_MIN, ALIGN_MIN_GATE4,
        SCORE_CHOP_STD, SCORE_CHOP_TREND, SCORE_CHOP_TRANS,
        SCORE_BEAR_LONG, SCORE_BEAR_REC_SHT,
        ALIGN_BEAR_LONG, ALIGN_BEAR_REC_SHT,
        FC_SIM_THRESHOLD, HURST_TREND, HURST_TRANSITION,
    )
except ImportError:
    # 兜底：保持原来的内联值
    ATR_SL_MIN_MULT=1.5; RR_MIN=1.5; ALIGN_MIN_GATE4=3
    SCORE_CHOP_STD=110; SCORE_CHOP_TREND=85; SCORE_CHOP_TRANS=95
    SCORE_BEAR_LONG=140; SCORE_BEAR_REC_SHT=130
    ALIGN_BEAR_LONG=5; ALIGN_BEAR_REC_SHT=4
    FC_SIM_THRESHOLD=0.25; HURST_TREND=0.6; HURST_TRANSITION=0.55

BASE = Path(__file__).parent.parent


# ═══════════════════════════════════════════════════════════════
# 强制分析路径：11道闸门（缺一不可）
# ═══════════════════════════════════════════════════════════════

class Step11Judge:
    """
    40年实战经验蒸馏：11道硬闸
    每道闸门只做一件事，结果明确，不模糊。
    """

    # ── 闸门阈值（全局常量，封印版）──────────────────────────
    MIN_ALIGN       = ALIGN_MIN_GATE4    # 七维共振最低一致数（低于=WAIT）
    MIN_EV          = 0.0                # EV最低门槛（低于=WAIT）
    MIN_RR          = RR_MIN             # 最低风险收益比
    MIN_ATR_SL_MULT = ATR_SL_MIN_MULT    # SL最少是ATR1H的倍数
    CHOP_MAX_SCORE  = SCORE_CHOP_STD     # CHOP体制score上限（高于才考虑入场）
    # [设计院复盘 2026-10-01 苏摩111] 死穴硬封禁 → 高证据标准
    # 根因：BEAR_TREND:LONG WR=44.6%非0%，硬封禁=系统性失明
    # 修正：对齐 brahma_decision_engine(9.17清空) + regime_config(needs_consensus/needs_event)
    # 现在是「高证据标准」而非「永久封禁」
    HIGH_EVIDENCE_COMBOS: dict = {
        'BEAR_TREND:LONG':     {'min_score': SCORE_BEAR_LONG, 'min_align': ALIGN_BEAR_LONG,
                                 'label': '逆势熊市做多，需铁证(n=14可推翻)'},
        'BEAR_RECOVERY:SHORT': {'min_score': SCORE_BEAR_REC_SHT, 'min_align': ALIGN_BEAR_REC_SHT,
                                 'label': '熊市反弹做空，需事件驱动'},
    }

    def __init__(self, sym: str, d: dict, fvg: dict, ob: dict, liq: dict,
                 res: dict, oi: dict, sm: dict, vol: dict, mac: dict,
                 risk: dict, tb_result: dict):
        self.sym    = sym
        self.d      = d
        self.fvg    = fvg
        self.ob     = ob
        self.liq    = liq
        self.res    = res
        self.oi     = oi
        self.sm     = sm
        self.vol    = vol
        self.mac    = mac
        self.risk   = risk
        self.tb     = tb_result
        self.reason: list[str] = []
        self.blocked_by: str | None = None

        # 从各步结果提取关键数值
        self.price    = float(d.get('price', 0))
        self.regime   = str(d.get('regime', 'CHOP_MID')).upper()
        self.score    = float(d.get('bs', {}).get('score_final', d.get('bs', {}).get('score', 0)) or 0)
        self.align    = int(res.get('align_count', 0))
        self.direction = str(tb_result.get('direction', 'NONE')).upper()
        self.atr_1h   = float(vol.get('atr_1h', 0) or 0)
        self.atr_4h   = float(vol.get('atr_4h', 0) or 0)
        self.hurst    = float(vol.get('hurst', 0.5) or 0.5)
        # [封印 2026-10-08 苏摩111] P1-③ CVD/GEX 追加 - Gate5 CHOP
        self.cvd_1h   = float(d.get('cvd_1h', 0) or 0)
        _gex_raw = d.get('gex', 0) or vol.get('gex', 0) or 0
        self.gex      = float(_gex_raw) if isinstance(_gex_raw, (int, float)) else 0.0
        self.oi_signal = str((oi or {}).get('signal', '') or (oi or {}).get('main_signal', '') or '')
        # [封印 2026-10-17 苏摩111] RSI先行信号注入：Gate5豁免判断需要
        _bs = d.get('bs', {})
        self.rsi_1h   = float(d.get('rsi_1h', _bs.get('rsi_1h', 50)) or 50)
        self.rsi_4h   = float(d.get('rsi_4h', _bs.get('rsi_4h', 50)) or 50)
        self.rsi_1d   = float(d.get('rsi_1d', _bs.get('rsi_1d', 50)) or 50)
        self.lsr_retail = float(d.get('lsr_retail', 0) or 0)
        self.zero_flip  = float(d.get('zero_flip', 0) or 0)
        self.entry_lo = float(tb_result.get('entry_lo', 0) or 0)
        self.entry_hi = float(tb_result.get('entry_hi', 0) or 0)
        self.sl       = float(tb_result.get('stop_loss', 0) or 0)
        self.tp1      = float(tb_result.get('tp1', 0) or 0)
        self.tp2      = float(tb_result.get('tp2', 0) or 0)
        self.rr       = float(tb_result.get('rr1', 0) or 0)

    # ──────────────────────────────────────────────────────────
    # 11道闸门（每道返回 True=通过 / False=阻断）
    # ──────────────────────────────────────────────────────────

    def gate1_data_freshness(self) -> bool:
        """Gate1: 数据新鲜度（>4h旧数据禁止入场）"""
        stale = self.d.get('_stale_keys', [])
        critical = [k for k in stale if any(x in k for x in ['price','regime','oi','liq'])]
        if critical:
            self.blocked_by = 'Gate1_数据过期'
            self.reason.append(f'❌ G1数据过期: {critical[:3]}，拒绝入场')
            return False
        self.reason.append(f'✅ G1数据新鲜')
        return True

    def gate2_direction(self) -> bool:
        """Gate2: 方向必须明确（NONE=WAIT）"""
        if self.direction == 'NONE':
            self.blocked_by = 'Gate2_无方向'
            self.reason.append(f'❌ G2无明确方向（trader_brain=NONE），WAIT')
            return False
        self.reason.append(f'✅ G2方向={self.direction}')
        return True

    def gate1b_vol_price_truth(self) -> bool:
        """Gate1.5: 量价真实性（主力行为识别）
        [设计院 2026-10-16 苏摩111封印] P0
        复用: brahma_brain/vol_price_gate.py
        """
        vpg = self.d.get('_vol_price_gate', {})
        if not vpg or not vpg.get('has_critical'):
            self.reason.append(f'✅ G1b量价正常 vol_ratio={vpg.get("vol_ratio","N/A")}')
            return True
        if vpg.get('block_long') and self.direction == 'LONG':
            sigs = [s['desc'] for s in vpg.get('signals', []) if s.get('severity') == 'CRITICAL']
            self.blocked_by = 'Gate1b_量价背离阻断多单'
            self.reason.append(f'❌ G1b量价CRITICAL→阻断多单: {sigs[0] if sigs else "CRITICAL"}')
            return False
        if vpg.get('block_short') and self.direction == 'SHORT':
            sigs = [s['desc'] for s in vpg.get('signals', []) if s.get('severity') == 'CRITICAL']
            self.blocked_by = 'Gate1b_量价背离阻断空单'
            self.reason.append(f'❌ G1b量价CRITICAL→阻断空单: {sigs[0] if sigs else "CRITICAL"}')
            return False
        self.reason.append(f'✅ G1b量价有警告但非CRITICAL，通过')
        return True

    def gate2b_fangcang_anchor(self) -> bool:
        """Gate2.5: 方仓锚定门（贝叶斯先验层）
        [设计院 2026-10-16 苏摩111封印] P0
        n≥15且dominant≥90% → HARD锁定，反向入场直接WAIT
        n≥15且dominant≥75% → SOFT锁定，反向仓位×0.3
        今日回测：10/14 fc=SHORT 100% n=20 → RSI 1H=9.8不能推翻方向 ✅
        """
        fc_sig   = str(self.d.get('fc_signal', '') or '')
        fc_n     = int(self.d.get('fc_n_similar', 0) or 0)
        fc_short = float(self.d.get('fc_short_pct', 0) or 0)
        fc_long  = float(self.d.get('fc_long_pct', 0) or 0)

        if fc_n < 8:  # [P1-D 2026-10-27 苏摩111] 15→8，方仓激活更多案例
            self.reason.append(f'⚪ G2b方仓n={fc_n}<8，样本不足，跳过锚定')
            return True

        dominant_pct = max(fc_short, fc_long)
        anchor_dir   = 'SHORT' if fc_short > fc_long else 'LONG'

        if dominant_pct >= 0.90:
            if self.direction != anchor_dir:
                self.blocked_by = f'Gate2b_方仓HARD锁定:{anchor_dir}{dominant_pct*100:.0f}%'
                self.reason.append(
                    f'❌ G2b方仓HARD锁定 {anchor_dir} {dominant_pct*100:.0f}%（n={fc_n}）\n'
                    f'   当前方向={self.direction} 与方仓冲突 → WAIT\n'
                    f'   RSI/FVG实时信号降级为TIMING_ONLY，不可覆盖方仓方向'
                )
                return False
            self.reason.append(f'✅ G2b方仓HARD {anchor_dir} {dominant_pct*100:.0f}%（n={fc_n}），方向一致')
            return True

        if dominant_pct >= 0.75:
            if self.direction != anchor_dir:
                self.d['_fangcang_soft_lock'] = True
                self.d['_fangcang_pos_scale']  = 0.3
                self.reason.append(
                    f'⚠️ G2b方仓SOFT锁定 {anchor_dir} {dominant_pct*100:.0f}%（n={fc_n}）\n'
                    f'   反向信号降仓×0.3执行'
                )
            else:
                self.reason.append(f'✅ G2b方仓SOFT {anchor_dir} {dominant_pct*100:.0f}%，方向一致')
            return True

        self.reason.append(f'⚪ G2b方仓{anchor_dir} {dominant_pct*100:.0f}%（n={fc_n}），置信不足，不锚定')
        return True

    def gate2b_macro_lock(self) -> bool:
        """Gate2.5: 宏观方向硬锁（CHOP_MID场景专用）
        [封印 2026-10-27 苏摩111] 预判型升级P0

        逻辑：
          BULL_EARLY/MID/LATE + CHOP_MID + 做空 → 硬拒
          BEAR_EARLY/DEEP + CHOP_MID + 做多 → 硬拒
          非CHOP体制保留原有软降权（不在此拦截）

        目的：宏观BULL时，即使4H是CHOP，也不做空
              08/17那周宏观=BULL_EARLY，所有空单在此被拦截
        """
        if 'CHOP' not in self.regime:
            self.reason.append('✅ G2.5 非CHOP体制，跳过宏观硬锁')
            return True

        # 读宏观体制
        _mac_d = self.mac or {}
        _sym_upper = self.sym.upper().replace('USDT','')
        _mac_sym = _mac_d.get(_sym_upper, _mac_d)
        _macro = str(_mac_sym.get('macro_regime','') or _mac_d.get('macro_regime','') or '')

        # ETH_WEAK铁律⑤豁免：ETH相对弱时BULL_EARLY允许做空
        _eth_rel = float(_mac_d.get('ETH_BTC_rel_4w', 0) or 0)
        _eth_weak = (_sym_upper == 'ETH') and (_eth_rel < -0.05)

        if 'BULL' in _macro and self.direction == 'SHORT' and not _eth_weak:
            self.blocked_by = f'Gate2b_宏观锁:CHOP+{_macro}禁空'
            self.reason.append(f'❌ G2.5 CHOP_MID+{_macro}→禁止做空（应做多或等待）')
            return False

        if 'BEAR' in _macro and self.direction == 'LONG':
            self.blocked_by = f'Gate2b_宏观锁:CHOP+{_macro}禁多'
            self.reason.append(f'❌ G2.5 CHOP_MID+{_macro}→禁止做多（应做空或等待）')
            return False

        _note = f'CHOP+{_macro}+{self.direction}方向一致' if _macro else 'CHOP宏观未知，跳过'
        if _eth_weak and 'BULL' in _macro:
            _note += ' (ETH_WEAK铁律⑤豁免)'
        self.reason.append(f'✅ G2.5 {_note}')
        return True

    def gate3_dead_combo(self) -> bool:
        """Gate3: 高证据标准检查（对齐系统9.17实际语义，非硬封禁）"""
        key = f'{self.regime}:{self.direction}'
        combo = self.HIGH_EVIDENCE_COMBOS.get(key)
        if combo:
            need_score = combo['min_score']
            need_align = combo['min_align']
            label      = combo['label']
            # 未达高证据标准 → WAIT（不是永封，是暂时条件不足）
            if self.score < need_score or self.align < need_align:
                self.blocked_by = f'Gate3_高证据标准:{key} score={self.score:.0f}<{need_score} align={self.align}<{need_align}'
                self.reason.append(
                    f'❌ G3高证据标准: {label}\n'
                    f'   需要 score≥{need_score}(当前{self.score:.0f}) + 共振≥{need_align}(当前{self.align})\n'
                    f'   条件不足=WAIT，非永封，铁证足够可突破'
                )
                return False
            else:
                self.reason.append(f'✅ G3高证据标准通过: {key} score={self.score:.0f}≥{need_score} align={self.align}≥{need_align}')
                return True
        self.reason.append(f'✅ G3常规体制方向，无特殊限制')
        return True

    def gate4_resonance(self) -> bool:
        """Gate4: 七维共振≥3（数据不足=WAIT）"""
        if self.align < self.MIN_ALIGN:
            self.blocked_by = f'Gate4_共振不足:{self.align}/7'
            self.reason.append(f'❌ G4共振{self.align}/7 < {self.MIN_ALIGN}，信号太弱，WAIT')
            return False
        self.reason.append(f'✅ G4共振{self.align}/7 ≥ {self.MIN_ALIGN}')
        return True

    def gate4b_transition_exempt(self) -> bool:
        """Gate4.5: 体制切换预判豁免（transition_score>=65时绕过CHOP门控）
        [封印 2026-10-27 苏摩111] P1-C 预判型升级

        OI骤增+FR转向+清算密度激增三合一 → transition_score>=65
        → 豁免Gate5 CHOP_MID阻断（体制正在切换中，不应拒绝信号）
        """
        if 'CHOP' not in self.regime:
            self.reason.append('✅ G4.5 非CHOP体制，跳过体制切换检查')
            return True
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _sys.path.insert(0, str(_Path(__file__).parent))
            from regime_transition_detector import check_gate4b_exemption
            exempt, note = check_gate4b_exemption(self.sym.replace('USDT',''))
            if exempt:
                self.reason.append(f'✅ G4.5 体制切换豁免: {note}')
                # 豁免时直接跳过Gate5
                self._transition_exempt = True
            else:
                self.reason.append(f'⏳ G4.5 体制切换未达阈值: {note}')
                self._transition_exempt = False
        except Exception as _e:
            self.reason.append(f'⚪ G4.5 体制切换检测异常: {_e}')
            self._transition_exempt = False
        return True  # Gate4.5永远不阻断，只设置_transition_exempt标志

    def gate5_chop_score(self) -> bool:
        """Gate5: CHOP体制需更高分数（Hurst>0.6时旁路——趋势区不应锁死）
        [P1修复 2026-10-01 苏摩111]
        根因：Hurst=0.622已进趋势区，但CHOP门槛一刀切导致WAIT锁死
        修正：Hurst>0.6时门槛从110降到85（趋势信号优先，CHOP只是滞后标签）
        """
        # [封印 2026-10-27 苏摩111] Gate4.5豁免：体制切换中直接通过Gate5
        if getattr(self, '_transition_exempt', False):
            self.reason.append('✅ G5 Gate4.5体制切换豁免，跳过CHOP门槛')
            return True
        if 'CHOP' in self.regime:
            # Hurst>0.6 = 趋势性隐现，降低门槛
            if self.hurst >= HURST_TREND:
                # [方案A 2026-10-03 苏摩111] Hurst>0.65+OI BUILD = 趋势初期，大幅降门槛
                _oi_sig = str(getattr(self, 'oi_signal', '') or
                              (self.oi or {}).get('main_signal', ''))
                _oi_build = any(x in _oi_sig for x in ['BUILD','LONG_BUILD','SHORT_BUILD'])
                _resonance_ok = getattr(self, 'resonance_count', 0) >= 5
                if _oi_build and _resonance_ok:
                    effective_threshold = SCORE_CHOP_TREND  # 40分——趋势初期+OI BUILD+共振
                    _note = f'Hurst={self.hurst:.3f}≥0.65+OI_BUILD+共振≥5/7，CHOP趋势初期解锁→{effective_threshold}'
                else:
                    effective_threshold = SCORE_CHOP_TRANS  # 75分——有趋势但OI未确认
                    _note = f'Hurst={self.hurst:.3f}≥0.65但OI未BUILD，保守门槛→{effective_threshold}'
            elif self.hurst >= HURST_TRANSITION:
                effective_threshold = SCORE_CHOP_TRANS  # 中间过渡区
                _note = f'Hurst={self.hurst:.3f}≥0.55过渡区，门槛→{effective_threshold}'
            else:
                effective_threshold = SCORE_CHOP_STD    # 标准震荡门槛
                _note = f'Hurst={self.hurst:.3f}<0.55随机游走，全门槛{effective_threshold}'
            # [封印 2026-10-08 苏摩111] P1-③ CHOP门槛条件化
            # CVD极端(>±1000)+OI+GEX三向一致时，额外降低CHOP门槛至40
            _cvd_val = float(getattr(self, 'cvd_1h', 0) or 0)
            _gex_val = float(getattr(self, 'gex', 0) or 0)
            _oi_sig2 = str(getattr(self, 'oi_signal', '') or (self.oi or {}).get('main_signal', ''))
            _cvd_extreme = abs(_cvd_val) >= 1000
            _gex_neg = _gex_val < 0
            _oi_build2 = any(x in _oi_sig2 for x in ['BUILD', 'SHORT_BUILD'])
            if _cvd_extreme and _gex_neg and _oi_build2 and effective_threshold > 40:
                _old_thresh = effective_threshold
                effective_threshold = 40
                _note = (_note + f' | CVD极端({_cvd_val:+.0f})+GEX负值+OI BUILD → 三向共振，CHOP门槛降至{effective_threshold}')
            # [封印 2026-10-17 苏摩111] RSI先行信号豁免
            # 数据依据：8-9月405次RSI55~75触发 UP=80.7% RR=5.36 期望值+2.26%
            # 规则：RSI1H在[55,75]偏强区 → 门槛降45分 + 仓位×1/3（不是展宽，是降档通过）
            _rsi_early = float(getattr(self, 'rsi_1h', 50) or 50)
            if 55 <= _rsi_early <= 75 and self.score < effective_threshold and effective_threshold > 30:
                effective_threshold = max(30, effective_threshold - 45)
                _note = (_note + f' | RSI1H={_rsi_early:.0f}先行信号区[55~75](历史80.7%UP)→降档{effective_threshold}分,仓1/3')
                self._rsi_override_pos_scale = 0.33  # 仓位系数，下游vip读取
            else:
                self._rsi_override_pos_scale = 1.0
            # [封印 2026-10-27 苏摩111] 周线宏观豁免：周线BULL+4H CHOP → 允许做多
            _mac_d2 = self.mac or {}
            _sym2 = self.sym.upper().replace('USDT','')
            _mac2 = str((_mac_d2.get(_sym2,{}) or {}).get('macro_regime','') or '')
            _weekly_exempt = False
            if 'BULL' in _mac2 and self.direction == 'LONG':
                _weekly_exempt = True
                _note = _note + f' | 周线{_mac2}+做多→豁免CHOP门槛✅'
            elif 'BEAR' in _mac2 and self.direction == 'SHORT':
                _weekly_exempt = True
                _note = _note + f' | 周线{_mac2}+做空→豁免CHOP门槛✅'
            if _weekly_exempt:
                self.reason.append(f'✅ G5周线豁免 {_note}')
                return True

            if self.score < effective_threshold:
                self.blocked_by = f'Gate5_CHOP门槛:{self.score:.0f}<{effective_threshold}'
                self.reason.append(f'❌ G5 {_note} score={self.score:.0f} < {effective_threshold}')
                return False
            self.reason.append(f'✅ G5 {_note} score={self.score:.0f}通过')
            return True
        self.reason.append(f'✅ G5体制={self.regime} score={self.score:.0f}通过')
        return True

    def gate6_entry_valid(self) -> bool:
        """Gate6: 入场区必须有效（entry_lo/hi不为0且方向正确）"""
        if self.entry_lo <= 0 or self.entry_hi <= 0:
            self.blocked_by = 'Gate6_入场区无效'
            self.reason.append(f'❌ G6入场区无效 entry=({self.entry_lo},{self.entry_hi})')
            return False
        # 方向验证：多单入场区必须在现价附近或下方
        mid = (self.entry_lo + self.entry_hi) / 2
        if self.direction == 'LONG' and mid > self.price * 1.05:
            self.blocked_by = 'Gate6_多单入场区过高'
            self.reason.append(f'❌ G6多单入场区${mid:.2f}高于现价${self.price:.2f}×1.05')
            return False
        if self.direction == 'SHORT' and mid < self.price * 0.95:
            self.blocked_by = 'Gate6_空单入场区过低'
            self.reason.append(f'❌ G6空单入场区${mid:.2f}低于现价${self.price:.2f}×0.95')
            return False
        self.reason.append(f'✅ G6入场区${self.entry_lo:.2f}~${self.entry_hi:.2f}有效')
        return True

    def gate7_sl_atr(self) -> bool:
        """Gate7: SL距离≥1.5×ATR1H（防针刺止损）"""
        if self.sl <= 0 or self.atr_1h <= 0:
            self.reason.append(f'⚠️ G7 SL或ATR无数据，跳过ATR验证')
            return True
        entry_mid = (self.entry_lo + self.entry_hi) / 2
        sl_dist = abs(self.sl - entry_mid)
        min_sl = self.atr_1h * self.MIN_ATR_SL_MULT
        if sl_dist < min_sl:
            self.blocked_by = f'Gate7_SL过近:{sl_dist:.2f}<{min_sl:.2f}'
            self.reason.append(f'❌ G7 SL距离${sl_dist:.2f} < 1.5×ATR1H${min_sl:.2f}，易被针刺')
            return False
        self.reason.append(f'✅ G7 SL距离${sl_dist:.2f} ≥ 1.5×ATR1H${min_sl:.2f}')
        return True

    def gate8_rr(self) -> bool:
        """Gate8: 风险收益比≥1.5（低RR坚决不做）"""
        if self.rr < self.MIN_RR:
            self.blocked_by = f'Gate8_RR不足:{self.rr:.2f}<{self.MIN_RR}'
            self.reason.append(f'❌ G8 RR={self.rr:.2f} < {self.MIN_RR}，赔率不够')
            return False
        self.reason.append(f'✅ G8 RR={self.rr:.2f} ≥ {self.MIN_RR}')
        return True

    def gate9_risk_circuit(self) -> bool:
        """Gate9: 风控熔断器（连亏/回撤/反脆弱）"""
        if self.risk.get('circuit_open'):
            self.blocked_by = 'Gate9_熔断器触发'
            self.reason.append(f'❌ G9熔断器触发: {self.risk.get("circuit_reason","未知")}')
            return False
        max_dd = float(self.risk.get('max_drawdown_pct', 0) or 0)
        if max_dd > 15:
            self.blocked_by = f'Gate9_最大回撤:{max_dd:.1f}%>15%'
            self.reason.append(f'❌ G9最大回撤{max_dd:.1f}% > 15%限制')
            return False
        self.reason.append(f'✅ G9风控正常 回撤={max_dd:.1f}%')
        return True

    def gate10_macro_block(self) -> bool:
        """Gate10: 宏观重大事件窗口（NFP/FOMC当天禁止）"""
        if self.mac.get('major_event_today'):
            event = self.mac.get('event_name', '重大宏观事件')
            self.blocked_by = f'Gate10_宏观事件:{event}'
            self.reason.append(f'❌ G10今日{event}，禁止入场（黑天鹅风险）')
            return False
        self.reason.append(f'✅ G10无重大宏观事件')
        return True

    def gate11_ev_positive(self) -> bool:
        """Gate11: 期望值EV > 0（最终量化裁判）"""
        # EV = WR × RR - (1-WR) 简化模型
        wr_key = f'{self.regime}:{self.direction}'
        wr_matrix = {}
        try:
            import json
            wm = json.loads((BASE / 'data' / 'wr_matrix_live.json').read_text())
            wr_matrix = wm.get('matrix', {})
        except Exception:
            pass

        wr_entry = wr_matrix.get(wr_key, {})
        wr = float(wr_entry.get('wr', 0.45) or 0.45)  # 默认历史胜率
        n  = int(wr_entry.get('n', 0) or 0)
        ev = wr * self.rr - (1 - wr)

        if n < 8:
            self.reason.append(f'⚠️ G11 WR样本n={n}<8，用历史均值WR={wr:.1%} EV={ev:+.3f}')
        else:
            self.reason.append(f'✅ G11 WR={wr:.1%}(n={n}) RR={self.rr:.2f} EV={ev:+.3f}')

        if ev <= self.MIN_EV:
            self.blocked_by = f'Gate11_EV≤0:{ev:+.3f}'
            self.reason.append(f'❌ G11 EV={ev:+.3f} ≤ 0，数学期望负，WAIT')
            return False
        return True

    # ──────────────────────────────────────────────────────────
    # 主判决逻辑
    # ──────────────────────────────────────────────────────────

    def judge(self) -> dict:
        """执行11道闸门，返回最终判决"""

        gates = [
            self.gate1_data_freshness,
            self.gate1b_vol_price_truth,   # [2026-10-16 苏摩111封印] P0 量价真实性
            self.gate2_direction,
            self.gate2b_fangcang_anchor,   # [2026-10-16 苏摩111封印] P0 方仓锚定
            self.gate2b_macro_lock,        # [封印 2026-10-27 苏摩111] CHOP+宏观方向硬锁
            self.gate3_dead_combo,
            self.gate4_resonance,
            self.gate4b_transition_exempt,  # [封印 2026-10-27 苏摩111] 体制切换预判豁免
            self.gate5_chop_score,
            self.gate6_entry_valid,
            self.gate7_sl_atr,
            self.gate8_rr,
            self.gate9_risk_circuit,
            self.gate10_macro_block,
            self.gate11_ev_positive,
        ]

        for i, gate in enumerate(gates, 1):
            passed = gate()
            if not passed:
                return self._build_result('WAIT')

        # 全部通过 → 判定 ENTER 或 WATCH
        confidence = self.tb.get('confidence', 'LOW')
        cross = int(self.tb.get('cross_count', 0) or 0)

        if cross >= 3 and confidence in ('HIGH', 'MED'):
            verdict = 'ENTER'
        else:
            verdict = 'WATCH'
            self.reason.append(f'⚠️ 交叉验证{cross}/4，置信={confidence}，降为WATCH挂单')

        return self._build_result(verdict)

    def _build_result(self, verdict: str) -> dict:
        gate_count = sum(1 for r in self.reason if r.startswith('✅'))
        _pos_scale = float(getattr(self, '_rsi_override_pos_scale', 1.0))
        return {
            'verdict':    verdict,
            'direction':  self.direction,
            'reason':     self.reason,
            'blocked_by': self.blocked_by,
            'confidence': self.tb.get('confidence', 'LOW'),
            'gates_passed': gate_count,
            'entry_lo':   self.entry_lo,
            'entry_hi':   self.entry_hi,
            'sl':         self.sl,
            'tp1':        self.tp1,
            'tp2':        self.tp2,
            'rr':         self.rr,
            'rsi_override_pos_scale': _pos_scale,  # [2026-10-17] RSI豁免仓位系数
            'vip_card':   self._format_vip(verdict),
            '_judge':     self,  # 供外层读取_rsi_override_pos_scale
        }

    def _format_vip(self, verdict: str) -> str:
        """强制格式VIP卡片——唯一模板，绝不乱输出"""
        sym = self.sym
        price = self.price
        direction = self.direction
        regime = self.regime
        align = self.align
        blocked = self.blocked_by or ''

        # WAIT / 阻断 → 标准等待卡
        if verdict == 'WAIT':
            gate_summary = '\n'.join(
                f'  {r}' for r in self.reason[-3:] if '❌' in r
            )
            return (
                f'──── VIP ────\n'
                f'🌿 姓赵不宣 | {sym} 今日观察\n'
                f'⏳ [闸门] 策略未通过铁律校验，拒绝输出\n'
                f'{gate_summary}\n'
                f'   当前 体制={regime}\n'
                f'   闸门{11}封印 2026-09-18 苏摩111\n'
            )

        # ENTER / WATCH → 标准VIP格式（永久封印版）
        entry_str = f'${self.entry_lo:.2f}~${self.entry_hi:.2f}'
        sl_str    = f'${self.sl:.2f}'
        tp1_str   = f'${self.tp1:.2f}'
        tp2_str   = f'${self.tp2:.2f}'
        rr_str    = f'{self.rr:.1f}'

        if direction == 'SHORT':
            action_emoji = '🔴'
            action_txt   = '空单｜挂单区'
            wait_txt     = '🟢 暂无多单｜等待结构'
        else:
            action_emoji = '🟢'
            action_txt   = '多单｜挂单区'
            wait_txt     = '🔴 暂无空单｜等待结构'

        confidence = self.tb.get('confidence', 'LOW')
        lever = '5x' if 'TREND' in regime else '3x'
        # [封印 2026-10-17 苏摩111] RSI豁免降档仓位
        _ps = float(getattr(self, '_rsi_override_pos_scale', 1.0))
        _is_rsi_override = _ps < 1.0 and 'CHOP' in regime
        if _is_rsi_override:
            pos = '0.5%（RSI先行降档）'
            lever = '3x'
        else:
            pos = '2%' if verdict == 'ENTER' else '1%'

        enter_label = 'ENTER' if verdict == 'ENTER' else 'WATCH'
        tag = f'{enter_label} | 共振{align}/7 | {confidence}'
        rsi_tag = f'\n⚡ RSI1H={float(getattr(self,"rsi_1h",50)):.0f}先行区[55~75]历史80.7%UP · CHOP降档执行' if _is_rsi_override else ''

        return (
            f'──── VIP ────\n'
            f'🌿 姓赵不宣 | {sym} 今日布局\n'
            f'——— {sym} ———\n'
            f'{action_emoji} {action_txt} {entry_str}\n'
            f'止损 {sl_str}｜目标 {tp1_str}→{tp2_str}\n'
            f'杠杆 {lever}｜仓位 {pos}\n'
            f'{wait_txt}\n'
            f'⚠️ {tag}{rsi_tag}\n'
            f'🚫 破{sl_str}作废\n'
            f'🌿 姓赵不宣 | 不是建议\n'
        )


def run_step11(sym: str, d: dict, fvg: dict, ob: dict, liq: dict,
               res: dict, oi: dict, sm: dict, vol: dict, mac: dict,
               risk: dict, tb_result: dict) -> dict:
    """
    公开入口：执行11步强制裁判
    接入：brahma_manual_analysis.run_analysis() 末尾
    """
    judge = Step11Judge(sym, d, fvg, ob, liq, res, oi, sm, vol, mac, risk, tb_result)
    result = judge.judge()
    # [P0封印 2026-10-18 苏摩111] 轨迹记录 Trajectory Logging
    # 接入位置: run_step11()末尾，每次裁决写入 data/decision_trace.jsonl
    # 目的: 溯源「为什么做这个决策」，解决前后矛盾问题
    try:
        import json as _tj, time as _tt, pathlib as _tp
        _trace = {
            'ts':       _tt.strftime('%Y-%m-%dT%H:%MZ', _tt.gmtime()),
            'ts_epoch': _tt.time(),
            'sym':      sym,
            'price':    d.get('price', 0),
            'regime':   d.get('regime', '?'),
            'verdict':  result.get('verdict', 'WAIT'),
            'direction':result.get('direction', 'NONE'),
            'blocked_by': result.get('blocked_by'),
            'reason':   result.get('reason', [])[:3],
            'score':    d.get('score_final', d.get('bs', {}).get('score_final', 0)),
            'rsi_1h':   d.get('rsi_1h', 0),
            'cvd_1h':   d.get('cvd_1h', 0),
            'lsr_retail': d.get('lsr_retail', 0),
            'zero_flip':  d.get('zero_flip', 0),
            'align_count': res.get('align_count', 0),
            'oi_signal':   oi.get('signal', '?'),
            'entry_lo': result.get('entry_lo', 0),
            'entry_hi': result.get('entry_hi', 0),
            'sl':       result.get('sl', 0),
            'tp1':      result.get('tp1', 0),
            'rr':       result.get('rr', 0),
            'ev_pct':   result.get('ev_pct', 0),
            'vip_summary': (result.get('vip_card','')[:80] if result.get('vip_card') else ''),
        }
        _trace_path = _tp.Path(__file__).parent.parent / 'data' / 'decision_trace.jsonl'
        with open(_trace_path, 'a', encoding='utf-8') as _tf:
            _tf.write(_tj.dumps(_trace, ensure_ascii=False) + '\n')
        # 保持最新500条，防文件膨胀
        try:
            _lines = _trace_path.read_text(encoding='utf-8').splitlines()
            if len(_lines) > 500:
                _trace_path.write_text('\n'.join(_lines[-500:]) + '\n', encoding='utf-8')
        except Exception: pass
    except Exception as _te:
        pass  # 轨迹写入失败不影响主链
    return result


# ─── 自测 ──────────────────────────────────────────────────────
if __name__ == '__main__':
    print('Step11 Judge 自测...')
    # 构造极简测试数据
    mock = dict(
        price=83785, regime='CHOP_MID',
        bs={'score_final': 105, 'score': 105},
        _stale_keys=[]
    )
    mock_tb = dict(
        direction='SHORT', confidence='HIGH', cross_count=3,
        entry_lo=85200, entry_hi=85460,
        stop_loss=87170, tp1=82109, tp2=81188, rr1=2.5
    )
    mock_res = dict(align_count=4, resonance_ratio=0.8)
    mock_vol = dict(atr_1h=461, atr_4h=1011, hurst=0.622)
    mock_risk = dict(circuit_open=False, max_drawdown_pct=0.0)
    mock_mac = dict(major_event_today=False)

    r = run_step11(
        'BTC', mock, {}, {}, {},
        mock_res, {}, {}, mock_vol, mock_mac,
        mock_risk, mock_tb
    )
    print(f"判决: {r['verdict']} | 方向: {r['direction']} | 闸门通过: {r['gates_passed']}/11")
    print(f"阻断: {r['blocked_by']}")
    print('\n决策链:')
    for line in r['reason']: print(f'  {line}')
    print('\n最终卡片:')
    print(r['vip_card'])
