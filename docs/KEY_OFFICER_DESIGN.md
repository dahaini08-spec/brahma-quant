# 梵天机要官 · 全体系顶层设计 v2.0
**版本**: v2.0 | **日期**: 2026-05-17 | **作者**: 设计院

---

## 一、职责定义（精准边界）

机要官是梵天的**凭证基础设施层**，职责只有三件事：

| 职责 | 说明 | 工具 |
|------|------|------|
| **存储** | 所有 Key 集中在 `.env`，其余模块只读不写 | `alerts/.env` |
| **访问** | 统一读取接口，禁止直接读 os.environ / 自行解析 .env | `config.py` |
| **健康** | 定期检测连通性，异常告警 | `key_sentinel.py` → nerve L_KEY |

**不在职责范围：**
- 推送逻辑（归 `push_hub.py`）
- 交易参数（归 `hunter_config.py` / `MASTER_CONFIG.py`）
- 发帖策略（归 `scripts/square/poster.py`）

---

## 二、问题清单（审计发现）

### P0 — 存储分裂
| 问题 | 位置 | 影响 |
|------|------|------|
| `SQUARE_API_KEYS` 存在 `square/config.py` | 机要官无法统一管理 | set/rotate 需特殊处理 |
| `BROADCAST_KEYS` 未被机要官感知 | 蓝安/旧主用 key 游离在外 | 轮换时遗漏 |
| `COINGLASS_API_KEY` 同时在 `.env` 和 `square/config.py` | 双重存储 | 值可能不同步 |

### P1 — 绕过统一入口
| 文件 | 问题 | 修复方向 |
|------|------|---------|
| `auto_poster.py:171` | 直接 open `.env` 读 DINGTALK_SECRET | 改用 `from config import dingtalk_ai` |
| `position_monitor.py:28` | `os.environ.get('BINANCE_SECRET_KEY')` | 改用 `from config import binance_keys` |
| `position_monitor.py:40` | 直接 open `.env` 读 DINGTALK_AI_SECRET | 改用 `from config import dingtalk_ai` |

### P2 — 字段冗余
| 问题 | 说明 |
|------|------|
| `BINANCE_SECRET` vs `BINANCE_SECRET_KEY` | 同一个值存了两遍，由 position_monitor 引发 |
| `push_hub.py` 存在两份 | `trading-system/scripts/` 和 `workspace/scripts/` 各一份，版本不同 |

### P3 — key_officer 功能不完整
| 缺失功能 | 说明 |
|---------|------|
| 不管 `BROADCAST_KEYS` | 发帖广播 key 游离在外 |
| `set` 只写 `.env`，Square key 走特殊路径 | 接口不一致 |
| 无 `audit` 命令 | 无法一键发现绕过统一入口的模块 |
| 无 `sync` 命令 | 无法一键对齐 .env ↔ config.py 的 COINGLASS key |

---

## 三、目标架构（v2.0）

```
┌──────────────────────────────────────────────────────────────┐
│                  唯一真相源：alerts/.env                       │
│                                                              │
│  BINANCE_API_KEY / BINANCE_SECRET                            │
│  DINGTALK_WEBHOOK / DINGTALK_SECRET                          │
│  DINGTALK_AI_WEBHOOK / DINGTALK_AI_SECRET                    │
│  COINGLASS_API_KEY                                           │
│  SQUARE_KEY_0 / SQUARE_KEY_1 / SQUARE_KEY_2    ← 迁移入 .env │
│  BROADCAST_KEY_0 / BROADCAST_KEY_1             ← 迁移入 .env │
└────────────────────────┬─────────────────────────────────────┘
                         │ 唯一读取入口
┌────────────────────────▼─────────────────────────────────────┐
│                     config.py（只读层）                        │
│                                                              │
│  binance_keys()      → (api_key, secret)                     │
│  dingtalk_main()     → (webhook, secret)                     │
│  dingtalk_ai()       → (webhook, secret)                     │
│  coinglass_key()     → api_key                               │
│  square_keys()       → [key0, key1, key2]   ← 新增            │
│  broadcast_keys()    → [key0, key1]         ← 新增            │
└────────┬──────────────┬──────────────────────────────────────┘
         │              │
         ▼              ▼
  交易模块          推送/发帖模块
  (scanner/         (push_hub /
   executor/         poster /
   sizer)            auto_poster)

┌──────────────────────────────────────────────────────────────┐
│                   key_officer.py（管理工具）                   │
│                                                              │
│  status   查看所有 key 配置状态（脱敏）                         │
│  test     在线连通性测试（Binance/DingTalk/Coinglass/Square）   │
│  set      写入 .env（统一入口，自动更新 config.py 兼容层）       │
│  rotate   Square/DingTalk key 轮换（先测再换）                  │
│  audit    扫描所有模块，报告绕过统一入口的直读行为               │
│  sync     对齐 .env ↔ square/config.py 的冗余字段              │
│  list     列出所有注册 key 名称                                 │
└────────────────────────┬─────────────────────────────────────┘
                         │ 健康监控
┌────────────────────────▼─────────────────────────────────────┐
│              key_sentinel.py（神经系统 L_KEY 层）              │
│                                                              │
│  Binance    5分钟缓存                                         │
│  DingTalk   24小时缓存（避免频率限制）                          │
│  Coinglass  1小时缓存                                         │
│  Square     12小时缓存（所有 key）                             │
└──────────────────────────────────────────────────────────────┘
```

---

## 四、迁移方案（Square Key → .env）

将 `square/config.py` 里的 key 迁移进 `.env`，`config.py` 读取：

```python
# config.py 新增
def square_keys() -> list:
    env = _load_env()
    keys = []
    for i in range(5):  # 最多5个
        k = env.get(f"SQUARE_KEY_{i}", "")
        if k: keys.append(k)
    # 兼容回退：读 square/config.py
    if not keys:
        keys = _read_square_config_keys()
    return keys

def broadcast_keys() -> list:
    env = _load_env()
    keys = []
    for i in range(5):
        k = env.get(f"BROADCAST_KEY_{i}", "")
        if k: keys.append(k)
    return keys or square_keys()[:2]  # 兜底用前2个square key
```

---

## 五、实施计划

| 优先级 | 任务 | 工时 | 状态 |
|--------|------|------|------|
| P0 | Square key 迁移进 .env | 10min | 待执行 |
| P0 | config.py 新增 square_keys() / broadcast_keys() | 10min | 待执行 |
| P0 | square/config.py 改为从 config.py 读（保持兼容） | 10min | 待执行 |
| P1 | key_officer 新增 audit / sync 命令 | 20min | 待执行 |
| P1 | auto_poster.py / position_monitor.py 统一走 config.py | 15min | 待执行 |
| P1 | 清理 BINANCE_SECRET_KEY 冗余字段 | 5min | 待执行 |
| P2 | push_hub.py 两份合并（保留 trading-system/scripts/版本） | 10min | 待执行 |
| P2 | workspace/scripts/push_hub.py 改为导入 trading-system 版本 | 5min | 待执行 |
