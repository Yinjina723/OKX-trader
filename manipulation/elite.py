# manipulation/elite.py
"""维度②: 精英 vs 散户背离 + 维度⑥: 精英多周期趋向"""

import numpy as np
from typing import Dict, List


def analyze_elite_divergence(
    ls_ratio: float,
    elite_ratio: float,
    elite_position_data: List = None,
) -> Dict:
    """
    精英持仓比 vs 散户多空比 — 背离 = 庄家收割方向

    返回: {"score": -2~+2, "signal": str, "detail": str}
      +值 = 做多信号, -值 = 做空信号
    """
    score = 0.0
    signals = []
    details = []

    if ls_ratio <= 0 or elite_ratio <= 0:
        return {"score": 0, "signal": "精英数据缺失", "detail": ""}

    # 精英仓位比（实打实的资金方向），优先级最高
    elite_pos_ratio = 1.0
    if elite_position_data and len(elite_position_data) >= 1:
        try:
            item = elite_position_data[0]
            if isinstance(item, dict):
                elite_pos_ratio = float(item.get("longShortPositionRatio", 1.0))
            elif isinstance(item, list) and len(item) >= 4:
                elite_pos_ratio = float(item[3])
        except (ValueError, TypeError, IndexError):
            pass

    # ── 场景1: 散户拥挤做多 + 精英做空/仓位空 → 最强做空信号 ──
    if ls_ratio > 2.5 and elite_pos_ratio < 0.7:
        score = -2.0
        signals.append("🔴 散户拥挤做多 VS 精英持仓空头 — 经典收割信号")
        details.append(f"散户做多 ({ls_ratio:.1f}:1)，精英仓位做空 ({elite_pos_ratio:.2f})")
        details.append("庄家在高位派发筹码给散户，即将砸盘。这是最可靠的做空信号之一")

    elif ls_ratio > 2.5 and elite_ratio < 1.0:
        score = -1.5
        signals.append("🔴 散户拥挤做多 VS 精英做空(人数) — 派发信号")
        details.append(f"散户做多 ({ls_ratio:.1f}:1)，精英做空 ({elite_ratio:.1f}:1)")
        details.append("精英在派发，散户在接盘，下跌风险极高")

    # ── 场景2: 散户拥挤做空 + 精英做多/仓位多 → 最强做多信号 ──
    elif ls_ratio < 0.5 and elite_pos_ratio > 1.5:
        score = 2.0
        signals.append("🟢 散户拥挤做空 VS 精英持仓多头 — 逼空信号")
        details.append(f"散户做空 ({ls_ratio:.2f}:1)，精英仓位做多 ({elite_pos_ratio:.2f})")
        details.append("庄家在低位吸筹，散户在恐慌做空。即将逼空拉升，是最可靠的做多信号之一")

    elif ls_ratio < 0.5 and elite_ratio > 1.5:
        score = 1.5
        signals.append("🟢 散户拥挤做空 VS 精英做多 — 吸筹逼空信号")
        details.append(f"散户做空 ({ls_ratio:.2f}:1)，精英做多 ({elite_ratio:.1f}:1)")
        details.append("精英在底部吸筹，拉升在即")

    # ── 场景3: 精英极端仓位 → 独立信号 ──
    elif elite_pos_ratio > 2.5 and score == 0:
        score = 1.2
        signals.append(f"🟢 精英持仓极度做多 (仓位比={elite_pos_ratio:.1f})")
        details.append(f"精英持仓仓位比={elite_pos_ratio:.1f}，大资金在积极做多")
    elif elite_pos_ratio < 0.4 and score == 0:
        score = -1.2
        signals.append(f"🔴 精英持仓极度做空 (仓位比={elite_pos_ratio:.1f})")
        details.append(f"精英持仓仓位比={elite_pos_ratio:.1f}，大资金在积极做空")

    # ── 场景4: 精英人数比背离（备用） ──
    elif ls_ratio > 2.0 and elite_ratio < 1.0:
        score = min(score - 0.8, -0.8)
        signals.append("🟡 散户偏多 + 精英偏空 — 轻度背离")
    elif ls_ratio < 0.7 and elite_ratio > 1.2:
        score = max(score + 0.8, 0.8)
        signals.append("🟡 散户偏空 + 精英偏多 — 轻度背离")

    return {
        "score": score,
        "signal": " | ".join(signals) if signals else "精英与散户无显著背离",
        "detail": "；".join(details),
        "ls_ratio": ls_ratio,
        "elite_ratio": elite_ratio,
        "elite_pos_ratio": elite_pos_ratio,
    }


