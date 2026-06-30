# 太医官 · 梵天神经系统
**版本**: v1.0 | **日期**: 2026-05-17 | **设计院**

---

## 定位

> "太医不治国，但国不可无太医。"

太医官专司梵天机体健康，诊脉、望色、察症、出医案。
**不掌交易，不管资金，只负责：系统是否健康运转。**

---

## 诊脉九层

| 层 | 代号 | 医术 | 职责 |
|----|------|------|------|
| L0 | 进脉 | `process_monitor.py` | 进程存活检测（ws_guardian / brahma_core）|
| L1 | 望色 | `schema_sentinel.py` | 数据结构完整性（JSON Schema 校验）|
| L2 | 闻声 | `log_sentinel.py` | 日志异常嗅探（ERROR / CRASH 模式）|
| L3 | 问症 | `anomaly_detector.py` | 行为偏差检测（信号漏斗/OI失效/重复开单）|
| L4 | 切脉 | `ast_scanner.py` | 代码静态扫描（AST 分析，慢，按需）|
| L5 | 鲜度 | `freshness_checker.py` | 数据新鲜度（价格/持仓/信号是否过时）|
| L6 | 对账 | `reconciler.py` | 持仓真实性校验（本地 vs 交易所）|
| L_KEY | 凭证 | `key_sentinel.py` | API Key 有效性（机要官哨兵）|
| L_BUS | 总线 | `nerve_bus_reader.py` | 实时事件总线（末梢上报）|

---

## 运行方式

```bash
# 快速诊断（跳过 L4 AST 扫描）
python3 nerve_system/nerve_runner.py --fast

# 完整诊断
python3 nerve_system/nerve_runner.py --full

# cron 触发（每1小时，light-context）
# → nerve-system-fast
```

---

## 医案格式

```
🏮 太医官 · 诊断医案
时间: 2026-05-17T12:34:35Z  耗时: 2.92s
告警: 3 ERROR / 2 WARN / 合计 5
L0 进脉: 2  L1 望色: 0  L2 闻声: 0  L3 问症: 1  L4 切脉: 0  L5 鲜度: 0  L6 对账: 2

🔴 ERROR（需立即关注）:
  [L0_PROCESS] ws_guardian: 进程未运行
  [L6_RECONCILE] R1_GHOST: HYPERUSDT 幽灵持仓已清理

🟡 WARN（需关注）:
  [L3_BEHAVIOR] 信号漏斗通过率 0%（过滤过严）
  [L_KEY] SQUARE_API_KEY: 3个Key失效
```

---

## 与其他官职的边界

| 系统 | 职责 | 太医官的关系 |
|------|------|-------------|
| 机要官 | 凭证管理 | 太医院的 L_KEY 层由机要官哨兵提供 |
| 协调官（蓝图守护） | Blueprint 一致性 | 太医官不管参数正不正确，只管模块存不存在 |
| 猎手拉娜 | 开仓决策 | 太医官监控猎手，不干预她的判断 |
| 梵天大脑 | 信号生成 | 太医官检测大脑输出的鲜度和行为偏差 |
