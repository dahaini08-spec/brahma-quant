#!/usr/bin/env python3
"""
ai4trade_publisher.py — 梵天信号自动发布到 AI-Trader 平台
[封印 2026-08-29 苏摩111]

功能：
  梵天 valid=True 信号 → 自动发布到 ai4trade.ai 平台
  建立公开信号记录，积累历史胜率展示

接入位置：
  brahma_analysis_runner.py run_analysis() 末尾（valid=True时调用）
  或 cron 独立调用最新信号

API文档：https://ai4trade.ai/SKILL.md
"""

import os, sys, json, requests, logging
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger('ai4trade_publisher')

# ── 凭证 ────────────────────────────────────────────────────
AI4TRADE_TOKEN  = os.environ.get('AI4TRADE_TOKEN', 'Kt7hRUKwygRXvH7cM1HsLfXPAhN33k7mJ1V1s580lb0')
AI4TRADE_AGENT_ID = int(os.environ.get('AI4TRADE_AGENT_ID', '23904'))
BASE_URL        = 'https://ai4trade.ai/api'
HEADERS         = {'Authorization': f'Bearer {AI4TRADE_TOKEN}', 'Content-Type': 'application/json'}

# ── 发布记录（防重复） ────────────────────────────────────
_PUBLISH_LOG = Path(__file__).parent.parent / 'data' / 'ai4trade_publish_log.jsonl'

def _already_published(signal_key: str) -> bool:
    """检查信号是否已发布（防重复）"""
    if not _PUBLISH_LOG.exists():
        return False
    for line in _PUBLISH_LOG.read_text().splitlines():
        try:
            if json.loads(line).get('signal_key') == signal_key:
                return True
        except Exception:
            pass
    return False

def _log_publish(signal_key: str, signal_id: int, symbol: str):
    """记录已发布的信号"""
    with open(_PUBLISH_LOG, 'a') as f:
        f.write(json.dumps({
            'signal_key': signal_key,
            'signal_id': signal_id,
            'symbol': symbol,
            'ts': datetime.now(timezone.utc).isoformat()
        }) + '\n')


def publish_signal(result: dict) -> dict:
    """
    把梵天 valid=True 信号发布到 AI-Trader 平台

    Args:
        result: brahma_analysis_runner.run_analysis() 的完整返回值

    Returns:
        {'success': bool, 'signal_id': int, 'url': str}
    """
    symbol    = result.get('symbol', '')
    direction = result.get('direction', result.get('signal_dir', ''))
    score     = result.get('score_final', result.get('score', 0))
    regime    = result.get('regime', '')
    price     = result.get('price', 0)
    sl_pct    = result.get('sl_pct', 2.0)
    rr        = result.get('rr1', 0)
    tp1       = result.get('tp1', 0)
    grade     = result.get('grade', 0)
    valid     = result.get('valid_signal', False)

    if not valid:
        return {'success': False, 'reason': 'signal not valid'}

    # 生成唯一key防重复
    ts_hour = datetime.now(timezone.utc).strftime('%Y%m%d%H')
    signal_key = f'{symbol}_{direction}_{ts_hour}'
    if _already_published(signal_key):
        return {'success': False, 'reason': 'already_published'}

    # 构建发布内容（梵天VIP格式）
    side = 'long' if direction == 'LONG' else 'short'
    entry_price = float(price)
    sl_price = entry_price * (1 - sl_pct/100) if side == 'long' else entry_price * (1 + sl_pct/100)
    tp_price = float(tp1) if tp1 else 0

    content = (
        f"🔱 梵天量化信号 | {symbol} {direction}\n"
        f"体制: {regime} | score={score:.0f} | grade={grade}\n"
        f"入场: ${entry_price:,.2f}\n"
        f"止损: ${sl_price:,.2f} (SL={sl_pct:.1f}%)\n"
        f"止盈: ${tp_price:,.2f} (RR={rr:.2f})\n"
        f"─────────────────\n"
        f"梵天35维量化矩阵 | WR矩阵铁证驱动\n"
        f"price_ts: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

    payload = {
        'action':      'buy' if side == 'long' else 'short',
        'symbol':      symbol.replace('USDT', ''),
        'market':      'crypto',
        'price':       entry_price,
        'quantity':    0.01,
        'executed_at': datetime.now(timezone.utc).isoformat(),
        'content':     content,
    }

    try:
        # 拉取 tradesync skill
        resp = requests.post(
            f'{BASE_URL}/signals/realtime',
            headers=HEADERS,
            json=payload,
            timeout=10
        )
        data = resp.json()
        if data.get('success') or data.get('id'):
            sig_id = data.get('id', data.get('signal_id', 0))
            _log_publish(signal_key, sig_id, symbol)
            logger.info(f'[AI4Trade] 发布成功 {symbol} {direction} id={sig_id}')
            return {
                'success': True,
                'signal_id': sig_id,
                'url': f'https://ai4trade.ai/signals/{sig_id}'
            }
        else:
            logger.warning(f'[AI4Trade] 发布失败: {data}')
            return {'success': False, 'reason': str(data)[:100]}
    except Exception as e:
        logger.error(f'[AI4Trade] 异常: {e}')
        return {'success': False, 'reason': str(e)[:100]}


def get_my_performance() -> dict:
    """查看梵天在AI-Trader的历史胜率"""
    try:
        resp = requests.get(f'{BASE_URL}/signals/{AI4TRADE_AGENT_ID}', headers=HEADERS, timeout=10)
        return resp.json()
    except Exception as e:
        return {'error': str(e)}


if __name__ == '__main__':
    # 测试发布
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))

    from brahma_brain.brahma_full_report import run_full_analysis
    print('跑BTC分析...')
    report, r = run_full_analysis('BTCUSDT')
    print(f'valid={r.get("valid_signal")} score={r.get("score",0):.0f}')

    # 强制valid=True测试发布
    r['valid_signal'] = True
    r['symbol'] = 'BTCUSDT'
    r['direction'] = r.get('signal_dir', 'LONG')
    result = publish_signal(r)
    print('发布结果:', result)
