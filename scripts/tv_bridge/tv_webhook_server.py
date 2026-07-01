"""
tv_webhook_server.py — TradingView Premium Webhook 接收服务
设计院·梵天 2026-06-30

启动: python3 tv_webhook_server.py
监听: 0.0.0.0:5678
TV端点: POST http://8.209.208.186:5678/tv
健康检查: GET http://8.209.208.186:5678/health

TV Pine Script Alert URL格式:
  URL: http://8.209.208.186:5678/tv
  Token header: X-TV-Token: brahma_tv_2026
  Body (JSON):
    {
      "symbol": "{{ticker}}",
      "type": "LIQ_LEVEL",
      "price": {{close}},
      "timeframe": "{{interval}}",
      "note": "你的备注"
    }
"""

import json, sys, os, subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

BASE    = Path(__file__).parent.parent.parent
TV_AUTH = "brahma_tv_2026"    # TV Alert配置中的token

sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE))

try:
    from scripts.tv_bridge.tv_signal_handler import process_tv_signal, format_tv_enhancement
    HANDLER_OK = True
except Exception as e:
    print(f"[TV-Server] ⚠ handler导入失败: {e}")
    HANDLER_OK = False

# Jarvis推送配置
try:
    sys.path.insert(0, str(BASE / 'scripts'))
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    JARVIS_TARGET = f"{JARVIS_USER_ID}:t:{JARVIS_THREAD_ID}"
except Exception:
    JARVIS_TARGET = None


def push_to_jarvis(message: str):
    """推送TV信号通知到苏摩主线程"""
    if not JARVIS_TARGET:
        return
    try:
        subprocess.run([
            'openclaw', 'message', 'send',
            '--channel', 'jarvis',
            '--to', JARVIS_TARGET,
            '--message', message,
        ], capture_output=True, timeout=10)
    except Exception:
        pass


class TVWebhookHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == '/health':
            self._ok({'status': 'ok', 'service': 'brahma-tv-bridge', 'handler': HANDLER_OK})
        else:
            self._err(404, 'not found')

    def do_POST(self):
        if self.path != '/tv':
            self._err(404, 'not found')
            return

        # Token验证
        auth = self.headers.get('X-TV-Token', '') or self.headers.get('Authorization', '')
        if TV_AUTH not in auth:
            self._err(401, 'unauthorized')
            print(f"[TV-Server] ❌ 未授权请求 from {self.client_address[0]}")
            return

        # 读取body
        length  = int(self.headers.get('Content-Length', 0))
        raw     = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception:
            self._err(400, 'invalid json')
            return

        print(f"[TV-Server] 📡 收到信号: {payload}")

        # 处理信号
        if HANDLER_OK:
            result = process_tv_signal(payload)
        else:
            result = {'ok': False, 'error': 'handler not loaded'}

        # 推送Jarvis通知（P1重要信号才推）
        sig_type = payload.get('type', '')
        price    = payload.get('price', 0)
        sym      = payload.get('symbol', '')
        tf       = payload.get('timeframe', '')
        note     = payload.get('note', '')

        important = sig_type in ('LIQ_LEVEL', 'OB_SIGNAL', 'STRUCTURE', 'ALERT_CROSS')
        if important and result.get('ok'):
            push_to_jarvis(
                f"📡 TV Premium信号\n"
                f"  {sym} {sig_type} ${float(price):,.2f} @{tf}\n"
                f"  {note}\n"
                f"  → 已写入brahma_bus缓存，下次分析自动增强"
            )

        self._ok({'ok': True, 'processed': result})

    def _ok(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        body = json.dumps({'error': msg}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[TV-Server] {self.client_address[0]} - {fmt % args}")


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5678
    server = HTTPServer(('0.0.0.0', port), TVWebhookHandler)
    print(f"[TV-Server] ✅ 启动成功")
    print(f"[TV-Server] 监听: 0.0.0.0:{port}")
    print(f"[TV-Server] TV端点: POST http://8.209.208.186:{port}/tv")
    print(f"[TV-Server] 健康检查: GET http://8.209.208.186:{port}/health")
    print(f"[TV-Server] Token: {TV_AUTH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[TV-Server] 停止")
