"""
brahma_gateway.py — 梵天统一入口网关
══════════════════════════════════════
设计院 2026-08-25 苏摩111 封印

使命：
  苏摩发任何消息 → 意图识别 → 路由到正确流水线
  解决「三个世界互不相知」根因：
    世界1: 梵天大脑(brahma_core)
    世界2: 对话框(苏摩手工分析)
    世界3: 币安AI Pro(binance-pro-cli)

路由规则：
  ANALYZE  → brahma_pipeline.run_full_pipeline(symbol)  [6步强制]
  EXECUTE  → auto_executor 执行流水线
  PORTFOLIO→ binance-cli 账户资产查询
  SCAN     → brahma_scan_all 全市场扫描
  GENERAL  → 普通对话（不走梵天）

调用方式：
  from brahma_gateway import handle
  reply = handle("分析BTC")
  reply = handle("现在有什么机会")
  reply = handle("我的持仓")
"""

import os
import sys
import re

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)
sys.path.insert(0, os.path.join(_BASE, '..', 'scripts'))

# ── 意图分类规则（纯规则，0 tokens）────────────────────────────────
# 格式: (intent, patterns...)
_INTENT_RULES = [
    ('ANALYZE', [
        r'分析\s*([A-Za-z]+)',
        r'([A-Za-z]+)\s*(怎么样|如何|现在|行情|走势)',
        r'([A-Za-z]+)\s*(做多|做空|多|空)',
        r'梵天\s*分析',
        r'全能力\s*分析',
        r'看\s*([A-Za-z]+)',
        r'([A-Za-z]+)\s*USDT',
        r'BTC|ETH|SOL|BNB',
    ]),
    ('EXECUTE', [
        r'^执行$',
        r'^111$',
        r'立即执行',
        r'下单',
    ]),
    ('PORTFOLIO', [
        r'持仓|仓位|余额|资产|账户|NAV',
        r'我.*持有',
        r'现在.*仓',
    ]),
    ('SCAN', [
        r'扫描|机会|信号|有什么.*机会|全市场',
        r'哪.*币.*好|推荐.*币',
    ]),
]

# ── 提取币种 ─────────────────────────────────────────────────────────
_KNOWN_SYMBOLS = [
    'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX',
    'MATIC', 'DOT', 'LINK', 'UNI', 'ATOM', 'LTC', 'ETC',
    'TRUMP', 'PEPE', 'WIF', 'BONK', 'SHIB',
]


def extract_symbol(text: str) -> str:
    """从消息中提取币种，返回XXXUSDT格式"""
    text_upper = text.upper()
    # 精确匹配已知币种
    for sym in _KNOWN_SYMBOLS:
        if sym in text_upper:
            return sym + 'USDT'
    # 正则匹配 XXXUSDT 格式
    m = re.search(r'\b([A-Z]{2,10})USDT\b', text_upper)
    if m:
        return m.group(0)
    # 正则匹配独立英文词
    m = re.search(r'\b([A-Z]{2,8})\b', text_upper)
    if m and m.group(1) not in ('AI', 'VIP', 'OK', 'NO'):
        return m.group(1) + 'USDT'
    # 默认BTC
    return 'BTCUSDT'


def classify_intent(text: str) -> tuple:
    """
    纯规则意图分类，0 tokens。
    返回 (intent, symbol_or_None)
    """
    for intent, patterns in _INTENT_RULES:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                symbol = extract_symbol(text) if intent == 'ANALYZE' else None
                return intent, symbol
    return 'GENERAL', None


# ── 各意图处理器 ──────────────────────────────────────────────────────
def _handle_analyze(symbol: str) -> str:
    """强制6步全能力分析流水线"""
    try:
        from brahma_pipeline import run_full_pipeline
        return run_full_pipeline(symbol)
    except Exception as e:
        return f'❌ 梵天分析流水线异常: {e}\n请检查brahma_pipeline.py'


def _handle_execute() -> str:
    """触发auto_executor执行流水线"""
    try:
        import subprocess, sys as _sys
        result = subprocess.run(
            [_sys.executable,
             os.path.join(_BASE, '..', 'scripts', 'auto_executor.py'),
             '--dry-run'],
            capture_output=True, text=True, timeout=30
        )
        return f'🟢 执行流水线触发:\n{result.stdout[-500:]}' if result.stdout else '执行流水线已触发'
    except Exception as e:
        return f'❌ 执行流水线异常: {e}'


def _handle_portfolio() -> str:
    """账户资产查询（通过binance-cli）"""
    return (
        '📊 **持仓查询**\n\n'
        '请使用 `binance-cli` 查询账户资产：\n'
        '- 合约持仓: `binance-cli futures positions`\n'
        '- 账户余额: `binance-cli account balance`\n\n'
        '或直接说 "查我的合约持仓" 我来帮你跑。'
    )


def _handle_scan() -> str:
    """触发全市场扫描"""
    try:
        import subprocess, sys as _sys
        result = subprocess.run(
            [_sys.executable,
             os.path.join(_BASE, '..', 'scripts', 'brahma_scan_all.py'),
             '--top', '5'],
            capture_output=True, text=True, timeout=60
        )
        out = result.stdout[-800:] if result.stdout else '扫描无输出'
        return f'🔍 **梵天全市场扫描结果**:\n\n{out}'
    except Exception as e:
        return f'❌ 全市场扫描异常: {e}'


# ── 主入口 ────────────────────────────────────────────────────────────
def handle(user_message: str) -> str:
    """
    梵天统一网关入口。
    苏摩任何消息都应先经过这里，不再手工乱输出。
    """
    intent, symbol = classify_intent(user_message)

    if intent == 'ANALYZE':
        return _handle_analyze(symbol)
    elif intent == 'EXECUTE':
        return _handle_execute()
    elif intent == 'PORTFOLIO':
        return _handle_portfolio()
    elif intent == 'SCAN':
        return _handle_scan()
    else:
        # GENERAL：不强制走梵天，正常对话
        return None  # None = 由AI正常回答


# ── CLI 测试 ──────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    msg = ' '.join(sys.argv[1:]) if len(sys.argv) > 1 else '分析BTC'
    intent, symbol = classify_intent(msg)
    print(f'意图: {intent} | 币种: {symbol}')
    result = handle(msg)
    if result:
        print(result)
    else:
        print('(GENERAL对话，由AI正常处理)')
