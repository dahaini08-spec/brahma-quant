"""
analysis_context.py — 梵天分析链强类型数据契约
[设计院封印 2026-10-01 苏摩111]

替代 run_analysis() 中用 d{} 字典隐式传递私有键的方式。
AnalysisContext 是步骤间唯一通信载体，所有字段有类型注解和默认值。

接入位置：
  - run_analysis() 创建 ctx = AnalysisContext(sym)
  - 各步骤写入 ctx.xxx，不再往 d{} 塞私有键
  - step11_mandatory_judge 读 ctx 而非解包 12 个参数
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AnalysisContext:
    """一次完整分析的所有步骤输出，强类型，无拼写错误。"""

    # ── 基础 ──────────────────────────────────────────
    symbol:     str   = ''          # 'BTC' / 'ETH'
    price:      float = 0.0         # Step0 拉取价
    regime:     str   = 'CHOP_MID'  # 体制标签
    score:      float = 0.0         # score_final（94维）
    raw_d:      dict  = field(default_factory=dict)   # brahma_state 原始dict

    # ── Step1~3 结构层 ────────────────────────────────
    fvg:        dict  = field(default_factory=dict)   # FVG共识+磁铁
    ob:         dict  = field(default_factory=dict)   # OB有效性表
    liq:        dict  = field(default_factory=dict)   # 清算地图
    fc:         dict  = field(default_factory=dict)   # 方仓HCME
    rng:        dict  = field(default_factory=dict)   # 区间识别

    # ── Step4 共振 ────────────────────────────────────
    res:        dict  = field(default_factory=dict)   # step4_resonance 输出
    pat:        dict  = field(default_factory=dict)   # 形态识别

    # ── Step5~6 资金流 ────────────────────────────────
    oi:         dict  = field(default_factory=dict)   # OI三周期
    lsr_trig:   dict  = field(default_factory=dict)   # LSR触发
    sm:         dict  = field(default_factory=dict)   # 聪明钱

    # ── Step7 波动率 ──────────────────────────────────
    vol:        dict  = field(default_factory=dict)   # Hurst/HAR-RV/κ/GEX/ATR

    # ── Step8~9 宏观风控 ──────────────────────────────
    mac:        dict  = field(default_factory=dict)   # 宏观日历
    risk:       dict  = field(default_factory=dict)   # 风控门控

    # ── trader_brain 输出（原来散落在d/final_* 变量） ──
    tb_result:       dict  = field(default_factory=dict)
    tb_direction:    str   = 'NONE'    # 'LONG'|'SHORT'|'NONE'
    tb_action:       str   = 'WAIT'    # 'ENTER'|'WATCH'|'WAIT'|'SKIP'
    tb_confidence:   str   = 'LOW'     # 'HIGH'|'MED'|'LOW'
    tb_align:        int   = 0         # G4重算后的真实共振数
    tb_cross_count:  int   = 0         # 交叉验证通过数

    # ── Step10 VIP输出 ────────────────────────────────
    vip_card:   str   = ''            # 最终VIP卡片文本

    # ── Step11 最终裁判 ───────────────────────────────
    s11_result:     Optional[dict] = None
    s11_verdict:    str  = 'WAIT'    # 'ENTER'|'WATCH'|'WAIT'
    s11_gates_ok:   int  = 0
    s11_blocked_by: str  = ''

    # ── 推理层 ────────────────────────────────────────
    inference_fb:   dict = field(default_factory=dict)

    # ── 跨市场 ────────────────────────────────────────
    cma:        Optional[dict] = None  # cross_market_alpha

    # ── 辅助 ─────────────────────────────────────────
    elapsed_s:      float = 0.0
    data_ts:        str   = ''

    # ────────────────────────────────────────────────
    # 便捷属性
    # ────────────────────────────────────────────────

    @property
    def is_chop(self) -> bool:
        return 'CHOP' in self.regime

    @property
    def is_trend(self) -> bool:
        return 'TREND' in self.regime

    @property
    def hurst(self) -> float:
        return float(self.vol.get('hurst', 0.5) or 0.5)

    @property
    def atr_1h(self) -> float:
        return float(self.vol.get('atr_1h', 0) or 0)

    @property
    def atr_4h(self) -> float:
        return float(self.vol.get('atr_4h', 0) or 0)

    @property
    def fvg_dir(self) -> str:
        return str(self.fvg.get('dir', 'NONE')).upper()

    @property
    def align_count(self) -> int:
        """G4对齐数：优先用tb_align（trader_brain重算后），回退res值"""
        if self.tb_align > 0:
            return self.tb_align
        return int(self.res.get('align_count', 0))

    def to_step11_kwargs(self) -> dict:
        """给 Step11Judge 的标准化入参，不再 12参数爆炸"""
        return dict(
            sym=self.symbol, d=self.raw_d,
            fvg=self.fvg, ob=self.ob, liq=self.liq,
            res=self.res, oi=self.oi, sm=self.sm,
            vol=self.vol, mac=self.mac, risk=self.risk,
            tb_result=self.tb_result,
        )
