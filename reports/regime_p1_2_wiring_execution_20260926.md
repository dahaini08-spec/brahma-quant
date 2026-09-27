# 体制重设计 P1-2 接线执行收尾报告
> 苏摩111 · 2026-09-26 · 封印标准全绿

## 一、封印范围（git可验证）

| Commit | 内容 |
|---|---|
| `deb922f2` | P1-1: DIRECTION_GATE + SCORE_GATE + 三层API |
| `734aadfb` | P1-3: STATIC_LOCK合并进SCORE_GATE locked键 + tier API |
| `58c3df1f` | P1-4: wr_feedback_engine MAX_DEVIATION单边化（只降不升） |
| `7599638a` | **P1-2封印主体**: trader_brain方向准入消费三层API + brahma_core score_gate单源化 + Fix-1a/1b（SCORE_GATE补4行118/118/108/105，删内联boost）+ Fix-2（140幽灵文案→查locked tier键+direction gate）+ Fix-3（词汇表18→6，BREAKOUT 0.85）+ Fix-4（MEMORY.md口径修正≤12KB）+ Fix-5（D8接线哨兵）+ A/B分离双层硬闸 + B线账本 |
| `d5c3004c` | 42孤岛接线: contract_validator→module_check + 人格SSOT→复盘LLM + engine_v5/IC审计本地指标回退 + 周度cron |
| `8a8113b9` | wiring_check分析注入复活（fa5bb68d机械替换误杀修复） |
| `34a5922e` | trader_brain info_flags断头路修复（18处填充从未消费→透传decide()返回） |

## 二、封印标准验证

| 冒烟项 | 结果 |
|---|---|
| #1 py_compile 全模块 | ✅ 10/10 绿 |
| #2 梵天冒烟12项 | ✅ 12/12 绿（post-commit自动跑） |
| #4 drift_checker | ✅ 8/8 零漂移 |
| #5 SSOT矩阵8用例 | ✅ 全PASS（新鲜override命中/过期兜底/neutral/未知0.85/CHOP_MID=110/BEAR_EARLY=118/BULL tier锁/get_gate_state聚合） |
| #6 真实链路score_gate一致性 | ✅ 破案：非bug（见下） |
| #7 trader_brain执法用例 | ✅ B1 needs_consensus非全票×0.5 / B2 needs_event非事件×0.5 / B3 CHOP三票全票通过 / D info_flags透传 |
| wiring_check 分析链路 | ✅ 46模块真实输出、0假注入；孤岛32→30（余下=预存历史债） |

## 三、冒烟#6破案记录（预存行为，非P1-2引入）

- 现象：ETH `CHOP_MID:SHORT` 门控时刻score=157≥100 → 合法放行（`score_gate_reject=None`正确），但最终`total`=13.2 < 100，看似「应拒未拒」
- trace探针时间线：L1954 GapGate极端封锁（gap>20%）把total清0 → 后续段小幅调整回到13.2
- `cf['score']`=173 是另一条从未清零路径（9.21已记载的双路径怪癖）
- **结论**：冒烟#6原断言写错（拿最终total对比门控时刻分数）。score gate在门控时刻行为正确，下游封锁是另一层风控语义（锁总分为0=禁止入场），无断链
- 插桩已全部清除，py_compile绿

## 四、测试期间的意外收获

1. **wiring_check假绿修复**：fa5bb68d(9.13)机械替换误杀分析链路注入 → 恢复后46模块有真实输出，0个「import但零输出」假绿
2. **info_flags断头路**：P1-2方向准入执法写入了18处`_info_flags`（含`方向准入×0.5`标记）但decide()返回从未透传 → commit `34a5922e`补1行透传，冒烟#7验证执法标记可见
3. **BULL_TREND:LONG建议表语义澄清**：建议表「只降不升」，BULL_TREND:LONG顺方向=中性1.0（不写=中性），非bug
4. **双模块陷阱确认**：`regime_config`同时存在于sys.modules两个键（裸键+`brahma_brain.regime_config`），monkeypatch必须patch裸键（trader_brain L704先命中）

## 五、剩余已知债（非本轮范围）

- 30个预存孤岛（42孤岛治理范畴，wiring_check exit=1为历史债报警）
- MEMORY.md旧封印45.5%实盘WR数据已作废标注，待B线对账闭环后重新积累
- OpenRouter免费LLM通道全灭（daily_review静默空返回，AI议会不受影响）

## 六、给苏摩的一句话

P1-2三层API接线全链路闭环：体制→方向门→score门→tier锁→执法（×0.5+info标记）→透传报告，冒烟全绿，7个commit可审计。
