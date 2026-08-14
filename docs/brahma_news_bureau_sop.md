# 梵天新闻局 SOP v1.0
<!-- 设计院封印 2026-08-14 苏摩111 -->

## 一、新闻局定位

```
职责：负责梵天所有对外发帖内容的生产、审核、发布、复盘
目标：把梵天全能力分析转化为高质量KOL内容资产
账号：姓赵不宣（主账户 SQUARE_KEY_0）
     备用账户1、2（SQUARE_KEY_1/2，备用轮换）
```

---

## 二、发帖资产全景图

### 2.1 核心模块（已落地）

| 模块 | 文件 | 功能 | 调用方式 |
|------|------|------|---------|
| 热点主引擎 | `scripts/square/square_hot_poster.py` | 8种帖型自动生成+发布 | CLI `--type <type>` |
| KOL模板库 | `scripts/square/kol_templates.py` | 3人设×4体制×N模板 | `build_kol_post()` |
| 品牌声音规范 | `scripts/square/brand_voice.py` | A/B/C账户风格+禁词 | `check_brand_voice()` |
| 信号转发帖 | `scripts/signal_to_square.py` | SQE信号→内容→发布 | `python3 signal_to_square.py` |
| 内容去重 | `data/square_post_dedup.json` | 24H内MD5去重 | 自动 |
| 发帖日志 | `data/square_post_log.jsonl` | 历史发帖记录 | 自动 |

### 2.2 8种帖型定义

| 帖型ID | 触发场景 | 核心数据来源 | 字数目标 |
|--------|---------|------------|---------|
| `hot_tickers` | 广场热议标的 | Square热榜+合约数据 | 200~350 |
| `funding_rate` | FR极端（>0.1%或<-0.05%） | 全市FR扫描 | 150~250 |
| `top_gainers` | 今日涨幅榜 | 24H涨幅前5 | 200~300 |
| `top_losers` | 今日跌幅榜 | 24H跌幅前5 | 200~300 |
| `hot_news` | 重大新闻触发 | binance-pro-cli news | 150~250 |
| `smart_money` | 大户异动 | TopTrader数据 | 150~250 |
| `pump_alert` | 暴涨猎手触发 | pump_hunter信号 | 150~250 |
| `market_summary` | 每日收盘复盘 | 全市行情+梵天体制 | 250~400 |
| `edu` | 定时教育内容 | 知识库pool | 100~220 |
| **`full_analysis`** | 苏摩指令触发 | 梵天全能力35维 | 300~500 |
| **`battle_record`** | 胜率/战绩记录 | signal_settler结算 | 200~350 |
| **`tradfi_insight`** | TradFi品种分析 | tradfi_router+全能力 | 200~350 |

### 2.3 三账户人设矩阵

| 账户 | KEY | 人设 | 语气 | 禁词 |
|------|-----|------|------|------|
| A_RETAIL（姓赵不宣主） | KEY_0 | 被市场割过找到系统方法 | 第一人称，有温度 | BEAR_TREND等体制代码，AI风格词 |
| B_INSTITUTION | KEY_1 | 数据驱动量化分析师 | 客观冷静，信息密度高 | 我感觉，可能会 |
| C_NEWBIE | KEY_2 | 新手教学导师 | 教学风，解释术语 | OB结构，ATR，EMA等术语 |

---

## 三、内容审核标准（三道门）

### Gate-1：技术合规（自动，`check_content()`）

```
✅ 字数 30~500字（square_hot_poster硬限制）
✅ 无全角感叹号（！）← 广场违规词，直接封号风险
✅ 无体制代码（BEAR_TREND/CHOP_MID/BULL_TREND）
✅ 无内部词（仅供内部/DD1）
✅ 24H内容去重（MD5）
```

### Gate-2：结构完整（自动，`validate_post()`）

```
✅ 包含价格数字（$符号）
✅ 包含入场区（「入场」或「entry」）
✅ 包含止损（「止损」「保护」「SL」）
✅ 包含目标（「目标」「TP」）
✅ 字数 150~500字（KOL模板标准）
```

### Gate-3：品牌声音（自动，`check_brand_voice()`）

```
✅ 第一句有冲突感/数字/判断（钩子）
✅ 核心价位绝对精确（不四舍五入）
✅ 结尾统一：⚠️ 仅供参考 模拟复盘
✅ 无AI风格词（「系统给出」「引擎」「35个维度」「模型」「AI」）
✅ 无鸡汤金句堆砌
✅ 第一人称叙事（主账户A）
```

### Gate-4：新闻局人工复核（苏摩触发发帖时）

```
发帖前必须展示完整内容，苏摩确认后发布
发帖内容清单：
  □ 帖子正文预览
  □ 字数（150~500）
  □ 三道门检查结果
  □ 对应梵天信号或分析依据
```

