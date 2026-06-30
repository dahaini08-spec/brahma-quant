# 神经系统 (Nerve System)

梵天系统的感知层 — 在问题爆发前捕获信号。

## 四层架构

```
L1 Schema Sentinel    — 状态文件契约守卫，字段类型/范围实时校验
L2 Runtime Type Guard — 函数入口类型防护，.get()陷阱自动拦截  
L3 Behavioral Anomaly — 行为异常感知，扫描0信号/停机/NAV异常
L4 AST Bug Scanner    — 周期性代码静态扫描，主动发现潜在陷阱
```

## 文件

- `schema_sentinel.py`  — L1 状态契约
- `type_guard.py`       — L2 运行时类型保护
- `anomaly_detector.py` — L3 行为异常
- `ast_scanner.py`      — L4 代码扫描
- `nerve_core.py`       — 统一入口，输出到 brahma_state["nerve"]
