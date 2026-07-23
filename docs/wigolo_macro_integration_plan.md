# wigolo × macro_calendar 集成方案草稿
设计院预研 · 2026-07-23

## 现状分析

**当前架构：**
- `macro_calendar.py`：2026全年FOMC/CPI/NFP日期**硬编码**，每季需人工更新
- `macro_calendar.json`：已计算好的下一个窗口列表（手动维护）
- 痛点：2027年到来前需要人工更新一次，容易遗漏临时加息会议等突发事件

## wigolo能补什么

wigolo = 本地优先网页搜索/抓取，无API key，零成本

```
替代路径：
  FOMC/CPI/NFP日期 → wigolo抓取 investing.com/forexfactory 日历
  突发性宏观事件    → wigolo抓取 Reuters/Bloomberg 标题
  TradFi Phase B    → wigolo抓取美股相关新闻作为 E_TF5 触发源
```

## 集成设计（Phase B时落地）

```python
# macro_calendar.py 新增函数（wigolo集成后）
def fetch_calendar_from_web():
    """
    触发条件: 当前hardcode日历距今<7天无新事件时自动触发
    数据源: wigolo搜索 "FOMC CPI NFP date 2026 2027"
    输出: 标准化事件列表，格式与MACRO_EVENTS_2026一致
    降级: wigolo失败→回退hardcode
    """
    pass

# tradfi_watcher.py 新增触发源
E_TF5 = "major_news"  # wigolo抓到 Fed/CPI/NFP 相关重大新闻标题
```

## 优先级判断

- **现在不装**：macro_calendar.py hardcode覆盖到2026年底，无紧急需求
- **触发条件**：TradFi Phase B启动（需30+条实证数据，当前0条）
                 OR 出现hardcode遗漏的突发宏观事件
- **预计时间**：2026年Q3末~Q4初

## 结论

wigolo对梵天的价值是真实的，但现在集成时机未到。
等Phase B条件成熟后，优先集成 `macro_calendar.py` 自动更新功能。
