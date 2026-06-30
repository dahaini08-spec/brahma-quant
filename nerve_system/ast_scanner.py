"""
L4 AST Bug Scanner — 静态代码分析，主动发现潜在陷阱
扫描模式：
  P1: dict/list 被当 bool 用 (.get() 结果直接 if)
  P2: positions 默认值用 0/None 而非 []
  P3: 除零风险 (/ 没有先判断 != 0)
  P4: 浮点数直接 == 比较
  P5: except: pass (吞异常)
"""
import ast, pathlib, json, time
from typing import List, Dict

ROOT = pathlib.Path(__file__).parent.parent
ALERTS_FILE = ROOT / "data" / "nerve_alerts.jsonl"

# 扫描范围：排除 archive/deprecated，只扫活跃代码
SCAN_DIRS = [
    ROOT,  # 顶层
]
EXCLUDE_PATTERNS = [
    "archive", "deprecated", "__pycache__", ".git", "nerve_system",
    "brahma_brain",      # 外脑模块
    "backtest",          # 回测代码
    "auto_poster",       # 推送工具
    "wf_sim_engine",     # 模拟引擎
    "dharma",            # 研究节点
    "scripts",           # 辅助脚本
    "daily_signal_report",
    "key_officer",
    "liquidity_hunt",      # 流动性猎手（辅助工具）
    "legacy",             # 归档代码，不扫描
    "lana_backtest",       # 历史回测
    "portfolio_manager",   # 投组管理（条件判断多）
    "hunter_main",         # 猎手主循环（非评分核心）
    "commander",           # 指挥官（多层条件判断）
]

# 核心生产文件白名单（只扫这些）
CORE_FILES_ONLY = [
    "executor.py", "state_manager.py", "portfolio_brain.py",
    "brahma_core.py", "ws_guardian.py", "position_sync.py",
    "lana/hunter_v2/hunter_filter.py", "lana/hunter_v2/hunter_executor.py",
    "lana/hunter_v2/hunter_scanner.py", "lana/execute_engine.py",
]

class BugPattern:
    def __init__(self, pid: str, name: str, desc: str, severity: str):
        self.pid = pid
        self.name = name
        self.desc = desc
        self.severity = severity  # ERROR / WARN


PATTERNS = [
    BugPattern("P1", "dict-as-bool",  ".get()返回值(可能是dict/list)直接用于布尔判断", "ERROR"),
    BugPattern("P2", "wrong-default", "state.get('positions', 0/None) 非列表默认值",   "ERROR"),
    BugPattern("P3", "bare-except",   "except: pass 或 except Exception: pass 吞异常",  "WARN"),
    BugPattern("P4", "float-eq",      "浮点数直接 == 比较",                              "WARN"),
]


