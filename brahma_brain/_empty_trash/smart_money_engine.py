"""smart_money_engine.py — shim → onchain_engine.py

INTERFACE CONTRACT:
  转发层，将 smart_money_engine 导入请求重定向至 onchain_engine.py
  所有公开函数与 onchain_engine 一致
"""
from brahma_brain.onchain_engine import *  # noqa: F401,F403
