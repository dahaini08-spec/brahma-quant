#!/usr/bin/env python3
"""
circuit_breaker.py — 梵天全链路熔断器
设计院封印 2026-07-02 Phase 1

信号→仓位→执行→结算 每步设熔断，崩溃自动回滚

熔断策略：
  CLOSED  → 正常工作
  OPEN    → 熔断，直接拒绝请求，防止雪崩
  HALF    → 半开，允许1个探测请求

配置：
  failure_threshold: 连续失败N次 → OPEN
  recovery_timeout:  OPEN后等待Xs → HALF
  success_threshold: HALF中成功N次 → CLOSED

私有版专属：层级熔断（不同层独立状态）
"""
import time
import json
import logging
import functools
from pathlib import Path
from typing import Any, Callable, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger('brahma.circuit_breaker')
BASE = Path(__file__).parent.parent


class CBState(Enum):
    CLOSED = 'CLOSED'    # 正常
    OPEN   = 'OPEN'      # 熔断
    HALF   = 'HALF'      # 半开探测


@dataclass
class CircuitBreakerConfig:
    name: str
    failure_threshold: int = 3     # 连续失败N次 → OPEN
    recovery_timeout: int  = 60    # OPEN后等待秒数 → HALF
    success_threshold: int = 2     # HALF成功N次 → CLOSED
    fallback: Any = None           # 熔断时的降级返回值


@dataclass
class CircuitBreakerState:
    state: CBState = CBState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.time)
    total_calls: int = 0
    total_failures: int = 0


