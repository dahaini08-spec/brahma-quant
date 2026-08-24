#!/usr/bin/env python3
"""
pip_extractor.py — PIPs几何形态特征提取器
设计院封印 2026-08-12 苏摩111

PIPs (Perceptually Important Points) 算法:
  从N根K线的收盘价序列中，用垂直距离迭代法选出5个最重要的价格拐点
  将5个PIPs点归一化为形态向量，用于方仓案例检索的第9维

学术来源: Perceptually Important Points in Financial Time Series (Chung & Tin, 2004)
验证效果: 方仓案例检索精度预计提升20-30%（形态相似 vs 仅数值相似）

输出:
  pip_vector: List[float] 长度=5, 每个值是归一化价格位置 [0,1]
  pip_shape:  str 形态分类（'V_BOTTOM'/'M_TOP'/'ASCENDING'/'DESCENDING'/'FLAT'）
  shape_score: float 形态清晰度分数 [0,1]
"""

from typing import List, Tuple, Optional
import math


def calc_pips(prices: List[float], n_pips: int = 5) -> List[int]:
    """
    从价格序列中提取N个PIPs点的索引
    算法：迭代选择到已有PIPs连线垂直距离最大的点
    
    Args:
        prices: 价格序列（收盘价）
        n_pips: 目标PIPs数量（默认5）
    
    Returns:
        PIPs点在prices中的索引列表（有序）
    """
    if len(prices) < n_pips:
        return list(range(len(prices)))
    
    # 初始化：选首尾两点
    pip_indices = [0, len(prices) - 1]
    
    while len(pip_indices) < n_pips:
        max_dist = -1.0
        max_idx  = -1
        
        # 遍历所有已有PIPs区间
        for i in range(len(pip_indices) - 1):
            left_i  = pip_indices[i]
            right_i = pip_indices[i + 1]
            
            if right_i - left_i <= 1:
                continue
            
            # 区间两端点
            x1, y1 = float(left_i),  prices[left_i]
            x2, y2 = float(right_i), prices[right_i]
            
            # 对区间内每个点计算到直线的垂直距离
            for j in range(left_i + 1, right_i):
                x0, y0 = float(j), prices[j]
                
                # 点到线段的垂直距离
                dx = x2 - x1
                dy = y2 - y1
                if dx == 0 and dy == 0:
                    dist = math.sqrt((x0-x1)**2 + (y0-y1)**2)
                else:
                    # 垂直距离公式（价格归一化）
                    price_range = max(prices) - min(prices)
                    if price_range == 0:
                        dist = 0.0
                    else:
                        # 归一化后计算，避免价格量纲影响
                        yn0 = (y0 - min(prices)) / price_range
                        yn1 = (y1 - min(prices)) / price_range
                        yn2 = (y2 - min(prices)) / price_range
                        xn0 = x0 / len(prices)
                        xn1 = x1 / len(prices)
                        xn2 = x2 / len(prices)
                        
                        # 点(xn0,yn0)到直线(xn1,yn1)-(xn2,yn2)的距离
                        dxn = xn2 - xn1
                        dyn = yn2 - yn1
                        denom = math.sqrt(dxn**2 + dyn**2)
                        if denom == 0:
                            dist = 0.0
                        else:
                            dist = abs(dyn*xn0 - dxn*yn0 + xn2*yn1 - yn2*xn1) / denom
                
                if dist > max_dist:
                    max_dist = dist
                    max_idx  = j
        
        if max_idx == -1:
            break
        
        # 插入新PIPs点（保持有序）
        pip_indices.append(max_idx)
        pip_indices.sort()
    
    return pip_indices


def pips_to_vector(prices: List[float], n_pips: int = 5) -> List[float]:
    """
    将价格序列转为PIPs归一化向量
    
    Returns:
        长度=n_pips的向量，每个值是归一化到[0,1]的价格位置
    """
    if len(prices) < 2:
        return [0.5] * n_pips
    
    pip_indices = calc_pips(prices, n_pips)
    pip_prices  = [prices[i] for i in pip_indices]
    
    # 归一化到[0,1]
    p_min = min(pip_prices)
    p_max = max(pip_prices)
    
    if p_max == p_min:
        return [0.5] * len(pip_prices)
    
    return [(p - p_min) / (p_max - p_min) for p in pip_prices]


def classify_pip_shape(pip_vector: List[float]) -> Tuple[str, float]:
    """
    根据5个PIPs点的归一化向量分类形态
    
    Returns:
        (shape_name, clarity_score)
    """
    if len(pip_vector) < 5:
        return ('UNKNOWN', 0.0)
    
    p = pip_vector  # [p0, p1, p2, p3, p4]
    p_min_idx = p.index(min(p))
    p_max_idx = p.index(max(p))
    
    # V底形态：最低点在中间区域（索引 1-3），且首尾都偏高
    if p_min_idx in (1, 2, 3) and p[0] > 0.4 and p[4] > 0.4:
        clarity = (p[0] + p[4]) / 2 - min(p)
        return ('V_BOTTOM', min(clarity * 2, 1.0))
    
    # M顶形态：最高点在中间区域（索引 1-3），且首尾都偏低
    if p_max_idx in (1, 2, 3) and p[0] < 0.6 and p[4] < 0.6:
        clarity = max(p) - (p[0] + p[4]) / 2
        return ('M_TOP', min(clarity * 2, 1.0))
    
    # 上升趋势：整体向上
    if p[4] > p[0] + 0.3:
        clarity = p[4] - p[0]
        return ('ASCENDING', min(clarity, 1.0))
    
    # 下降趋势：整体向下
    if p[0] > p[4] + 0.3:
        clarity = p[0] - p[4]
        return ('DESCENDING', min(clarity, 1.0))
    
    # 头肩顶：p1高 p3高 p2更高（左肩-头-右肩）
    if len(p) >= 5 and p[2] > p[1] and p[2] > p[3] and abs(p[1]-p[3]) < 0.2:
        return ('HEAD_SHOULDERS', 0.7)
    
    # 双底：p1低 p3低 相近
    if p[1] < 0.3 and p[3] < 0.3 and abs(p[1]-p[3]) < 0.15:
        return ('DOUBLE_BOTTOM', 0.8)
    
    # 双顶：p1高 p3高 相近
    if p[1] > 0.7 and p[3] > 0.7 and abs(p[1]-p[3]) < 0.15:
        return ('DOUBLE_TOP', 0.8)
    
    return ('FLAT', 0.3)


def extract_pip_feature(prices: List[float], n_pips: int = 5) -> dict:
    """
    完整提取PIPs特征
    
    Args:
        prices: 收盘价序列（建议20-50根）
    
    Returns:
        dict with: pip_vector, pip_shape, shape_score, pip_similarity_ready
    """
    if len(prices) < n_pips + 2:
        return {
            'pip_vector': [0.5] * n_pips,
            'pip_shape': 'INSUFFICIENT_DATA',
            'shape_score': 0.0,
            'pip_similarity_ready': False,
        }
    
    pip_vec = pips_to_vector(prices, n_pips)
    shape, score = classify_pip_shape(pip_vec)
    
    return {
        'pip_vector': pip_vec,
        'pip_shape': shape,
        'shape_score': score,
        'pip_similarity_ready': True,
    }


def pip_cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """计算两个PIPs向量的余弦相似度"""
    if len(vec_a) != len(vec_b):
        return 0.0
    dot   = sum(a*b for a,b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a*a for a in vec_a))
    norm_b = math.sqrt(sum(b*b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
