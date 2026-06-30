#!/bin/bash
# 梵天系统自动修复启动脚本
# 每次容器重启后自动运行

WORK_DIR="/root/.openclaw/workspace/trading-system"
LOG="/tmp/brahma_startup.log"

echo "[$(date)] 梵天启动脚本开始..." >> $LOG

# 1. 安装核心依赖
pip install tornado requests numpy pandas websocket-client -q --break-system-packages >> $LOG 2>&1
echo "[$(date)] ✅ 依赖安装完成" >> $LOG

# 2. 修复 libgomp（LightGBM依赖）
GOMP=$(find /usr/local/lib -name 'libgomp-*.so.1.0.0' 2>/dev/null | head -1)
if [ -n "$GOMP" ] && [ ! -f /usr/lib/libgomp.so.1 ]; then
    cp "$GOMP" /usr/lib/libgomp.so.1 && ldconfig 2>/dev/null
    echo "[$(date)] ✅ libgomp修复完成" >> $LOG
fi

# 3. 启动Dashboard
pkill -f brahma_dashboard_server 2>/dev/null; sleep 1
cd $WORK_DIR
mkdir -p logs
nohup python3 scripts/brahma_dashboard_server.py --port 7777 >> logs/dashboard.log 2>&1 &
DASH_PID=$!
echo "[$(date)] ✅ Dashboard PID=$DASH_PID" >> $LOG
sleep 5

# 验证Dashboard是否启动成功
HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:7777/ 2>/dev/null)
if [ "$HTTP" != "200" ]; then
    pip install tornado -q --break-system-packages >> $LOG 2>&1
    nohup python3 scripts/brahma_dashboard_server.py --port 7777 >> logs/dashboard.log 2>&1 &
    sleep 5
fi

# 4. 启动Cloudflare隧道
pkill -f cloudflared 2>/dev/null; sleep 1
nohup /root/.openclaw/workspace/scripts/cloudflared tunnel --url http://localhost:7777 --no-autoupdate > /tmp/cf_dash.log 2>&1 &
echo "[$(date)] ✅ Cloudflare隧道启动" >> $LOG

# 5. 等待URL生成并推送通知
sleep 12
NEW_URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cf_dash.log | tail -1)

if [ -n "$NEW_URL" ]; then
    echo "[$(date)] ✅ URL: $NEW_URL" >> $LOG
    python3 $WORK_DIR/scripts/notify_jarvis.py "🔄 梵天系统已重启

📡 最新网址：
🌐 订阅者：$NEW_URL
👨‍💻 开发者：$NEW_URL/pro

✅ Dashboard运行正常"
else
    python3 $WORK_DIR/scripts/notify_jarvis.py "⚠️ 梵天重启但隧道URL获取失败，请手动检查"
fi

echo "[$(date)] 启动脚本完成" >> $LOG
