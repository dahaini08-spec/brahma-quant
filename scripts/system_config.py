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
JARVIS_THREAD_ID = os.environ.get('JARVIS_THREAD_ID', '019f309c-609b-7a75-a195-e221e5927c63')

# ── 兼容旧代码（别名）────────────────────────────────────────────
JARVIS_TARGET  = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
JARVIS_CHANNEL = 'jarvis'
