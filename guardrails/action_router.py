#!/usr/bin/env python3
"""
action_router.py — 梵天系统统一行动路由器
设计院 · 防御纵深框架 Layer 2
2026-05-28

核心思想：
  AI 不直接调用任何外部动作。
  AI 发出「意图 + 载荷」，action_router 决定是否执行、走哪条路径。

覆盖的三类操作：
  post_square  — 广场发帖
  send_dd1     — 钉钉1推送
  run_analysis — 梵天分析（禁止手写脚本）

每类操作有独立的 Guard 链，全部通过才执行。
任意 Guard 失败 → 拦截 + 告警 + 写入错误注册表。

使用：
  from guardrails.action_router import route

  # 广场发帖
  result = route('post_square', {'content': '...', 'broadcast': True})

  # 钉钉1推送
  result = route('send_dd1', {'text': '根据新浪财经...'})

  # 梵天分析
  result = route('run_analysis', {'symbol': 'ETH', 'brief': True})

  if result.ok:
      print('执行成功:', result.data)
  else:
      print('被拦截:', result.reason)
"""

import os
import re
import sys
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))

# ═══════════════════════════════════════════════════════
# 结果类
# ═══════════════════════════════════════════════════════

@dataclass
class RouteResult:
    ok: bool
    action: str
    reason: str = ''
    data: Any = None
    guard_failed: str = ''

    def __bool__(self):
        return self.ok


# ═══════════════════════════════════════════════════════
# Guard 基类 + 具体 Guards
# ═══════════════════════════════════════════════════════

class Guard:
    name: str = 'BaseGuard'

    def check(self, payload: dict) -> tuple:
        """返回 (ok: bool, reason: str)"""
        raise NotImplementedError


class DD1SignatureGuard(Guard):
    """
    拦截含钉钉1签名的内容进入广场路径。
    ERR-001 的代码级实现。
    """
    name = 'DD1SignatureGuard'
    SIGNATURE = '根据新浪财经公开数据'

    def check(self, payload: dict) -> tuple:
        content = payload.get('content', '') or payload.get('text', '')
        if self.SIGNATURE in content:
            return False, (
                f'内容含钉钉1专属签名「{self.SIGNATURE}」，'
                f'禁止走广场路径。请使用 route("send_dd1", ...) 走钉钉确认门。'
            )
        return True, ''


class BannedWordsGuard(Guard):
    """过滤广场禁词，命中则清洗后放行（不拦截，仅清洗）"""
    name = 'BannedWordsGuard'
    BANNED = [
        '梵天', '达摩院', 'brahma', '神级', '体制代码',
        '新浪财经', '打爆', '爆仓', '稳赚', '无风险',
        '一定涨', '一定跌', '内部', '私聊', '加我',
    ]

    def check(self, payload: dict) -> tuple:
        content = payload.get('content', '')
        hit = [w for w in self.BANNED if w.lower() in content.lower()]
        if hit:
            # 尝试自动清洗
            try:
                from news_formatter import clean as nf_clean
                payload['content'] = nf_clean(content)
                return True, f'禁词{hit}已自动清洗'
            except Exception:
                return False, f'内容含广场禁词 {hit}，且 news_formatter 不可用，拦截'
        return True, ''


class DD1FormatGuard(Guard):
    """验证钉钉1内容格式符合标准（调 dd1_guardian）"""
    name = 'DD1FormatGuard'

    def check(self, payload: dict) -> tuple:
        text = payload.get('text', '')
        if not text:
            return False, '钉钉1内容为空'
        try:
            from dd1_guardian import guard as _guard
            result = _guard(text)
            if result.ok:
                return True, ''
            return False, f'DD1格式不合格: {result.report()[:200]}'
        except ImportError:
            # guardian 不可用时，至少验证签名存在
            if '根据新浪财经公开数据' not in text:
                return False, 'DD1内容缺少「根据新浪财经公开数据」签名'
            return True, 'dd1_guardian不可用，基础格式通过'


class DD1ConfirmGateGuard(Guard):
    """确保钉钉1走确认门（入队，不直接发送）"""
    name = 'DD1ConfirmGateGuard'

    def check(self, payload: dict) -> tuple:
        # 如果 payload 里有 skip_confirm=True，拒绝
        if payload.get('skip_confirm'):
            return False, '钉钉1禁止跳过确认门（skip_confirm 无效）'
        return True, ''


