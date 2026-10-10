"""
scripts/analysis/ — 단계별 분석 thin wrapper 레이어
[상태: 骨架阶段 2026-10-10 苏摩111]

현재 각 step*.py는 brahma_manual_analysis.py에서 import하는 얇은 래퍼입니다.
실제 구현은 모두 brahma_manual_analysis.py에 있습니다.

미래 계획:
  각 step*.py에 독립 구현을 이전하여 brahma_manual_analysis.py 단일화 해소
  현재는 인터페이스 안정화 단계

직접 사용 금지: scripts 외부에서 이 패키지를 import하지 마세요.
  → from brahma_manual_analysis import stepN 사용하세요.
"""
"""brahma analysis step modules — thin wrappers over brahma_manual_analysis"""
# [2026-10-04 杨志林工程化 苏摩111]
# 每个step模块独立可导入、可测试、可替换
