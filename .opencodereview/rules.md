# 梵天交易系统专属 OCR 审查规则
# 设计院自主制定 2026-07-25

## 止损计算安全（CRITICAL）

- 做空止损必须 = 进场区上沿 × (1 + SL_PCT)，做多止损必须 = 进场区下沿 × (1 - SL_PCT)
- 禁止用 atr * 小乘数（如 atr * 0.28）计算止损，会产生噪音级止损（<0.5%）
- SL距离必须 ≥ 1.5×ATR4H，否则止损不合理，须标注 CRITICAL
- RR封印：CHOP/BULL体制做空 RR=1.0，BEAR体制做空 RR=1.0，不得虚报高RR

## 体制封禁规则（CRITICAL）

- BEAR_TREND_LONG（熊市趋势做多）：WR=45%，全局封禁，禁止绕过
- CHOP_MID 无铁证做多/空：封禁，score需≥155且grade≥80才允许
- 任何绕过 StructureGate（grade<80）的入场逻辑：标注 CRITICAL

## 梵天核心架构规则（ERROR）

- 禁止在 brahma_analysis_runner 入口之外新建裸 HTTP 分析调用
- 禁止修改 brahma_engine.py 的 _regime_mult 映射表（体制乘数），需苏摩111批准
- 所有 cron 必须带 --channel jarvis --to <userId>:thread:<threadId>，否则标注 ERROR
- 信号推送必须有 BRAHMA 标签格式校验，无标签不得推送

## 仓位与风控规则（ERROR）

- 单笔仓位 NAV 上限 10%（PIXEL教训），超出须标注 ERROR
- wuqu_positions 写入必须依赖 success=True，禁止无条件写入
- 止损监控脚本必须有 except + logging，裸 except: pass 须标注 ERROR

## 代码质量规则（WARN）

- venv/bin/python 而非系统 python3 用于生产脚本，混用须标注 WARN
- brahma_bus 缓存 TTL 必须有明确设置，无 TTL 的缓存条目须标注 WARN
- 新增外部 API 调用必须有 timeout 参数，无 timeout 须标注 WARN
- signal_weights.json 的乘数变更须有注释说明数据来源和验证批次
