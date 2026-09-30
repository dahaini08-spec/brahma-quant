#!/usr/bin/env python3
"""
system_config.py — 梵天系统配置中心
从环境变量或本地.secrets文件读取，不硬编码敏感信息
"""
import os
from pathlib import Path

# ── Binance API ───────────────────────────────────────────────────
API_KEY    = os.environ.get('BINANCE_API_KEY', '')
API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

if not API_KEY or not API_SECRET:
    _secrets = Path(__file__).parent.parent / '.secrets'
    if _secrets.exists():
        for line in _secrets.read_text().strip().split('\n'):
            if '=' in line:
                k, v = line.split('=', 1)
                if k.strip() == 'BINANCE_API_KEY':
                    API_KEY = v.strip()
                elif k.strip() == 'BINANCE_API_SECRET':
                    API_SECRET = v.strip()

# ── Binance 基础URL ────────────────────────────────────────────────
FAPI_BASE = os.environ.get('BINANCE_FAPI_BASE', 'https://fapi.binance.com')
TESTNET   = os.environ.get('BINANCE_TESTNET', 'false').lower() == 'true'

# ── Jarvis 推送路由（SSOT）────────────────────────────────────────
JARVIS_USER_ID   = os.environ.get('JARVIS_USER_ID',   '73295708')
# [P2-4 2026-09-30 重构 苏摩111] 线程SSOT统一：alerts/.env 是唯一线程源（与scripts/push_hub同源）。
# 旧兑底 01a0d79b... 已是死线程（9.27线程迁移后），环境变量未设时会丢消息——
# 读取顺序: 环境变量 > alerts/.env > 旧封印值兑底（仅容灾）。
def _load_thread_id():
    v = os.environ.get('JARVIS_THREAD_ID')
    if v:
        return v
    try:
        env = Path(__file__).parent.parent / 'alerts' / '.env'
        for line in env.read_text(encoding='utf-8').splitlines():
            if line.startswith('JARVIS_THREAD_ID='):
                return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return '01a0d79b-fea4-71b1-9f2a-c02a9844b4ed'  # 容灾兑底（历史上曾为主线程）

JARVIS_THREAD_ID = _load_thread_id()

# ── 兼容旧代码（别名）────────────────────────────────────────────
JARVIS_TARGET  = f"{JARVIS_USER_ID}:t:{JARVIS_THREAD_ID}"
JARVIS_CHANNEL = 'jarvis'
