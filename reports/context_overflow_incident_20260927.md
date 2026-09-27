# 48h上下文重置事故复盘报告（2026-09-27）

> 三方联合深评 · 顶层视角 · 设计院最高批准权流程
> 波及面：4 session / 2次真实重置 / 54次溢出拦截 / 23次空转压缩

## 一、核心结论

**根因：compaction 三参数在 200k 窗口上构成数学死锁（非代码bug）。**

- reserveTokens=100000 + keepRecentTokens=100000 + reserveTokensFloor=100000
- 预算 = 200k窗口 − reserve(100k) = 100k
- 压缩后 = keepRecent(100k) + 系统注入(~30k) ≈ 130k > 100k → 永不收敛
- keep预算 ≥ 全部消息(~72k) → 压缩器无事可摘（tokensBefore=0）→ 23次全空摘要
- 3次重试烧完 → 会话强制重置 → 排队消息重注入 → 再溢出 → 死循环
- 本次溢出仅 2399 tokens（est=102,399 vs budget=100,000）

## 二、问题清单（P1-P8）

| # | 问题 | 证据 | 定级 |
|---|------|------|------|
| P1 | 三参数数学死锁（根因） | precheck日志: est=102,399 > budget=100,000，ovf=2,399 | 🔴 P0 |
| P2 | 消息重放风暴（放大器） | 02:26-02:28 三轮×8条排队消息重注入（id/parentId各不同=队列重注入非用户重发） | 🔴 P0 |
| P3 | 压缩器空转 | 本线程23次压缩 tokensBefore全=0，摘要全"No prior history" | 🟠 P1 |
| P4 | 慢性病史 | 全库28,636次压缩/482个transcript，9.9起旧线程300-737次压缩全空摘要 | 🟠 P1 |
| P5 | 上下文窗口漂移 | models注册表=195k vs config models.providers=200000，runtime按200k算 | 🟡 P2 |
| P6 | 监控缺失 | 54次溢出/2次重置无人察觉，直至用户看到报错 | 🟠 P1（已修） |
| P7 | EROFS拦截自愈 | /etc/openclaw-config 只读tmpfs（K8s ConfigMap式投影），CLI patch亦被拒 | 🔴 P0（阻塞项） |
| P8 | midTurnPrecheck | route=truncate_tool_results_only 早期恢复正常，救不了结构性死锁 | 🟢 无需动 |

48h波及面：01a0d382=17次 / 01a0d71c=34次 / 99ad3973=3次 / 033e87af=2次。

## 三、三方联合评估

- **量化工程师**：Pi运行时语义正确（reserve=输出余量/keep=保留尾部/floor=保底下限），错在三个旋钮被拧到同一个100k。修复数学：reserve(40k)+keep(60k)+系统注入(30k)=130k ≤ 160k预算，余量30k。隐藏成本：每次重试=一次LLM摘要调用，全空摘要照样烧配额（与OpenRouter免费池烧穿相互加剧）。
- **量化分析师**：时间轴——00:12首次precheck命中(est=104,985)→持续拦截54次→02:25用户触发深评→02:26-02:28三轮重放→23次压缩全no-op→02:28:26最后36-token残段压缩后彻底重置。旧transcript 737/538/446次压缩证明病已潜伏17天。
- **顶级合约交易员**：交易直接伤害=0（B线纸面盘/cron舰队/SL监控全程未中断，哨兵日志确认）。真实伤害=指挥链断裂：02:26-02:28黄金决策窗口主线程失联，若持仓异动将无人响应。防御已补：哨兵*/10扫描，死锁再现即P0推送。

## 四、顶层设计

### 立即修复（苏摩平台层，唯一阻塞项）
```json5
// /etc/openclaw-config（宿主操作，容器内EROFS只读）
{ "agents": { "defaults": { "compaction": {
  "reserveTokens": 40000,
  "keepRecentTokens": 60000,
  "reserveTokensFloor": 40000
} } } }
```
- 不触碰 maxActiveTranscriptBytes（永久封禁项不动）
- 验证方式：precheck日志 est/budget 比值应降至 ~64%

### 已落地
- ✅ `trading-system/scripts/context_overflow_sentinel.py`：JSON-lines解析→90s事件簇→P2静默/P1推送/P0重置告警，push_hub去重12h，状态7天滚动
- ✅ 接线 `brahma_crontab.txt` */10（L27）+ supercronic热载，冒烟绿
- ✅ 本报告归档 + git commit + MEMORY.md 索引

### 长期防线（铁律）
1. **不变量**：`reserve + keep + 系统注入 < 窗口×80%`——任何compaction调参先过数学门
2. **重放防御**：会话重置后排队消息自动重注入是放大器；苏摩看到重置报错后等10秒再发新指令，勿急着重发批量消息
3. **P5**：contextWindow 200000 vs 注册表195k建议一并校正
4. **OpenRouter $10 credits**（既有决策待批）：解除LLM依赖路径连带脆弱性

## 五、教训沉淀

- 只读配置层（EROFS/tmpfs）= 平台级封禁，修复必须上浮到宿主操作——这是第二次同类教训（第一次是maxActiveTranscriptBytes）
- 配置耦合错误比代码bug更隐蔽：运行时语义全部正确，数学不可解时静默空转17天才急性发作
- 空转（no-op循环）比崩溃更危险：烧配额、烧日志、无告警——哨兵的价值就是把静默空转变成P0/P1信号
