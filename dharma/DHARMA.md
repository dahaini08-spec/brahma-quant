# 梵天子系统 · 达 摩 院
# Dharma Research Lab v1.0
<!-- 2026-05-15 · 万能节点验证框架 -->

```
╔══════════════════════════════════════════════════════════════════╗
║  梵天子系统 · 达摩院 Dharma Research Lab                          ║
║  8年 × 20币 × 19,568节点 · 万能验证框架                           ║
║  Zero-API · 纯本地 · 可重现 · 无限可扩展                          ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 一、系统定位

达摩院是梵天的**知识生产子系统**。

梵天决策，拉娜执行，**达摩院负责验证**。

所有进入梵天系统的规则，必须先通过达摩院的统计检验。
没有数据支撑的规则，不得进入系统。

```
梵天主系统
  ├── 拉娜  lana/         — 信号生成 + 执行
  ├── 达摩院 dharma/      — 验证 + 知识发现  ← 本子系统
  └── 主控  main.py       — 调度
```

---

## 二、目录结构

```
dharma/
  dharma_core.py          ← 核心引擎（数据/统计/实验基类）
  dharma_runner.py        ← 主运行器（菜单式）
  DHARMA.md               ← 本文档
  __init__.py
  
  experiments/
    __init__.py           ← 全部8个实验模块
  
  results/
    _registry.json        ← 实验注册表（自动维护）
    exp01_*.json          ← 各实验结果
    exp02_*.json
    ...
```

---

## 三、核心组件

### NodeDB — 全量节点数据库
```python
db = NodeDB.get().load()       # 单例，自动加载19,568节点
db.nodes                       # 全量列表
db.filter(year_from=2022)      # 过滤：当前体制
db.filter(coin_type='revert')  # 过滤：均值回归型标的
db.filter(regime='CHOP_HIGH')  # 过滤：高位震荡体制
db.filter(direction='空')       # 过滤：做空信号
```

### FeatureKit — 特征工具箱
```python
FeatureKit.get(node, 'RSI_1H')          # 提取单个特征
FeatureKit.get_win(node, '紧止损')       # 获取胜负标签 1/0
FeatureKit.get_pnl(node, '24根后收益%') # 获取PnL
FeatureKit.extract_vector(node, atr_idx) # 完整特征向量
FeatureKit.information_gain(labels, splits) # 信息增益
```

### StatEngine — 统计引擎
```python
StatEngine.bootstrap_wr(samples, n_iter=1000)  # Bootstrap胜率
StatEngine.bootstrap_mean(values)              # Bootstrap均值
StatEngine.profit_factor(wins, losses)         # PF利润因子
StatEngine.kelly_fraction(wr, avg_win, avg_loss) # Kelly仓位
```

### DharmaExperiment — 实验基类
```python
class MyExperiment(DharmaExperiment):
    name = "my_exp"
    description = "我的实验"
    version = "1.0"
    
    def run(self) -> dict:
        nodes = self.db.nodes
        # ... 你的验证逻辑 ...
        self.print_wr("标签", bootstrap_result)
        return {'key': value}

# 执行（自动保存结果到results/）
MyExperiment().execute()
```

---

## 四、实验清单

| 编号 | 名称 | 内容 | 状态 |
|------|------|------|------|
| EXP-01 | 信号质量三分类 | 三都赢/三都输分布，按币种排行 | ✅ |
| EXP-02 | Bootstrap基准 | RSI/ATR/体制/方向全维度胜率 | ✅ |
| EXP-03 | 过滤链穷举 | 2022+体制下最优复合过滤链 | ✅ |
| EXP-04 | 特征重要性 | 信息增益排序，最优/最差分桶 | ✅ |
| EXP-05 | 双周期RSI枚举 | 1H×4H完整组合空间 | ✅ |
| EXP-06 | 持仓时间矩阵 | 体制×信号强度×出场时机 | ✅ |
| EXP-07 | Alpha腐烂检测 | 各规律按时间窗口有效性 | ✅ |
| EXP-08 | PF利润因子 | 止盈革命，持仓时间vs PF | ✅ |

---

## 五、运行方式

```bash
# 交互菜单
python3 dharma_runner.py

# 运行指定实验
python3 dharma_runner.py 01 03 08

# 运行全部
python3 dharma_runner.py all

# 查看历史结果
python3 dharma_runner.py list
```

---

## 六、扩展新实验

1. 在 `experiments/__init__.py` 末尾添加新类（继承 `DharmaExperiment`）
2. 在 `dharma_runner.py` 的 `EXPERIMENTS` 字典中注册
3. 运行即可，结果自动保存

```python
# 示例：新增资金费率Alpha验证
class Exp09_FundingRate(DharmaExperiment):
    name = "exp09_funding_rate"
    description = "资金费率Alpha验证"
    version = "1.0"

    def run(self):
        # 加载FR历史数据（需外部数据）
        # 与节点时间戳对齐
        # Bootstrap验证FR条件下的胜率提升
        ...
        return results
```

---

## 七、已验证核心发现

### 🔴 必须知道的事实（不可违反）

```
1. 趋势型标的（BTC/ETH）三都赢率最低（15.8%/18.0%）
   → 反转策略对趋势型标的无效

2. 61.2%的信号是假信号（三配置都输）
   → 系统最大任务：从源头识别这61.2%

3. 最优体制：底部复苏(RECOVERY)胜率38.0% ✅
   当前体制：高位震荡(CHOP_HIGH) 36.0% 🟡
   最差体制：熊市趋势(BEAR_TREND) 24.4% 🔴
```

### 🟢 有效的过滤组合（CI下界>35%）

```
最优过滤链（2022+体制，CI36.5%）：
  RSI极端(<25/>75) + 4H中性(40-60) + 低ATR分位(<33%) + 排除BTC/ETH
  胜率：41.6%  n=388

做空最优双周期（大样本）：
  RSI_1H=70-80 × RSI_4H=50-65  胜率35.8% CI[34.2%~37.4%] n=4508

Alpha腐烂：
  黄金组合在2024-26体制下+9.2%优势（最强）
  在2018-21单边趋势中无效甚至负效
```

### ⚠️ 待验证（下一批实验方向）

```
EXP-09  资金费率Alpha（需FR历史数据）
EXP-10  OI异动×RSI共振
EXP-11  Taker比率方向性
EXP-12  跨币种相关性过滤（BTC RSI作为宏观指示器）
EXP-13  黑天鹅节点识别（2022 FTX前后特征）
```

---

## 八、设计原则

```
1. 数据优先：所有规则来自统计，不来自直觉
2. Bootstrap强制：单点估计无效，必须有置信区间
3. 样本门槛：n<50的结果不显示/不采纳
4. 体制隔离：当前体制数据权重>历史数据
5. 可重现：固定seed，每次结果一致
6. 防过拟合：新规则必须有独立样本外验证
```

---

*达摩院 v1.0 · 2026-05-15 · 梵天子系统*