---

## 四、内容生产流程（SOP）

### 4.1 全能力分析帖（最高优先级）

```
触发：苏摩说「分析XXX发帖」
流程：
  Step1 brahma_1hao_analysis.py --symbols XXX --direction LONG/SHORT
  Step2 解析：HCME WR + Kronos p_up + score + 清算集群 + 决策树
  Step3 根据梵天裁决选帖型：
    ENTER → full_analysis模板（有入场区/止损/目标）
    SKIP  → insight模板（分析洞察，不给操作建议）
    WATCH → watch模板（关注位+触发条件）
  Step4 三道门审核
  Step5 展示预览 → 苏摩确认 → 发布
```

### 4.2 自动发帖（cron驱动）

```
已运行cron任务（square_hot_poster）：
  - 热点榜（3H一次）
  - 日报复盘（每日22:00 CST）
  - 教育内容（每日10:00 CST）
  - 资金费率异常（触发式）
  - 暴涨猎手（触发式）

全自动流程：
  数据采集 → 内容生成 → 三道门检查 → 去重 → 发布 → 日志
```

### 4.3 战绩帖

```
触发：signal_settler结算后 WR≥60% 或单笔盈利≥100%
内容：
  □ 开仓价/当前价/盈利%
  □ 入场逻辑（不露梵天体制代码）
  □ 止损执行情况
  □ 关键价位留存
```

### 4.4 TradFi品种帖（新增）

```
触发：苏摩分析COIN/SNDK/NVDA等TradFi品种
特殊规则：
  A类品种：必须标注「美股交易时段内有效」
  C类品种：可全时段发帖
  禁止：将亚盘A类信号发布为操作建议（tradfi_router铁律1）
```

---

## 五、内容质量标准

### 黄金结构（主账户A_RETAIL）

```
[钩子] 第一句：数字+判断+冲突感（不超过20字）
[背景] 1~2句：为什么这个位置重要
[信号] 核心逻辑：用人话说出为什么做这个方向
[数据] 关键验证：1~2个最有说服力的数据点
[操作] 价位表：
  入场区：$XXX ~ $XXX
  止损：$XXX（-X%）
  目标：$XXX / $XXX（R:R=X.X）
[风险] 1句：这个逻辑何时失效
[免责] ⚠️ 仅供参考 模拟复盘
```

### 禁止清单（永久）

```
❌ 「大家好」「分享一下」「盯了很久」
❌ AI风：「系统」「引擎」「35个维度」「多模型」「AI分析」
❌ 体制代码：BEAR_TREND / CHOP_MID / BULL_TREND
❌ 全角感叹号！
❌ 吹嘘胜率，保证盈利
❌ 亚盘发布A类TradFi操作建议
❌ 无止损的操作建议
❌ 复制粘贴前一天的帖子
```

---

## 六、新闻局资产目录

```
scripts/square/
├── square_hot_poster.py    # 主引擎：8种帖型+审核+发布
├── kol_templates.py        # 模板库：3人设×体制矩阵
├── brand_voice.py          # 品牌声音规范
├── square_data_collector.py # 数据采集层
└── __init__.py

scripts/
├── signal_to_square.py     # 信号→内容流水线

data/
├── square_post_log.jsonl   # 历史发帖记录（54条）
├── square_post_dedup.json  # 24H去重库
└── content_pool/           # 教育内容知识库（如有）

docs/
└── brahma_news_bureau_sop.md  # 本文件（新闻局SOP）
```

---

## 七、待建设模块（P1/P2）

| 优先级 | 模块 | 说明 |
|--------|------|------|
| P1 | `brahma_news_bureau.py` | 统一调度器：所有帖型从一个入口触发 |
| P1 | TradFi帖型模板 | 针对A/B/C三类的差异化模板 |
| P1 | 战绩帖自动触发 | signal_settler结算后自动判断是否发帖 |
| P2 | 多账户轮换策略 | KEY_0/1/2按帖型自动路由 |
| P2 | 内容效果追踪 | 发帖后48H互动数据回收 |
| P2 | 财报日历集成 | C类TradFi财报前后自动触发专题帖 |

---

## 八、苏摩操作指令速查

```
「分析BTC发帖」     → 全能力分析 → full_analysis帖型
「战绩贴TUT」       → 查settler结算 → battle_record帖型
「分析SNDK发帖」    → tradfi_router C类 → tradfi_insight帖型
「日报发帖」        → market_summary帖型
「暴涨预警帖」      → pump_alert帖型
「发帖中出现XXX」   → 新闻局记录违规词 → 下次自动过滤
「新闻局审核」      → 展示最近5条发帖内容+三道门检查状态
```
