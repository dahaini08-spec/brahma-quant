# analysis_contract.md — 分析操作契约 v1.0
# 设计院 · 防御纵深框架 Layer 1
# 2026-05-28

---

## 分析判断树

```
Step 1: 是否有明确的分析标的（symbol）？
  NO  → 询问用户，不假设
  YES → 继续 Step 2

Step 2: 调用梵天系统
  route("run_analysis", {"symbol": "ETH", "brief": True})
  等价命令：python3 brahma_analyze.py ETH --brief
  绝对停止手写任何临时分析脚本

Step 3: 解读结果
  score >= 145 → S1信号，可入场逻辑
  score >= 120 → S2观察，等确认
  score < 120  → 报告中性，不建议入场
```

---

## 绝对禁止

| 禁止行为 | 替代方案 |
|---------|---------|
| 手写 EMA/RSI/MACD 脚本 | brahma_analyze.py |
| 手动拼接 /fapi/v1/klines 分析 | brahma_analyze.py |
| 自然语言「分析」代替实际调用 | brahma_analyze.py |
| 修改 brahma_brain/ 任意文件 | 先跑分析确认评分不降 |

---

## 评分解读标准

| 分段 | 级别 | 操作建议 |
|-----|------|---------|
| ≥145 | S1 🔴 | 有效信号，附入场/止损/T1 |
| ≥120 | S2 🟠 | 观察信号，等价格触达 |
| ≥100 | S3 ⚪ | 弱信号，仅参考 |
| <100 | 中性 | 不建议入场 |

---

## brahma_analyze.py 标准调用

```bash
# 单标的
python3 brahma_analyze.py ETH --brief

# 多标的
python3 brahma_analyze.py ETH BTC SOL --brief

# 指定方向
python3 brahma_analyze.py ETH SHORT

# JSON格式（程序解析）
python3 brahma_analyze.py ETH --json
```

工作目录：`/root/.openclaw/workspace/trading-system/`

---

## 【数据溯源铁律 v2.0 · 2026-06-04 ERR-006后强制升级】

### 分析前必须执行的数据锚点查询

任何涉及以下内容，禁止使用训练记忆，必须先执行实时查询：

```
① 历史最高点/ATH
   → python3 guardrails/data_anchor.py
   → 或直接查 fapi/v1/klines?interval=1M&limit=200

② 历史最低点/ATL  
   → 同上，取最低值

③ 当前价格基准
   → fapi/v1/ticker/price

④ 跌幅/涨幅计算
   → 必须基于①②③的实时数据
   → 禁止凭印象说"大约xxx"

⑤ 用户明确提供的数字
   → 用户数字优先级 > 系统查询
   → 系统查询 > 训练记忆
   → 训练记忆 = 禁止单独使用
```

### 触发词清单（命中任意一个→强制查数据）
最高点 / ATH / 顶部 / 高点 / 跌幅 / 涨幅 / 底部 / 低点 / 从多少跌 / 距顶 / 历史最高

### 错误优先级
ERR-006 = P0级别，与DD1格式错误同等级别
一旦违反 = 立即停止输出，查数据后重新开始