def analyze_elite_trend(elite_trend_data: Dict, ls_ratio: float) -> Dict:
    """
    精英持仓多周期趋势分析 — 使用已拉取的 5m/1H/1D 数据

    逻辑:
    - 计算各周期 SMA(3) vs SMA(8) 的交叉方向
    - 多周期共振 → 强信号
    - 短线拐头 + 中长线不变 → 建仓/派发信号
    """
    if not elite_trend_data or not isinstance(elite_trend_data, dict):
        return {"score": 0, "signal": "精英多周期数据缺失", "detail": ""}

    tf_directions = {}

    for tf_period in ['5m', '1H', '1D']:
        tf_data = elite_trend_data.get(tf_period, [])
        if not tf_data or len(tf_data) < 8:
            continue

        ratios = []
        for item in tf_data:
            try:
                if isinstance(item, dict):
                    r = float(item.get("longShortPositionRatio", 0))
                elif isinstance(item, list) and len(item) >= 4:
                    r = float(item[3])
                else:
                    continue
                if r > 0:
                    ratios.append(r)
            except (ValueError, TypeError, IndexError):
                continue

        if len(ratios) < 8:
            continue

        ratios.reverse()
        sw, lw = min(3, len(ratios)), min(8, len(ratios))
        recent_sma = np.mean(ratios[-sw:])
        earlier_sma = np.mean(ratios[-lw:-sw]) if len(ratios) >= lw else recent_sma

        if earlier_sma > 0:
            trend_change = (recent_sma - earlier_sma) / earlier_sma
            if trend_change > 0.05:
                tf_directions[tf_period] = 1    # 精英仓位增加 = 看多
            elif trend_change < -0.05:
                tf_directions[tf_period] = -1   # 精英仓位减少 = 看空
            else:
                tf_directions[tf_period] = 0

    if len(tf_directions) < 2:
        return {"score": 0, "signal": "精英多周期数据不足", "detail": ""}

    dirs = list(tf_directions.values())
    score = 0.0
    signals = []
    details = []

    # 三周期共振
    if len(dirs) >= 3:
        if all(d == 1 for d in dirs):
            score = 1.5
            signals.append("🟢 精英三周期共振做多(5m+1H+1D)")
            details.append("精英大资金在三个周期上同时增加多头仓位，这是最可靠的做多信号之一")
        elif all(d == -1 for d in dirs):
            score = -1.5
            signals.append("🔴 精英三周期共振做空(5m+1H+1D)")
            details.append("精英大资金在三个周期上同时增加空头仓位，强烈看跌")

    # 中长线一致
    if score == 0:
        h_dir = tf_directions.get('1H', 0)
        d_dir = tf_directions.get('1D', 0)
        m_dir = tf_directions.get('5m', 0)

        if h_dir == 1 and d_dir == 1:
            score = 1.0
            signals.append("🟢 精英中长线(1H+1D)一致做多")
            details.append("1H和1D周期精英仓位同步增加，中期看涨")
        elif h_dir == -1 and d_dir == -1:
            score = -1.0
            signals.append("🔴 精英中长线(1H+1D)一致做空")
            details.append("1H和1D周期精英仓位同步减少，中期看跌")
        elif m_dir == 1 and h_dir == -1:
            score = 0.6
            signals.append("🟡 精英短线转多(5m↑) + 中线仍空(1H↓)")
            details.append("短线精英开始买入但中线尚未转向，可能是建仓初期")
        elif m_dir == -1 and h_dir == 1:
            score = -0.6
            signals.append("🟡 精英短线转空(5m↓) + 中线仍多(1H↑)")
            details.append("短线精英开始减仓但中线仓位仍多，可能是派发初期")

    return {
        "score": round(score, 2),
        "signal": " | ".join(signals) if signals else "精英多周期方向不显著",
        "detail": "；".join(details),
        "tf_directions": tf_directions,
    }
