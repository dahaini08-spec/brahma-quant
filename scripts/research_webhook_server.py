"""
research_webhook_server.py · QuantDinger 研究信号 Webhook 接收服务 v1.0
=====================================================================
设计院 · 2026-06-17

职责：
  接收 QuantDinger 推送的研究信号 JSON
  → 调用 research_bridge.inject_research_signal()
  → 写入 external_signal 缓存
  → brahma_core 下次评分时读取注入

API：
  POST /research/signal  — 接收研究信号
  GET  /health           — 健康检查
  GET  /stats            — 注入统计
  POST /control/dry_run  — 切换 dry_run 模式（需要 ADMIN_TOKEN）

安全原则（STAR.md L0）：
  - 仅接受来自 quant_research_net 的请求（Docker 网络隔离保证）
  - ADMIN_TOKEN 保护 dry_run 切换（防止误激活实盘注入）
  - 所有注入有审计日志（research_bridge_log.jsonl）
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# 路径
sys.path.insert(0, '/app/trading-system')
sys.path.insert(0, '/app/trading-system/brahma_brain')

from research_bridge import inject_research_signal, get_current_regime, get_bridge_stats

app = Flask(__name__)
logging.basicConfig(level=os.environ.get('BRIDGE_LOG_LEVEL', 'INFO'))
logger = logging.getLogger('research_webhook')

# 配置
DRY_RUN     = os.environ.get('BRIDGE_DRY_RUN', 'true').lower() == 'true'
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', 'xingshu_admin_2026')


@app.route('/health')
def health():
    return jsonify({
        'status':    'ok',
        'dry_run':   DRY_RUN,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    })


@app.route('/stats')
def stats():
    return jsonify({
        'bridge': get_bridge_stats(),
        'dry_run': DRY_RUN,
    })


@app.route('/research/signal', methods=['POST'])
def receive_signal():
    """接收研究信号主入口"""
    global DRY_RUN

    try:
        signal = request.get_json(force=True)
        if not signal:
            return jsonify({'error': 'empty body'}), 400

        # 获取当前体制
        regime = get_current_regime()

        # 调用 research_bridge 注入（STAR.md 所有门控在此执行）
        result = inject_research_signal(signal, regime, dry_run=DRY_RUN)

        logger.info(f"[webhook] {signal.get('symbol','')} {signal.get('direction','')} "
                    f"injected={result['injected']} score={result['score_delta']} "
                    f"reason={result['reason']}")

        return jsonify({
            'received': True,
            'regime':   regime,
            'result':   result,
        })

    except Exception as e:
        logger.error(f'[webhook] 处理异常: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/control/dry_run', methods=['POST'])
def toggle_dry_run():
    """切换 dry_run 模式（需要 ADMIN_TOKEN）"""
    global DRY_RUN
    token = request.headers.get('X-Admin-Token', '')
    if token != ADMIN_TOKEN:
        return jsonify({'error': 'unauthorized'}), 401

    data = request.get_json(force=True) or {}
    new_val = bool(data.get('dry_run', True))
    old_val = DRY_RUN
    DRY_RUN = new_val

    logger.warning(f'[webhook] dry_run 切换: {old_val} → {new_val}  '
                   f'(by token={token[:8]}...)')

    return jsonify({
        'dry_run': DRY_RUN,
        'message': f'dry_run {"开启" if DRY_RUN else "⚠️ 已关闭，注入已激活"}',
    })


if __name__ == '__main__':
    logger.info(f'Research Webhook 启动: dry_run={DRY_RUN}')
    app.run(host='0.0.0.0', port=8890, debug=False)
