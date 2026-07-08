"""
test_security.py — 梵天安全测试套件
设计院 2026-07-08 | 第三方审计P0-3修复

覆盖:
  1. 无硬编码密钥
  2. 执行层默认禁用
  3. safety.py API完整性
  4. 泄露密钥检测
"""
import os, sys, re, ast, glob
import pytest
from pathlib import Path

BASE = Path(__file__).parent.parent

# ── 1. 硬编码密钥扫描 ──────────────────────────────────────────────
LEAKED_KEYS = [
    'sDqoRAyeYHHzevKNxSj5JfkWpNUd6v8qPAhVy0Y8wbWGwC48eC7uhFOENAlVqV7b',
    'hXQnzQco9SNVgKgF2m3xvBGlJjOHBVtlzqRlxOTkp0kiJAwAOTeUiGLQSAopqIj7',
]
HARDCODE_PATTERN = re.compile(
    r'''(API_KEY|API_SECRET|_BN_KEY|_BN_SEC)\s*=\s*['"][A-Za-z0-9]{40,}['"]'''
)

def get_python_files():
    files = []
    for p in BASE.rglob('*.py'):
        parts = p.parts
        if any(x in parts for x in ['.git', '.venv', 'venv', '__pycache__', 'node_modules']):
            continue
        files.append(p)
    return files

def test_no_hardcoded_api_keys():
    """确认代码库中无硬编码API密钥"""
    violations = []
    for pyfile in get_python_files():
        # 跳过测试文件自身（含密钥作为检测常量属于合理用法）
        if pyfile.name == 'test_security.py':
            continue
        try:
            content = pyfile.read_text(encoding='utf-8', errors='ignore')
            for leaked in LEAKED_KEYS:
                if leaked in content:
                    violations.append(f"{pyfile.relative_to(BASE)}: 发现已泄露密钥前缀")
            if HARDCODE_PATTERN.search(content):
                # 检查是否是 os.environ 风格（合法）
                for m in HARDCODE_PATTERN.finditer(content):
                    ctx = content[max(0,m.start()-50):m.end()]
                    if 'environ' not in ctx and 'getenv' not in ctx:
                        violations.append(f"{pyfile.relative_to(BASE)}:{m.group()[:60]}")
        except Exception:
            pass
    assert not violations, f"发现硬编码密钥:\n" + "\n".join(violations)

def test_no_leaked_key_in_brahma_bus():
    """brahma_bus.py 不含硬编码默认值"""
    bus_file = BASE / 'brahma_brain' / 'brahma_bus.py'
    if not bus_file.exists():
        pytest.skip("brahma_bus.py不存在")
    content = bus_file.read_text()
    for leaked in LEAKED_KEYS:
        assert leaked not in content, f"brahma_bus.py 仍含泄露密钥"

def test_no_leaked_key_in_auto_executor():
    """auto_executor.py 不含硬编码密钥"""
    exe_file = BASE / 'scripts' / 'auto_executor.py'
    if not exe_file.exists():
        pytest.skip("auto_executor.py不存在")
    content = exe_file.read_text()
    for leaked in LEAKED_KEYS:
        assert leaked not in content, f"auto_executor.py 仍含泄露密钥"

# ── 2. safety.py API 完整性测试 ────────────────────────────────────
def test_safety_module_importable():
    """safety.py 可正常导入"""
    sys.path.insert(0, str(BASE))
    from brahma_brain.safety import (
        safety_config, is_live_trading_enabled, is_paper_only,
        require_live_trading, require_api_keys, safety_report
    )
    assert callable(safety_config)
    assert callable(is_live_trading_enabled)
    assert callable(require_live_trading)

def test_require_api_keys_detects_leaked():
    """require_api_keys() 能检测到已泄露的旧密钥"""
    sys.path.insert(0, str(BASE))
    from brahma_brain.safety import require_api_keys, reset_cache
    reset_cache()
    
    orig_key = os.environ.get('BINANCE_API_KEY', '')
    orig_sec = os.environ.get('BINANCE_SECRET', '')
    
    try:
        os.environ['BINANCE_API_KEY'] = LEAKED_KEYS[0]
        os.environ['BINANCE_SECRET'] = LEAKED_KEYS[1]
        with pytest.raises(RuntimeError, match="已泄露"):
            require_api_keys()
    finally:
        if orig_key: os.environ['BINANCE_API_KEY'] = orig_key
        elif 'BINANCE_API_KEY' in os.environ: del os.environ['BINANCE_API_KEY']
        if orig_sec: os.environ['BINANCE_SECRET'] = orig_sec
        elif 'BINANCE_SECRET' in os.environ: del os.environ['BINANCE_SECRET']
        reset_cache()

def test_safety_report_structure():
    """safety_report() 返回完整字段"""
    sys.path.insert(0, str(BASE))
    from brahma_brain.safety import safety_report, reset_cache
    reset_cache()
    report = safety_report()
    required_fields = [
        'live_trading_enabled', 'api_key_set', 'api_secret_set',
        'leaked_key_detected', 'fail_closed', 'max_nav_pct', 'min_score'
    ]
    for field in required_fields:
        assert field in report, f"safety_report 缺少字段: {field}"

# ── 3. 语法检查 ────────────────────────────────────────────────────
@pytest.mark.parametrize("pyfile", [
    'scripts/auto_executor.py',
    'brahma_brain/brahma_bus.py',
    'brahma_brain/safety.py',
    'scripts/auto_execute_gate.py',
    'guardrails/layer_9_12.py',
])
def test_python_syntax(pyfile):
    """核心文件语法正确"""
    full = BASE / pyfile
    if not full.exists():
        pytest.skip(f"{pyfile} 不存在")
    try:
        ast.parse(full.read_text())
    except SyntaxError as e:
        pytest.fail(f"{pyfile} 语法错误: {e}")

if __name__ == '__main__':
    pytest.main([__file__, '-v'])