class CircuitBreaker:
    """
    单个熔断器实例
    线程安全（基于GIL），适合单进程多cron场景
    """
    
    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self._state = CircuitBreakerState()
        self._state_file = BASE / 'data' / f'cb_{config.name}.json'
        self._load_state()
    
    def _load_state(self):
        """从文件恢复状态（进程重启后保持记忆）"""
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text())
                self._state.state = CBState(data.get('state', 'CLOSED'))
                self._state.failure_count = data.get('failure_count', 0)
                self._state.last_failure_time = data.get('last_failure_time', 0.0)
                self._state.total_calls = data.get('total_calls', 0)
                self._state.total_failures = data.get('total_failures', 0)
        except Exception:
            pass  # 加载失败 → 从CLOSED开始
    
    def _save_state(self):
        """持久化状态"""
        try:
            data = {
                'name': self.config.name,
                'state': self._state.state.value,
                'failure_count': self._state.failure_count,
                'last_failure_time': self._state.last_failure_time,
                'last_state_change': self._state.last_state_change,
                'total_calls': self._state.total_calls,
                'total_failures': self._state.total_failures,
                'updated_at': time.time(),
            }
            self._state_file.parent.mkdir(exist_ok=True)
            tmp = str(self._state_file) + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f)
            import os
            os.replace(tmp, str(self._state_file))
        except Exception:
            pass
    
    @property
    def is_open(self) -> bool:
        """当前是否熔断"""
        if self._state.state == CBState.OPEN:
            # 检查是否可以进入HALF状态
            if time.time() - self._state.last_failure_time >= self.config.recovery_timeout:
                self._transition(CBState.HALF)
                return False
            return True
        return False
    
    def _transition(self, new_state: CBState):
        old = self._state.state
        self._state.state = new_state
        self._state.last_state_change = time.time()
        if new_state == CBState.CLOSED:
            self._state.failure_count = 0
            self._state.success_count = 0
        elif new_state == CBState.OPEN:
            self._state.last_failure_time = time.time()
        logger.warning(f"[CircuitBreaker:{self.config.name}] {old.value} → {new_state.value}")
        self._save_state()
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        通过熔断器调用函数
        OPEN状态时返回fallback值
        """
        self._state.total_calls += 1
        
        if self.is_open:
            logger.warning(
                f"[CB:{self.config.name}] OPEN → 熔断，返回fallback"
            )
            return self.config.fallback
        
        try:
            result = func(*args, **kwargs)
            
            # 成功
            if self._state.state == CBState.HALF:
                self._state.success_count += 1
                if self._state.success_count >= self.config.success_threshold:
                    self._transition(CBState.CLOSED)
            elif self._state.state == CBState.CLOSED:
                self._state.failure_count = 0  # 重置连续失败计数
            
            self._save_state()
            return result
            
        except Exception as e:
            self._state.failure_count += 1
            self._state.total_failures += 1
            logger.error(
                f"[CB:{self.config.name}] 调用失败 "
                f"(失败次数:{self._state.failure_count}/{self.config.failure_threshold}): {e}"
            )
            
            if self._state.state == CBState.HALF:
                # HALF中失败 → 重新OPEN
                self._transition(CBState.OPEN)
            elif self._state.failure_count >= self.config.failure_threshold:
                # 达到阈值 → OPEN
                self._transition(CBState.OPEN)
            
            self._save_state()
            return self.config.fallback
    
    def status(self) -> dict:
        return {
            'name': self.config.name,
            'state': self._state.state.value,
            'failure_count': self._state.failure_count,
            'total_calls': self._state.total_calls,
            'total_failures': self._state.total_failures,
            'failure_rate': (
                f"{100*self._state.total_failures//self._state.total_calls}%"
                if self._state.total_calls > 0 else "N/A"
            ),
        }
    
    def reset(self):
        """手动重置（苏摩干预用）"""
        self._transition(CBState.CLOSED)
        self._state.total_failures = 0
        self._save_state()
        logger.info(f"[CB:{self.config.name}] 手动重置为CLOSED")


# ── 全系统熔断器注册表 ─────────────────────────────────────────────────
class BrahmaCircuitRegistry:
    """
    梵天全链路熔断器注册表
    私有版专属：按信号流分层配置
    """
    _instance = None
    
    @classmethod
    def get(cls) -> 'BrahmaCircuitRegistry':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self._breakers = {}
        self._init_default_breakers()
    
    def _init_default_breakers(self):
        """初始化9层数据链路的熔断器"""
        configs = [
            # 层0 守望层 - 零成本，容错高
            CircuitBreakerConfig(
                name='rsi_watcher', failure_threshold=5,
                recovery_timeout=120, fallback=None
            ),
            # 层1 扫描层 - AI调用，较重，熔断激进
            CircuitBreakerConfig(
                name='brahma_scan', failure_threshold=3,
                recovery_timeout=300, fallback={'error': 'CB_OPEN', 'score': 0}
            ),
            # 层2 总线层 - 缓存，高可用
            CircuitBreakerConfig(
                name='brahma_bus', failure_threshold=5,
                recovery_timeout=60, fallback=None
            ),
            # 层3 评分层 - 核心，失败代价高
            CircuitBreakerConfig(
                name='confluence_score', failure_threshold=3,
                recovery_timeout=180, fallback={'total': 0, 'grade': 'CB_OPEN', 'valid': False}
            ),
            # 层4 验证层 - 因果验证
            CircuitBreakerConfig(
                name='causal_verify', failure_threshold=3,
                recovery_timeout=120, fallback={'verdict': 'CB_OPEN', 'score_adj': 0}
            ),
            # 层5 时机层
            CircuitBreakerConfig(
                name='timing_filter', failure_threshold=3,
                recovery_timeout=120, fallback={'status': 'WAIT', 'badge': '⏸ CB_OPEN'}
            ),
            # 层6 仓位层 - 执行前最后一关，严格
            CircuitBreakerConfig(
                name='position_sizer', failure_threshold=2,
                recovery_timeout=300, fallback={'pct': 0, 'level': 'CB_BLOCKED'}
            ),
            # 层7 信号队列
            CircuitBreakerConfig(
                name='signal_queue', failure_threshold=3,
                recovery_timeout=120, fallback=None
            ),
            # 层8 执行门控 - 最严格，失败立即OPEN
            CircuitBreakerConfig(
                name='auto_execute_gate', failure_threshold=1,
                recovery_timeout=600, fallback={'allowed': False, 'reason': 'CB_OPEN'}
            ),
            # 层9 执行器 - 最危险，失败立即熔断10分钟
            CircuitBreakerConfig(
                name='auto_executor', failure_threshold=1,
                recovery_timeout=600, fallback={'error': 'CB_OPEN', 'status': 'FAILED'}
            ),
        ]
        for cfg in configs:
            self._breakers[cfg.name] = CircuitBreaker(cfg)
    
    def get_breaker(self, name: str) -> Optional[CircuitBreaker]:
        return self._breakers.get(name)
    
    def call_safe(self, layer: str, func: Callable, *args, **kwargs) -> Any:
        """通过指定层的熔断器调用"""
        breaker = self.get_breaker(layer)
        if breaker is None:
            logger.warning(f"[Registry] 未知层: {layer}，直接调用")
            return func(*args, **kwargs)
        return breaker.call(func, *args, **kwargs)
    
    def status_all(self) -> dict:
        """全系统熔断状态"""
        return {
            name: cb.status()
            for name, cb in self._breakers.items()
        }
    
    def has_open_breakers(self) -> bool:
        """是否有OPEN状态的熔断器"""
        return any(
            cb._state.state == CBState.OPEN
            for cb in self._breakers.values()
        )
    
    def reset_all(self):
        """全部重置（苏摩紧急干预）"""
        for cb in self._breakers.values():
            cb.reset()
        logger.warning("[Registry] 全部熔断器已重置")


# ── 装饰器（函数级熔断） ──────────────────────────────────────────────
def circuit_protected(layer: str, fallback=None):
    """
    装饰器：为函数添加熔断保护
    
    用法：
        @circuit_protected('brahma_scan', fallback={'error': 'cb_open'})
        def run_analysis(symbol):
            ...
    """
    def decorator(func):
        cfg = CircuitBreakerConfig(name=layer, fallback=fallback)
        cb = CircuitBreaker(cfg)
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return cb.call(func, *args, **kwargs)
        
        wrapper._circuit_breaker = cb  # 允许外部访问状态
        return wrapper
    return decorator


# ── 主入口（状态检查） ────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse, json as _json
    parser = argparse.ArgumentParser()
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--reset', type=str, help='重置指定层')
    parser.add_argument('--reset-all', action='store_true')
    args = parser.parse_args()
    
    registry = BrahmaCircuitRegistry.get()
    
    if args.reset_all:
        registry.reset_all()
        print("✅ 全部熔断器已重置")
    elif args.reset:
        cb = registry.get_breaker(args.reset)
        if cb:
            cb.reset()
            print(f"✅ {args.reset} 已重置")
        else:
            print(f"❌ 未找到: {args.reset}")
    else:
        status = registry.status_all()
        print(f"\n🔌 梵天全链路熔断器状态 | {time.strftime('%H:%M UTC')}")
        print("="*55)
        for name, s in status.items():
            icon = {'CLOSED': '🟢', 'OPEN': '🔴', 'HALF': '🟡'}.get(s['state'], '⚪')
            print(f"  {icon} {name:22s} {s['state']:8s} "
                  f"失败:{s['failure_count']}/{s.get('total_failures',0)} "
                  f"总调用:{s['total_calls']}")
        if registry.has_open_breakers():
            print("\n⚠️ 存在OPEN熔断器，部分功能已降级")
        else:
            print("\n✅ 全部层级正常（CLOSED）")
