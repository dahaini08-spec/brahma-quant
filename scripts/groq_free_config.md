# Groq免费API接入方案
# 设计院 2026-09-03 苏摩111 · 待激活

## 免费额度
- 14,400 requests/day
- llama-3.1-8b-instant（速度极快，适合监控类判断）
- gemma2-9b-it（质量略高）

## 激活步骤
1. 注册：https://console.groq.com（免费）
2. 获取API Key
3. 写入环境变量：
   echo 'GROQ_API_KEY=your_key_here' >> /root/.openclaw/workspace/trading-system/.env

## 接入代码（已封装，激活后直接可用）
```python
import os, requests

def groq_judge(prompt: str, model="llama-3.1-8b-instant") -> str:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return ""
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role":"user","content":prompt}], "max_tokens": 200},
        timeout=10
    )
    return r.json()["choices"][0]["message"]["content"]
```

## 适用任务（Key激活后迁移）
- regime_switch_monitor.py → groq判断体制是否真正切换
- oi_watchlist_monitor.py  → groq判断OI异动是否值得推送
- cron_health_board.py     → groq判断告警级别

## 预期效果
- 监控类任务完全免费
- 每日节省额外 $0.26（目前仅剩Qwen3.5成本）