class BrahmaRequiredGuard(Guard):
    """分析操作必须走梵天系统，禁止手写脚本"""
    name = 'BrahmaRequiredGuard'

    def check(self, payload: dict) -> tuple:
        # 检查是否有禁用的手写分析参数
        if payload.get('manual_script'):
            return False, '禁止手写分析脚本，必须使用梵天系统 brahma_analyze.py'
        symbol = payload.get('symbol', '')
        if not symbol:
            return False, '分析操作必须指定 symbol'
        # 检查 brahma_analyze.py 存在
        analyze_path = BASE / 'brahma_analyze.py'
        if not analyze_path.exists():
            return False, f'brahma_analyze.py 不存在: {analyze_path}'
        return True, ''


class RateLimitGuard(Guard):
    """简单频率控制：同一 action 60 秒内不重复触发（可配置）"""
    name = 'RateLimitGuard'
    _last_ts: Dict[str, float] = {}
    COOLDOWN = {
        'post_square': 30,   # 秒
        'send_dd1':    10,
        'run_analysis': 5,
    }

    def check(self, payload: dict) -> tuple:
        import time
        action = payload.get('_action', '')
        cooldown = self.COOLDOWN.get(action, 0)
        if cooldown == 0:
            return True, ''
        last = self._last_ts.get(action, 0)
        elapsed = time.time() - last
        if elapsed < cooldown:
            return False, f'{action} 频率限制：需等待 {cooldown - elapsed:.0f}s'
        self._last_ts[action] = time.time()
        return True, ''


# ═══════════════════════════════════════════════════════
# 执行器
# ═══════════════════════════════════════════════════════

def _exec_post_square(payload: dict) -> RouteResult:
    content   = payload.get('content', '')
    broadcast = payload.get('broadcast', True)
    try:
        sq_path = str(BASE.parent / 'scripts' / 'square')
        if sq_path not in sys.path:
            sys.path.insert(0, sq_path)
        from poster import post_to_square
        result = post_to_square(content, broadcast=broadcast)
        bc = result.get('broadcast_results', [])
        urls = [rv.get('url', '') for rv in bc if rv and rv.get('ok')]
        return RouteResult(ok=bool(urls), action='post_square', data={'urls': urls})
    except Exception as e:
        return RouteResult(ok=False, action='post_square', reason=str(e))


def _exec_send_dd1(payload: dict) -> RouteResult:
    text = payload.get('text', '')
    try:
        from push_hub import send_dd1
        ok = send_dd1(text)
        return RouteResult(ok=ok, action='send_dd1',
                           data={'queued': True, 'note': '已入队，等待888确认'})
    except Exception as e:
        return RouteResult(ok=False, action='send_dd1', reason=str(e))


def _exec_run_analysis(payload: dict) -> RouteResult:
    symbol = payload.get('symbol', '').upper().replace('USDT', '') + 'USDT'
    brief  = payload.get('brief', True)
    direction = payload.get('direction', '')
    try:
        cmd = ['python3', str(BASE / 'brahma_analyze.py'), symbol]
        if brief:
            cmd.append('--brief')
        if direction:
            cmd.append(direction.upper())
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = proc.stdout + proc.stderr
        return RouteResult(ok=proc.returncode == 0, action='run_analysis',
                           data={'output': output, 'symbol': symbol})
    except Exception as e:
        return RouteResult(ok=False, action='run_analysis', reason=str(e))


# ═══════════════════════════════════════════════════════
# 路由表
# ═══════════════════════════════════════════════════════

_rate_guard = RateLimitGuard()

ACTION_MAP: Dict[str, Dict] = {
    'post_square': {
        'guards':  [DD1SignatureGuard(), BannedWordsGuard(), _rate_guard],
        'executor': _exec_post_square,
        'desc':    '广场发帖',
    },
    'send_dd1': {
        'guards':  [DD1FormatGuard(), DD1ConfirmGateGuard()],
        'executor': _exec_send_dd1,
        'desc':    '钉钉1推送（需888确认）',
    },
    'run_analysis': {
        'guards':  [BrahmaRequiredGuard()],
        'executor': _exec_run_analysis,
        'desc':    '梵天系统分析',
    },
}


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════

