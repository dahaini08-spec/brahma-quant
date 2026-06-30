# post_contract.md — 发帖操作契约 v1.0
# 设计院 · 防御纵深框架 Layer 1
# 2026-05-28
#
# 本文件是可执行契约，不是描述。
# 每次发帖前，AI 必须从第一条开始逐条判断，不可跳过。

---

## 发帖判断树（必须从上往下，命中即停止）

```
Step 1: 内容含「根据新浪财经公开数据」？
  YES → 走钉钉1路径：route("send_dd1", {"text": content})
        绝对停止，不往下走
  NO  → 继续 Step 2

Step 2: 内容含广场禁词？
  （梵天/达摩院/brahma/神级/体制代码/新浪财经/打爆/爆仓/稳赚）
  YES → 先调 news_formatter.clean(content)，再继续
  NO  → 继续 Step 3

Step 3: 是否有有效的广场 API Key？
  NO  → 报错，停止
  YES → 继续 Step 4

Step 4: 调用统一路由器
  route("post_square", {"content": cleaned_content, "broadcast": True})
  不得绕过 action_router 直接调 poster.py / auto_poster.py
```

---

## 绝对禁止（红线，无例外）

| 禁止行为 | 原因 | 历史案例 |
|---------|------|---------|
| DD1内容走 auto_poster | 钉钉1=内部格式，广场=公开平台 | ERR-001 2026-05-28 |
| 未经 clean() 直接发帖 | 内部词泄露 | ERR-003 2026-05-28 |
| 绕过 action_router 直调 poster | 绕过所有Guards | 设计红线 |
| 广场帖含「新浪财经」 | 格式特征词不应公开 | ERR-001 |

---

## 内容质量要求（广场帖）

- 字数：150~500字（过短无价值，过长截断）
- 必须含：至少一个品种代码（BTC/ETH/SOL/BNB）
- 必须含：明确观点（看多/看空/观望之一）
- 禁止：无具体数据的泛泛而谈
- 结尾：含返佣链接 https://www.bsmkweb.cc/register?ref=XZBX666

---

## 路由快查表

| 内容类型 | 调用方式 |
|---------|---------|
| 钉钉1策略信号（含新浪财经签名） | `route("send_dd1", {"text": ...})` |
| 广场信号帖（叙事风格） | `route("post_square", {"content": ...})` |
| 广场深度分析帖 | `route("post_square", {"content": ...})` |
| 钉钉系统告警 | `push_hub.send_system_alert(level, title)` |

---

## 回归测试

```bash
# 验证 ERR-001 不复现
cd /root/.openclaw/workspace/trading-system
python3 guardrails/error_registry.py --regression
```
