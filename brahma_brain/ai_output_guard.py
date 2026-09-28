"""
ai_output_guard.py — L1数字禁令 + AI面输出校验器（AI Truth Architecture P1）
[2026-09-28 苏摩111] 审计报告: reports/ai_truth_audit_20260928.md §四 L1

宪法原则：**梵天的数字只能由确定性代码产生。**
LLM在系统中的两个合法角色：门禁参谋（三值verdict）/ 文案叙述（无数字）。
LLM输出中出现任何数字token = 违约 → 拒收/降级处理 + 隔离取证（append-only）。

表面类型：
  gate          封闭词汇（PASS/WARN/BLOCK）     → 违约=fail-closed降权
  closed_vocab  封闭词汇（合理/异常、支持/质疑） → 违约=按保守值处理
  review_text   定性文本（教训/审核note）       → 违约=隔离，消费方拒收（SFT/推送跳过）

接入位置：
  - scripts/signal_settler.py     learning_log写入前（lesson守卫+pending_ic标记）
  - scripts/wr_feedback_engine.py P2-1 LLM审核响应（合理/异常封闭词汇）
  - scripts/regime_switch_monitor.py P2-2 LLM审核note（推送前数字禁令）
  - scripts/build_sft_dataset.py  build_from_learning_log（只吃APPROVE教训）
  - brahma_brain/ic_tracker.py    review_pending_lessons（L3 IC门裁决表）
  - brahma_brain/brahma_smoke_test.py T15（守卫探针）
"""
import json, re, time
from pathlib import Path

BASE = Path(__file__).parent.parent
QUARANTINE_PATH = BASE / 'data' / 'ai_guard_quarantine.jsonl'
REGISTRY_PATH   = BASE / 'data' / 'ai_surfaces_registry.json'

# 数字token：金额/价格/百分比/任何整数浮点（定性面禁一切数字）
_NUM_TOKEN = re.compile(r'\$?\d+(?:[,.]\d+)*(?:\.\d+)?\s*%?')

# ── AI面注册SSOT（8面全景 · 审计报告§一）──────────────────────────
SURFACES = {
    's25_reasoning_gate': {'module': 'brahma_brain/reasoning_client.py', 'role': 'gate',
        'vocab': ['PASS', 'WARN', 'BLOCK'], 'numbers_allowed': False, 'wired': True,
        'note': 'fail-closed WARN (commit 488941ed)'},
    'settler_lesson': {'module': 'scripts/signal_settler.py', 'role': 'review_text',
        'numbers_allowed': False, 'wired': True,
        'note': 'L1数字禁令+L3 pending_ic标记'},
    'wr_feedback_review': {'module': 'scripts/wr_feedback_engine.py', 'role': 'closed_vocab',
        'vocab': ['合理', '异常'], 'numbers_allowed': False, 'wired': True,
        'note': '违约=按异常旗标处理（保守）'},
    'regime_llm_review': {'module': 'scripts/regime_switch_monitor.py', 'role': 'closed_vocab',
        'vocab': ['支持', '质疑'], 'numbers_allowed': False, 'wired': True,
        'note': '违约=丢弃AI note（确定性推送不受影响）'},
    'council_shadow': {'module': 'brahma_brain/llm_council_bridge.py', 'role': 'shadow',
        'numbers_allowed': False, 'wired': False,
        'note': 'P2: 影子停烧配额'},
    'battlefield_push': {'module': 'scripts/battlefield_auto_analysis.py', 'role': 'push_text',
        'numbers_allowed': True, 'wired': False,
        'note': 'P2水印：推送数字来自确定性模板，LLM叙述无数字权限'},
    'square_post': {'module': 'scripts/square_auto_post.py', 'role': 'public_post',
        'numbers_allowed': True, 'wired': False,
        'note': 'P2水印；模板引擎已0LLM（square_template.py）'},
    'daily_review': {'module': 'scripts/daily_review_llm.py', 'role': 'report',
        'numbers_allowed': True, 'wired': False, 'note': 'P2水印'},
}


def register_surfaces() -> dict:
    """AI面SSOT清单落盘（幂等）。审计/守门可读，接线状态一目了然。"""
    payload = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'principle': '梵天的数字只能由确定性代码产生；LLM输出含数字=违约',
        'surfaces': SURFACES,
    }
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(REGISTRY_PATH)
    return payload


def scan_numbers(text: str) -> list:
    """提取文本中的数字token（去重保序）。定性面LLM输出不允许任何数字。"""
    return _NUM_TOKEN.findall(str(text or ''))


def guard_text(surface: str, text: str) -> tuple:
    """定性文本守卫（L1）。返回 (ok, reason, violations)。
    违约→隔离取证（append-only jsonl）。"""
    viol = scan_numbers(text)
    if viol:
        _quarantine(surface, text, viol, '数字禁令:LLM输出含数字')
        return False, f'数字违约: {viol[:3]}', viol
    return True, '', []


def guard_closed_vocab(surface: str, text: str, allowed: tuple) -> tuple:
    """封闭词汇守卫（gate类）。文本必须以白名单词开头且无数字。
    违约→隔离取证。返回 (ok, reason, violations)。"""
    t = str(text or '').strip()
    viol = scan_numbers(t)
    if viol:
        _quarantine(surface, t, viol, f'数字禁令+封闭词汇({"/".join(allowed)})')
        return False, f'数字违约: {viol[:3]}', viol
    if not any(t.startswith(w) for w in allowed):
        _quarantine(surface, t, [], f'封闭词汇违约: 首2字"{t[:2]}"不在{list(allowed)}')
        return False, f'词汇违约: "{t[:12]}"', []
    return True, '', []


def _quarantine(surface: str, text: str, violations: list, reason: str) -> None:
    """隔离取证：append-only，永不静默丢弃（为审计留痕）。"""
    try:
        QUARANTINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            'ts': time.time(),
            'ts_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'surface': surface,
            'reason': reason,
            'violations': violations[:5],
            'text': str(text)[:200],
        }
        with open(QUARANTINE_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as _e:
        import sys
        print(f'[WARN] ai_output_guard: {_e}', file=sys.stderr)
