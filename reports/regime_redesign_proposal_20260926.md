# 体制规则重设计方案 · 设计院三方联合（2026-09-26 · 苏摩111待批）

> 触发：苏摩指令「体制评分权重已经清理，6层决策中体制规则重新设计方案，更加符合梵天系统的实现需求」
> 三方：梵天大脑（代码侦察）× 40年实战经验（审计视角）× 设计院（架构裁决）
> 状态：方案文本，未获111前不动代码。

## 一、侦察铁证（全部代码实证）

| # | 发现 | 铁证位置 |
|---|------|---------|
| 1 | 体制链双轨并存：SSOT建议表（只降不升）+ trader_brain硬编码（三票投票/突破覆盖/自我怀疑降级），trader_brain从不消费SSOT表 | regime_config.py L32-51 vs trader_brain.py L280-370；grep `get_regime_mult` 零命中 |
| 2 | SSOT表是"仓位放大器"不是"决策者"——mult只作用于position_sizer的pct cap，不参与方向/许可决策 | brahma_core L346 `_regime_position_cap` + L3257-3262 |
| 3 | 词汇表分裂：SSOT表18体制 vs 生产watcher只产5标签（当前11标的全部CHOP_MID）；BULL_PEAK/BEAR_CRASH/MOMENTUM_*等13体制无生产者 | regime_state.json全量CHOP_MID |
| 4 | 事件体制BULL_EXPLOSION只在trader_brain出现，SSOT表无此行（词汇表不一致） | trader_brain L262-275 |
| 5 | 三方对"封禁"语义不统一：SSOT表0.35×只降不升 / trader_brain自我怀疑0.5×降级 / signal_quality STATIC_LOCK"死穴永久封禁" | 三个文件三套语义 |
| 6 | v25.6已废除HARD_BLOCK改0.35×自然淘汰，但STATIC_LOCK仍锁CHOP_MID:LONG="永久封禁"——语义漂移活化石 | brahma_core L357-364 vs signal_quality L1071 |
| 7 | 死亡区间130-145 WR=17.9%是n=14小样本统计量被当宪法级硬规则（n<30不进硬规则是40年大忌） | signal_quality L114-121 |
| 8 | wr_feedback MAX_DEVIATION=0.40对称钳制允许低WR方向反哺放大到1.40——与"只降不升"封印精神矛盾 | wr_feedback L58, L187-189 |

## 二、重设计哲学（40年视角）

**"封禁"语义废除 → "证据标准"语义。**

市场上没有永远封禁的方向，只有证据标准——BULL下做空不是禁手，是"需要更硬的证据"（3/3全票或事件驱动）。v25.6的0.35×自然淘汰本已是这个思想，但展示层/MEMORY/STATIC_LOCK仍用旧语义，三方漂移。本次统一。

**三层语义分离：**

| 层 | 问题 | 单源 | API |
|---|------|------|-----|
| 方向层（谁能上桌） | trader_brain硬编码 | regime_config | `get_direction_gate(regime, dir) → 'open'\|'needs_consensus'\|'needs_event'` |
| 许可层（多高分开仓） | MIN_SCORE_OPEN统一100，无体制差异化 | regime_config | `get_score_gate(regime, dir) → {min_score, min_samples}` |
| 乘数层（多大仓） | 已是SSOT正确架构 | regime_config | `get_regime_mult_info()` 保持不变 |

## 三、方案本体

### 3.1 DIRECTION_GATE表（先验范围，不是封禁）

```python
DIRECTION_GATE = {
    # gate_level: 'open'=三票2/3即可 | 'needs_consensus'=三票3/3全票 | 'needs_event'=需事件驱动
    'BULL_TREND':    {'SHORT': 'needs_consensus'},
    'BULL_EARLY':    {'SHORT': 'needs_consensus'},
    'BEAR_TREND':    {'LONG': 'needs_consensus'},
    'BEAR_EARLY':    {'LONG': 'needs_consensus'},
    'BEAR_RECOVERY': {'SHORT': 'needs_event'},   # 宪法语义落表执法
    'CHOP_MID':      {},                          # 双向open（2/3）
    # 未列 = open（默认2/3三票）
}
```

### 3.2 SCORE_GATE表（体制差异化开仓门）

