#!/bin/bash
# 梵天系统 Watchdog - 每分钟检查并恢复服务
# Gateway重启后自动拉起后端+tunnel，并推送新URL到Jarvis

LOG="/tmp/watchdog.log"
WORK_DIR="/root/.openclaw/workspace/trading-system"
JARVIS_THREAD_ID="019f309c-609b-7a75-a195-e221e5927c63"
SENDER_ID="73295708"
OPENCLAW_CHANNEL="jarvis"

log() { echo "[$(date '+%H:%M:%S')] $1" >> "$LOG"; }

# ── 1. 后端守护 ──
if ! python3 -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',7777));s.close();exit(r)" 2>/dev/null; then
    log "后端离线，重启..."
    pip install tornado requests --break-system-packages -q > /dev/null 2>&1
    nohup python3 "$WORK_DIR/scripts/brahma_dashboard_server.py" --port 7777 >> /tmp/dash.log 2>&1 &
    sleep 5
    if python3 -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',7777));s.close();exit(r)" 2>/dev/null; then
        log "后端恢复 ✅"
    else
        log "后端重启失败 ❌"
    fi
fi

# ── 2. Tunnel守护 ──
if ! ps aux | grep -q "[c]loudflared"; then
    log "Tunnel离线，重启..."
    > /tmp/tunnel_new.log
    /tmp/cloudflared tunnel --url http://localhost:7777 >> /tmp/tunnel_new.log 2>&1 &
    sleep 15
    
    NEW_URL=$(grep "https.*trycloudflare" /tmp/tunnel_new.log | grep INF | tail -1 | grep -oP 'https://[^\s|]+')
    if [ -n "$NEW_URL" ]; then
        cat /tmp/tunnel_new.log >> /tmp/tunnel.log
        log "Tunnel恢复: $NEW_URL"
        
        # 保存URL供后续查询
        echo "$NEW_URL" > /tmp/current_tunnel_url.txt
        
        # 推送新URL到Jarvis
        MSG="🔄 梵天系统自动恢复
━━━━━━━━━━━━━━━
✅ 后端服务：在线
✅ Tunnel已重建

🔗 订阅者：${NEW_URL}
👨‍💻 开发者：${NEW_URL}/pro

(Gateway重启后自动恢复)"
        openclaw message send \
            --channel "$OPENCLAW_CHANNEL" \
            --to "${SENDER_ID}:thread:${JARVIS_THREAD_ID}" \
            --message "$MSG" 2>/dev/null || log "推送失败"
    else
        log "Tunnel重启失败 ❌"
    fi
fi
