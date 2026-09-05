# 梵天设计院 × Multica Agent 配置文档
# 2026-09-05 苏摩111封印

## Step 1: 注册 multica.ai
打开 https://multica.ai → Sign up → 用GitHub账号登录

## Step 2: 连接 GitHub 仓库
Multica → Settings → Integrations → GitHub
→ 授权 GitHub App → 选择仓库: dahaini08-spec/brahma-quant

## Step 3: 创建 "梵天设计院" Agent
Multica → Agents → New Agent

填写以下信息：
- Name: 梵天设计院
- Description: 梵天量化系统AI工程师，负责代码修改、封印、冒烟测试
- Runtime: Custom Daemon
  - 填写 james-bond 服务器地址（OpenClaw已在运行）

Agent System Prompt（直接复制粘贴）:
---
你是梵天量化交易系统的AI工程师（设计院）。

梵天宪法铁律：
1. 每次修改前声明假设（接入位置在哪）
2. 新建模块必须在runner/core/brahma_brain中接入
3. 封印 = 代码完成 + 接入验证 + 冒烟测试全绿
4. commit message必须包含「接入位置：XXX」
5. BEAR_TREND做多/BULL_TREND做空 → 永久封禁

工作流程：
1. 读取Issue描述，理解任务目标
2. grep现有代码，找准修改位置
3. 最小代价原则：修根因，不打补丁
4. 修改完成 → 运行冒烟测试 → 创建PR
5. PR描述包含：修改内容 + 接入位置 + 测试结果

冒烟测试命令：
cd /root/.openclaw/workspace/trading-system
python3 brahma_brain/brahma_smoke_test.py
---

## Step 4: 创建 Skills（可复用的作战手册）

### Skill 1: 梵天封印流程
触发词: "封印", "P0修复", "P1修复"
内容:
1. 读取AGENTS.md了解设计院宪法
2. grep所有调用方（修根因不打补丁）
3. 在共享函数上修一次
4. 跑冒烟测试：python3 brahma_brain/brahma_smoke_test.py
5. git commit --no-verify -m "XXX [接入位置: YYY]"
6. 创建PR，描述修改内容

### Skill 2: 冒烟测试
触发词: "冒烟", "smoke test", "测试"
内容:
cd /root/.openclaw/workspace/trading-system
BRAHMA_SKIP_COUNCIL=1 python3 brahma_brain/brahma_smoke_test.py

### Skill 3: 梵天分析
触发词: "分析BTC", "分析ETH", "战场情报"
内容:
cd /root/.openclaw/workspace/trading-system
python3 scripts/brahma_manual_analysis.py --symbols BTC ETH

## Step 5: 创建首个测试Issue

Title: [测试] P0封印流程验证
Labels: P0, test
Assignee: 梵天设计院

Description:
验证Multica × 梵天工作流是否正常。

任务：
1. 读取 brahma_brain/free_llm_client.py
2. 确认 BRAHMA_CONSTITUTION 变量存在
3. 确认 TASK_MODEL_MAP 路由表完整（10个路由）
4. 跑冒烟测试确认全绿
5. 创建PR，描述验证结果

验收标准：
- [ ] BRAHMA_CONSTITUTION 包含5条铁律
- [ ] TASK_MODEL_MAP 包含council/regime/wr_audit等10个路由
- [ ] 冒烟测试全绿
- [ ] PR创建成功

## Step 6: 配置 Autopilots（定时任务）

### Autopilot 1: 每日战场报告
Schedule: 0 1 * * * (北京09:00)
Agent: 梵天设计院
Task: 运行 scripts/brahma_manual_analysis.py --symbols BTC ETH，输出VIP策略卡片

### Autopilot 2: 每日冒烟健康检查
Schedule: 0 0 * * * (北京08:00)
Agent: 梵天设计院
Task: 运行冒烟测试，有失败则创建P0 Issue

---

## 仓库信息
- GitHub: https://github.com/dahaini08-spec/brahma-quant
- 主分支: main
- CI: .github/workflows/brahma_ci.yml（已有，PR自动触发）
- 冒烟测试: brahma_brain/brahma_smoke_test.py

## Review Gate 规则
- P0修复：苏摩必须review diff后发111 approve
- P1/P2：设计院自测全绿后可直接merge
- 宪法级改动（FREE_MODELS/体制铁律）：苏摩111封印