```python
SCORE_GATE = {
    # regime:dir → {min_score, min_samples, note}
    'BULL_TREND:LONG': {'min_score': 100, 'min_samples': 0,  'note': '回测WR56.7% n=3655大样本'},
    'BULL_TREND:SHORT':{'min_score': 120, 'min_samples': 0,  'note': '逆势方向从严'},
    'BEAR_TREND:SHORT':{'min_score': 100, 'min_samples': 0,  'note': '顺势'},
    'BEAR_TREND:LONG': {'min_score': 140, 'min_samples': 14, 'note': '死亡区间执法点，n<30→半开门'},
    'CHOP_MID:LONG':   {'min_score': 110, 'min_samples': 0,  'note': '宪法CHOP禁单语义→110'},
    'CHOP_MID:SHORT':  {'min_score': 100, 'min_samples': 0,  'note': 'WR57.3%铁证中性'},
}
```

### 3.3 小样本统计诚实原则

- 表行带`min_samples`：样本<30 → 执法降为半开门（mult×0.5 + WATCH），不是硬拒
- n=14的"WR=0%"从宪法级硬规则降为可被新样本推翻的表行
- 这不是放松风控，是把小样本证据的执法强度对齐其统计可信度

### 3.4 词汇表收敛（P2）

- 实际产出并集 = watcher5 {BULL_TREND, BULL_EARLY, BEAR_TREND, BEAR_EARLY, CHOP_MID} ∪ HMM4 {BULL_TREND, BEAR_TREND, BEAR_RECOVERY, CHOP_MID} = **6体制**
- 13个无生产者体制行（BULL_PEAK/BULL_BREAK/BEAR_CRASH/MOMENTUM_*/CHOP_HIGH/CHOP_RANGE_*/BREAKOUT）删除或降级为文档注释
- trade_gateway `_REGIME_CN` 映射同步收敛

### 3.5 MAX_DEVIATION单边化

`wr_feedback_engine.py`：钳制区间 `[baseline-0.40, baseline+0.40]` → `[baseline-0.40, baseline]`（override只能比baseline低，不能放大）。与「WR反哺只降不升」封印精神对齐，修根。

### 3.6 MEMORY封印口径修正（P2）

- 「BULL_TREND:LONG score≥140+SL≥3% WR=0% 永久封禁」→ SCORE_GATE表行（n=14，可推翻）
- 「BEAR_RECOVERY:SHORT WR=0% 严禁」→ DIRECTION_GATE `needs_event`
- 「永久封禁」→「高证据标准」

## 四、40年视角缺口 → 重设计后解法

| 缺口 | 解法 |
|------|------|
| 三方封禁语义漂移 | 方向/许可/乘数三层单源执法 |
| n=14小样本当宪法 | min_samples字段，n<30→半开门 |
| 词汇表分裂（18 vs 实际6） | vocab收敛+事件体制入表 |
| MAX_DEVIATION对称钳制 | 单边化只降不升 |
| L6终审哨兵盲区 | P2：哨兵挂gate_state（独立项） |

## 五、实施顺序（每步独立可回滚，冒烟全绿才动下一步）

**P1（本次执行范围）**
1. regime_config.py扩展：DIRECTION_GATE + SCORE_GATE + 三层API
2. trader_brain L280-370改消费新API（三票投票留trader_brain——信号层不搬家；体制规则读表）
3. signal_quality STATIC_LOCK合并进SCORE_GATE
4. wr_feedback MAX_DEVIATION单边化
5. 冒烟：官方12/12 + wiring9/9 + drift 0漂移 + SSOT行为矩阵7用例 + 新增门控用例

**P2（下一轮）**
6. 词汇表收敛18→6 + trade_gateway映射同步
7. MEMORY.md口径修正
8. L6终审哨兵（独立项）

**改动面**：5文件（regime_config / trader_brain / signal_quality_engine / wr_feedback / MEMORY），每步可回滚。

## 六、两个决定等111

1. **111 → P1+P2全量**（5文件本轮全改，词汇表收敛+MEMORY口径修正一并完成）
2. **P1 only**（新表+API+消费改写+MAX_DEVIATION单边化；词汇表与MEMORY口径下一轮再定）