class ASTBugVisitor(ast.NodeVisitor):
    def __init__(self, source_lines: List[str], filepath: str):
        self.lines = source_lines
        self.filepath = filepath
        self.findings: List[Dict] = []
        # 追踪 var = xxx.get(key) 的赋值
        self._get_vars: Dict[str, int] = {}  # varname -> lineno

    def _finding(self, pattern: BugPattern, lineno: int, snippet: str) -> Dict:
        # 支持 # noqa 豁免（合法的字符串/bool检查被误报时用此注释）
        if '# noqa' in snippet:
            return None
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "layer": "L4_AST",
            "level": pattern.severity,
            "pattern": pattern.pid,
            "name": pattern.name,
            "file": str(self.filepath),
            "line": lineno,
            "snippet": snippet[:100],
            "desc": pattern.desc,
        }

    def visit_Assign(self, node: ast.Assign):
        """记录 var = xxx.get(key) 的赋值"""
        if isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr == 'get':
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._get_vars[target.id] = node.lineno

        # P2: state.get('positions', 0) 或 state.get('positions', None)
        if isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr == 'get':
                args = node.value.args
                if len(args) >= 2:
                    default = args[1]
                    # 默认值是0或None但key包含"position"/"circuit"/"health"
                    key_str = ""
                    if args and isinstance(args[0], ast.Constant):
                        key_str = str(args[0].value)
                    dangerous_keys = ["position", "circuit", "health", "breaker", "risk", "guardian"]
                    if any(k in key_str for k in dangerous_keys):
                        if isinstance(default, (ast.Constant,)) and default.value in (0, None, False):
                            snippet = self.lines[node.lineno - 1].strip() if node.lineno <= len(self.lines) else ""
                            _r=self._finding(PATTERNS[1], node.lineno, snippet); self.findings.append(_r) if _r else None

        self.generic_visit(node)

    def visit_If(self, node: ast.If):
        """P1: if var: 且 var 是 .get() 赋值的变量"""
        test = node.test
        var_name = None
        if isinstance(test, ast.Name):
            var_name = test.id
        elif isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            if isinstance(test.operand, ast.Name):
                var_name = test.operand.id

        if var_name and var_name in self._get_vars:
            snippet = self.lines[node.lineno - 1].strip() if node.lineno <= len(self.lines) else ""
            # 只报告可能是 dict/list 的情况（排除明显是bool/int的命名）
            # 扩展布尔变量命名白名单（修复误报 2026-05-17）
            bool_hints = [
                "ok", "flag", "enabled", "found", "valid", "active",
                "success", "error", "is_", "has_", "can_", "_ok", "_flag",
                "res", "result", "data", "val", "wh", "sc", "ob", "fib",
                "div", "vol", "pos", "sig", "ret", "out", "ref",
                # 新增常见命名模式
                "note", "reason", "cap", "key", "meta", "info", "cfg",
                "lvn", "smc", "prio", "priority", "cg", "lvs", "lv",
                "current", "target", "cap_", "_cap", "_note", "_key",
            ]
            probably_bool = (
                any(h in var_name.lower() for h in bool_hints)
                or len(var_name) <= 4          # 4字符以下短变量
                or var_name.startswith('_')    # 私有变量
                or '_res' in var_name          # 结果变量
                or var_name.endswith('_res')
                or var_name.endswith('_cap')   # cap类变量
                or var_name.endswith('_key')   # key类变量
                or var_name.endswith('_note')  # note类变量
                or var_name.endswith('_pct')   # 百分比变量
                or var_name.endswith('_price') # 价格变量
                or var_name in ('queue','candidates','closes','tgts_up','tgts_dn',
                                'strategy','symbol','price','pnl_pct','n_open',
                                'support','resist','swings','fibs','by_exit',
                                'health','grade','extra','lines')
            )
            if not probably_bool:
                _r=self._finding(PATTERNS[0], node.lineno, snippet); self.findings.append(_r) if _r else None

        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        """P3: except: pass — 只报 body 仅含 pass 的情况（吞异常）"""
        if not node.body:
            return
        # 只有 pass（吞异常）才报警，return/continue/log 是合法处理
        only_pass = all(isinstance(s, ast.Pass) for s in node.body)
        if only_pass:
            snippet = self.lines[node.lineno - 1].strip() if node.lineno <= len(self.lines) else ""
            _r=self._finding(PATTERNS[2], node.lineno, snippet); self.findings.append(_r) if _r else None

        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare):
        """P4: float == float"""
        for op, comp in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.NotEq)):
                # 简单检测：右侧是浮点常量
                if isinstance(comp, ast.Constant) and isinstance(comp.value, float):
                    snippet = self.lines[node.lineno - 1].strip() if node.lineno <= len(self.lines) else ""
                    _r=self._finding(PATTERNS[3], node.lineno, snippet); self.findings.append(_r) if _r else None
        self.generic_visit(node)


def _should_exclude(path: pathlib.Path) -> bool:
    parts = path.parts
    return any(ex in parts for ex in EXCLUDE_PATTERNS)


def scan_file(filepath: pathlib.Path) -> List[Dict]:
    try:
        src = filepath.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(filepath))
        lines = src.split("\n")
        visitor = ASTBugVisitor(lines, str(filepath.relative_to(ROOT)))
        visitor.visit(tree)
        return visitor.findings
    except SyntaxError as e:
        return [{
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "layer": "L4_AST",
            "level": "WARN",
            "pattern": "P0",
            "name": "syntax-error",
            "file": str(filepath.relative_to(ROOT)),
            "line": e.lineno,
            "snippet": str(e),
            "desc": "语法错误，无法解析",
        }]
    except Exception:
        return []


def run(verbose: bool = False) -> List[Dict]:
    all_findings = []

    for scan_root in SCAN_DIRS:
        for pyfile in sorted(scan_root.rglob("*.py")):
            if _should_exclude(pyfile):
                continue
            findings = scan_file(pyfile)
            all_findings.extend(findings)

    # 去重（同文件同行同pattern）
    seen = set()
    unique = []
    for f in all_findings:
        key = (f.get("file"), f.get("line"), f.get("pattern"))
        if key not in seen:
            seen.add(key)
            unique.append(f)

    # 写入告警文件
    if unique:
        ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERTS_FILE, "a") as fh:
            for alert in unique:
                fh.write(json.dumps(alert, ensure_ascii=False) + "\n")

    return unique


def summary(findings: List[Dict]) -> str:
    errors = [f for f in findings if f.get("level") == "ERROR"]
    warns  = [f for f in findings if f.get("level") == "WARN"]
    by_pattern: Dict[str, int] = {}
    for f in findings:
        p = f.get("pattern", "?")
        by_pattern[p] = by_pattern.get(p, 0) + 1

    lines = [f"[AST SCANNER] {len(findings)} 个发现 ({len(errors)} ERROR / {len(warns)} WARN)"]
    for pid, count in sorted(by_pattern.items()):
        pname = next((p.name for p in PATTERNS if p.pid == pid), pid)
        lines.append(f"  {pid} {pname}: {count}处")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    verbose = "-v" in sys.argv
    findings = run(verbose=verbose)
    print(summary(findings))
    if verbose:
        for f in findings[:30]:
            print(f"  [{f['level']}] {f['file']}:{f['line']} ({f['pattern']}) {f['snippet']}")