def route(action: str, payload: dict, silent: bool = False) -> RouteResult:
    """
    统一行动路由器入口。

    Args:
        action:  操作类型 post_square / send_dd1 / run_analysis
        payload: 操作载荷（依 action 不同）
        silent:  True 时不打印日志

    Returns:
        RouteResult
    """
    if action not in ACTION_MAP:
        return RouteResult(ok=False, action=action,
                           reason=f'未知操作: {action}，支持: {list(ACTION_MAP.keys())}')

    entry = ACTION_MAP[action]
    guards: List[Guard] = entry['guards']
    executor: Callable  = entry['executor']

    # 注入 action 到 payload（供 RateLimitGuard 等使用）
    payload = dict(payload)
    payload['_action'] = action

    # 逐 Guard 检查
    for guard in guards:
        ok, reason = guard.check(payload)
        if not ok:
            if not silent:
                print(f'[ActionRouter] 🔴 {action} 被 {guard.name} 拦截: {reason}')
            _record_block(action, guard.name, reason, payload)
            return RouteResult(ok=False, action=action,
                               reason=reason, guard_failed=guard.name)
        elif reason and not silent:
            print(f'[ActionRouter] ⚠️ {guard.name}: {reason}')

    # 执行
    if not silent:
        print(f'[ActionRouter] ✅ {action} Guards 全通过，执行...')
    return executor(payload)


def _record_block(action: str, guard: str, reason: str, payload: dict):
    """被拦截时写日志（不强依赖 error_registry，避免循环）"""
    try:
        import time
        from datetime import datetime, timezone, timedelta
        bj = timezone(timedelta(hours=8))
        ts = datetime.now(bj).strftime('%Y-%m-%d %H:%M BJ')
        log_path = BASE / 'data' / 'action_router_blocks.jsonl'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            'ts': ts,
            'action': action,
            'guard': guard,
            'reason': reason[:200],
            'preview': str(payload.get('content') or payload.get('text') or '')[:80],
        }
        with open(log_path, 'a') as f:
            import json
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# CLI / 快速测试
# ═══════════════════════════════════════════════════════

if __name__ == '__main__':
    print('=== ActionRouter 自检 ===\n')

    # 测试1: DD1 内容应被拦截
    r1 = route('post_square', {
        'content': '根据新浪财经公开数据 ETH/USDT\n【ETH】箜 ▼\n  入场区: $2034'
    }, silent=False)
    print(f'Test1 DD1→广场 拦截: {"✅ PASS" if not r1.ok else "❌ FAIL"}\n')

    # 测试2: 正常广场内容应通过 Guards（不实际发帖）
    r2_payload = {'content': '今日ETH行情分析，现价$1982，关注支撑位$1950。'}
    ok2, _ = DD1SignatureGuard().check(r2_payload)
    ok3, _ = BannedWordsGuard().check(r2_payload)
    print(f'Test2 正常内容 Guards通过: {"✅ PASS" if ok2 and ok3 else "❌ FAIL"}\n')

    # 测试3: 梵天分析必须有 symbol
    r3 = route('run_analysis', {'symbol': ''}, silent=False)
    print(f'Test3 空symbol被拦截: {"✅ PASS" if not r3.ok else "❌ FAIL"}\n')

    # 测试4: 未知操作
    r4 = route('unknown_action', {}, silent=True)
    print(f'Test4 未知操作拦截: {"✅ PASS" if not r4.ok else "❌ FAIL"}\n')

    print('=== 自检完成 ===')


# ═══════════════════════════════════════════════════════
# 回归测试适配器（供 error_registry 调用）
# ═══════════════════════════════════════════════════════

def _test_banned_words_cleaned(test_input: str) -> bool:
    """
    ERR-003 回归测试：含禁词内容经过 BannedWordsGuard 后禁词被清洗。
    返回 True = 清洗成功（禁词已从内容中移除）= 应放行
    返回 False = 清洗失败（禁词仍存在）= 视为拦截
    """
    payload = {'content': test_input}
    guard = BannedWordsGuard()
    ok, reason = guard.check(payload)
    cleaned = payload.get('content', test_input)
    # 验证禁词已被清除
    still_has_banned = any(w.lower() in cleaned.lower() for w in guard.BANNED)
    if still_has_banned:
        return False  # 禁词未清除 = 失败
    return True   # 清洗成功 = 放行
