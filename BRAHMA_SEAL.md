
---
## 🏛️ 封口宪法 v2.0（2026-07-14 设计院封印）

### 封口前必须通过的5道门控
1. `python3 scripts/brahma_smoke_test.py` → 全绿才允许封口
2. 新模块 → 加入 `REQUIRED_MODULES`（smoke_test.py）
3. 新字段 → 在 `_panorama_full` 输出可见（run手动验证）
4. 新cron → 验证 delivery.to 含 `019f5e0f`
5. 重大改动 → 苏摩111批准 → 测试 → MEMORY.md封印

### 固化四防线
- 防线1: 封口门控（5道强制检查）
- 防线2: 冒烟测试（目标30项，每新增能力同步扩展）
- 防线3: 自愈健康检查（每新模块上线当天加入brahma_health.py）
- 防线4: MEMORY.md宪法（苏摩111最终批准）

### 模块路径规范（防漂移）
- `brahma_brain/` → 核心模块，所有import走这里
- `scripts/`       → 独立运行脚本，可被cron直接调用
- 同一功能不得两处实现，scripts/有的核心模块必须同步到brahma_brain/
