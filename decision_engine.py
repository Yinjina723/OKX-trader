# decision_engine.py
"""智能决策引擎 —— 发现热门合约 → 自动下载 → AI 分析 → 评分排序 → 每日推荐

流程:
  1. 从 OKX 拉取 24h 成交额 Top10 的 USDT 永续合约
  2. 逐个检查历史数据，缺失则自动下载
  3. 加载 K 线 + 计算指标 + 庄家检测 + 情绪分析
  4. 调 AI 分析最新完整日线，给出方向/入场/止损/止盈
  5. 加载该币种最新回测报告，提取历史统计
  6. 按置信度公式评分排序，缓存到 decision_latest.json

用法:
  from decision_engine import run_decision_pipeline, save_decision_cache, load_decision_cache
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config import Config
from okx_client import OKXClient
from prepare_data import download_symbol
from indicators import calculate_all as calculate_indicators
from patterns import (
    detect_candlestick_patterns,
    detect_rsi_divergence,
    detect_macd_divergence,
    detect_ma_alignment,
)
from manipulation.daily_engine import run_daily_manipulation
from ai_analysis import analyze_daily_with_ai
from liquidation_hunter import analyze_liquidation_hunt
from capital_guard import integrate_with_hunt

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════════

def _build_sentiment(df: pd.DataFrame, funding_df: Optional[pd.DataFrame], day_idx: int) -> Dict:
    """为指定 day_idx 构建情绪数据（仅用资金费率历史，无前视偏差）。

    与 backtest.BacktestEngine._build_sentiment 逻辑一致。
    """
    if funding_df is None or funding_df.empty:
        return {
            "funding": {"status": "无历史数据"},
            "oi": {"status": "不可得"},
            "ls_ratio": {"status": "不可得"},
            "overall_bias": "insufficient",
            "summary_text": "",
            "warnings": [],
        }

    cutoff = df["timestamp"].iloc[day_idx]
    mask = funding_df["fundingTime"] <= cutoff
    if not mask.any():
        return {
            "funding": {"status": "无数据"}, "oi": {"status": "不可得"},
            "ls_ratio": {"status": "不可得"},
            "overall_bias": "insufficient", "summary_text": "", "warnings": [],
        }

    rates = funding_df.loc[mask, "fundingRate"].values.astype(float)
    cur = float(rates[-1])
    avg5 = float(np.mean(rates[-5:])) if len(rates) >= 5 else cur
    half = max(1, len(rates) // 2)
    trend = "上升" if np.mean(rates[half:]) > np.mean(rates[:half]) else \
            "下降" if np.mean(rates[half:]) < np.mean(rates[:half]) else "持平"

    warnings = []
    if cur > 0.001:
        warnings.append("多头拥挤")
    elif cur < -0.001:
        warnings.append("空头拥挤")

    overall = "bullish" if cur > 0.0005 else "bearish" if cur < -0.0005 else "mixed"

    return {
        "funding": {
            "current": round(cur, 6), "avg_recent_5": round(avg5, 6),
            "summary": f"当前{cur:.4%}, 近5期均值{avg5:.4%}, 趋势{trend}",
            "warnings": warnings,
        },
        "oi": {"status": "不可得"},
        "ls_ratio": {"status": "不可得"},
        "overall_bias": overall,
        "summary_text": f"资金费率: {cur:.4%} ({trend})",
        "warnings": warnings,
    }


# ══════════════════════════════════════════════════════════════════
#  数据管道
# ══════════════════════════════════════════════════════════════════

def get_top_coins(config: Config, top_n: int = 15) -> List[Dict]:
    """从 OKX 获取 USDT 永续合约热门榜，按 24h 成交额排序取 Top N。"""
    client = OKXClient(config)
    # 按 CoinGecko 市值排序，市值 > 1 亿，取 Top 15
    return client.get_top_swaps_by_market_cap(min_mcap=100_000_000, top_n=top_n)


def ensure_data(config: Config, symbol: str) -> bool:
    """确保指定币种的历史数据已下载。"""
    safe = symbol.replace("-", "_")
    kline_path = os.path.join(config.HISTORY_DIR, f"kline_{safe}.csv")
    if os.path.exists(kline_path):
        return True

    logger.info(f"  [{symbol}] 历史数据不存在，自动下载...")
    client = OKXClient(config)
    return download_symbol(client, symbol, config.HISTORY_DIR)


def load_and_prepare_data(config: Config, symbol: str):
    """加载 K 线 CSV，计算技术指标。返回 (df, funding_df) 或 (None, None)。"""
    safe = symbol.replace("-", "_")
    kline_path = os.path.join(config.HISTORY_DIR, f"kline_{safe}.csv")
    funding_path = os.path.join(config.HISTORY_DIR, f"funding_{safe}.csv")

    if not os.path.exists(kline_path):
        return None, None

    df = pd.read_csv(kline_path, parse_dates=["timestamp"])
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    if len(df) < 70:
        logger.warning(f"  [{symbol}] K线仅 {len(df)} 根，不足以分析")
        return None, None

    df = calculate_indicators(df, advanced=config.ADVANCED_INDICATORS)

    funding_df = None
    if os.path.exists(funding_path):
        try:
            funding_df = pd.read_csv(funding_path, parse_dates=["fundingTime"])
        except Exception:
            pass

    return df, funding_df


def analyze_latest_day(config: Config, symbol: str, df: pd.DataFrame,
                       funding_df: Optional[pd.DataFrame]) -> Optional[Dict]:
    """调用 AI 分析最新完整日线（倒数第2根K线），返回 {date, close, ai}。"""
    n = len(df)
    yesterday_idx = n - 2  # 倒数第2根 = 最新一根完整日线

    if yesterday_idx < 65:
        logger.warning(f"  [{symbol}] 数据不够预热 (idx={yesterday_idx})")
        return None

    df_view = df.iloc[:yesterday_idx + 1].copy()

    patterns = detect_candlestick_patterns(df_view, lookback=10)
    rsi_div = detect_rsi_divergence(df_view)
    macd_div = detect_macd_divergence(df_view)
    divergence = {"rsi": rsi_div, "macd": macd_div}
    ma_alignment = detect_ma_alignment(df_view)

    manipulation = run_daily_manipulation(
        df_view, symbol=symbol,
        wick_shadow_ratio=config.WICK_SHADOW_RATIO,
    )

    sentiment = _build_sentiment(df, funding_df, yesterday_idx)

    ai_result = analyze_daily_with_ai(
        config, symbol, df_view, yesterday_idx,
        manipulation=manipulation, patterns=patterns,
        divergence=divergence, ma_alignment=ma_alignment,
        sentiment=sentiment,
    )

    return {
        "date": str(df["timestamp"].iloc[yesterday_idx])[:10],
        "close": float(df["close"].iloc[yesterday_idx]),
        "ai": ai_result,
    }


def load_backtest_stats(config: Config, symbol: str) -> Optional[Dict]:
    """加载指定币种最新的回测 JSON 报告。"""
    import glob
    safe = symbol.replace("-", "_")
    pattern = os.path.join(config.OUTPUT_DIR, f"backtest_{safe}_*.json")
    files = sorted(glob.glob(pattern), reverse=True)

    if not files:
        return None

    try:
        with open(files[0], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
#  置信度评分
# ══════════════════════════════════════════════════════════════════

def calc_confidence_score(ai_result: Dict, backtest_report: Optional[Dict]) -> Dict:
    """根据 AI 信号 + 历史回测统计计算置信度得分（满分 100）。

    评分维度:
      1. 方向历史胜率 (40分)
      2. AI 信号强度    (20分)
      3. 策略盈亏比      (20分)
      4. 近期趋势        (20分)

    扣分项:
      - 方向胜率 < 30% → 标红警告，扣 15 分
      - 连亏 ≥ 3 笔   → 扣 10 分
    """
    direction = ai_result.get("direction", "neutral")
    strength = ai_result.get("strength", "medium")

    if direction == "neutral":
        return {
            "score": 0,
            "reasons": ["AI 无明确方向"],
            "warnings": ["AI 建议观望，不推荐交易"],
            "red_flag": True,
            "direction_win_rate": 0,
            "direction_count": 0,
        }

    summary: Dict = {}
    by_dir: Dict = {}
    trades: List = []

    if backtest_report:
        summary = backtest_report.get("summary", {})
        by_dir = backtest_report.get("by_direction", {})
        trades = backtest_report.get("trades", [])

    score = 0
    reasons: List[str] = []
    warnings: List[str] = []
    red_flag = False

    # ── 1. 方向历史胜率 (40分) ──
    dir_stats = by_dir.get(direction, {})
    dir_win_rate = dir_stats.get("win_rate", 0)
    dir_count = dir_stats.get("count", 0)

    if dir_stats:
        if dir_win_rate >= 60:
            score += 40
            reasons.append(f"✅ 历史{direction}胜率 {dir_win_rate}% ({dir_count}笔)")
        elif dir_win_rate >= 50:
            score += 32
            reasons.append(f"✅ 历史{direction}胜率 {dir_win_rate}% ({dir_count}笔)")
        elif dir_win_rate >= 40:
            score += 24
            reasons.append(f"历史{direction}胜率 {dir_win_rate}% ({dir_count}笔)")
        elif dir_win_rate >= 30:
            score += 12
            reasons.append(f"历史{direction}胜率 {dir_win_rate}%（偏低）")
        else:
            warnings.append(f"⛔ 历史{direction}胜率仅 {dir_win_rate}%，极度不推荐跟单")
            red_flag = True
    else:
        score += 25
        reasons.append("该方向无历史回测数据")

    # ── 2. AI 信号强度 (20分) ──
    if strength == "strong":
        score += 20
        reasons.append("✅ AI信号强度: 强")
    elif strength == "medium":
        score += 12
        reasons.append("AI信号强度: 中等")
    elif strength == "weak":
        score += 4
        reasons.append("AI信号强度: 弱")

    # ── 3. 策略盈亏比 (20分) ──
    pf = summary.get("profit_factor", 0)
    if pf >= 2.0:
        score += 20
        reasons.append(f"✅ 策略盈亏比 {pf:.2f}")
    elif pf >= 1.5:
        score += 16
        reasons.append(f"✅ 策略盈亏比 {pf:.2f}")
    elif pf >= 1.0:
        score += 12
        reasons.append(f"策略盈亏比 {pf:.2f}")
    elif pf >= 0.5:
        score += 6
        reasons.append(f"策略盈亏比 {pf:.2f}（偏低）")
    else:
        score += 4
        reasons.append("无盈亏比数据")

    # ── 4. 近期趋势 — 近 5 笔胜率 (20分) ──
    if trades:
        recent = trades[-5:]
        recent_wins = sum(1 for t in recent if t.get("pnl_pct", 0) > 0)
        recent_win_rate = recent_wins / len(recent) * 100

        if recent_win_rate >= 60:
            score += 20
            reasons.append(f"✅ 近{len(recent)}笔胜率 {recent_win_rate:.0f}%")
        elif recent_win_rate >= 40:
            score += 12
            reasons.append(f"近{len(recent)}笔胜率 {recent_win_rate:.0f}%")
        else:
            score += 4
            reasons.append(f"近{len(recent)}笔胜率 {recent_win_rate:.0f}%（偏低）")

        # 连亏检测
        streak_loss = 0
        for t in reversed(trades):
            if t.get("pnl_pct", 0) <= 0:
                streak_loss += 1
            else:
                break
        if streak_loss >= 3:
            score = max(0, score - 10)
            warnings.append(f"⚠️ 已连亏 {streak_loss} 笔，建议减半仓位或跳过")
        elif streak_loss >= 2:
            score = max(0, score - 5)
            warnings.append(f"已连亏 {streak_loss} 笔")

    # ── 整体胜率过低时加个提示 ──
    overall_wr = summary.get("win_rate", 0)
    if overall_wr < 35:
        warnings.append(f"整体策略胜率仅 {overall_wr}%，谨慎参与")

    score = max(0, min(100, score))

    return {
        "score": score,
        "reasons": reasons,
        "warnings": warnings,
        "red_flag": red_flag,
        "direction_win_rate": dir_win_rate,
        "direction_count": dir_count,
    }


# ══════════════════════════════════════════════════════════════════
#  主管道
# ══════════════════════════════════════════════════════════════════

def run_decision_pipeline(config: Config) -> List[Dict]:
    """运行完整决策管道，返回按置信度降序排列的推荐列表。

    返回列表每项:
      {symbol, status, rank, current_signal, confidence, backtest_summary,
       backtest_by_direction, vol_24h_usd}
    """
    logger.info("=" * 60)
    logger.info("🚀 智能决策引擎启动")
    logger.info("=" * 60)

    # ── Step 1: 使用配置中的交易对列表 ──
    coins_meta = [{"instId": s} for s in config.SYMBOLS]
    logger.info(f"扫描配置中 {len(coins_meta)} 个交易对: {', '.join(config.SYMBOLS)}")

    results: List[Dict] = []

    for coin in coins_meta:
        symbol = coin["instId"]
        try:
            logger.info(f"\n{'─'*40}")
            logger.info(f"处理 [{symbol}] ...")

            # ── Step 2: 确保数据 ──
            if not ensure_data(config, symbol):
                logger.warning(f"  [{symbol}] 数据下载失败")
                results.append({
                    "symbol": symbol, "status": "data_error", "rank": 0,
                    "vol_24h_usd": coin.get("volCcy24h_usd", 0),
                    "vol_fmt": coin.get("vol_fmt", ""),
                    "market_cap": coin.get("market_cap", 0),
                    "mcap_fmt": coin.get("mcap_fmt", ""),
                    "confidence": {"score": 0, "reasons": ["数据下载失败"],
                                   "warnings": [], "red_flag": True},
                })
                continue

            # ── Step 3: 加载 & 准备数据 ──
            df, funding_df = load_and_prepare_data(config, symbol)
            if df is None:
                logger.warning(f"  [{symbol}] 数据加载/准备失败")
                results.append({
                    "symbol": symbol, "status": "data_insufficient", "rank": 0,
                    "vol_24h_usd": coin.get("volCcy24h_usd", 0),
                    "vol_fmt": coin.get("vol_fmt", ""),
                    "market_cap": coin.get("market_cap", 0),
                    "mcap_fmt": coin.get("mcap_fmt", ""),
                    "confidence": {"score": 0, "reasons": ["数据不足（需要至少70根日线）"],
                                   "warnings": [], "red_flag": True},
                })
                continue

            logger.info(f"  [{symbol}] K线 {len(df)} 根")

            # ── Step 4: AI 分析最新日线 ──
            current_signal = analyze_latest_day(config, symbol, df, funding_df)
            if current_signal is None:
                logger.warning(f"  [{symbol}] AI 分析失败")
                results.append({
                    "symbol": symbol, "status": "ai_failed", "rank": 0,
                    "vol_24h_usd": coin.get("volCcy24h_usd", 0),
                    "vol_fmt": coin.get("vol_fmt", ""),
                    "market_cap": coin.get("market_cap", 0),
                    "mcap_fmt": coin.get("mcap_fmt", ""),
                    "confidence": {"score": 0, "reasons": ["AI分析失败"],
                                   "warnings": [], "red_flag": True},
                })
                continue

            ai = current_signal.get("ai", {})
            logger.info(f"  [{symbol}] AI: direction={ai.get('direction')}, "
                        f"strength={ai.get('strength')}, "
                        f"entry={ai.get('entry')}")

            # ── Step 5: 加载回测统计 ──
            bt_report = load_backtest_stats(config, symbol)
            if bt_report:
                bt_s = bt_report.get("summary", {})
                logger.info(f"  [{symbol}] 回测: 胜率={bt_s.get('win_rate')}%, "
                            f"成交={bt_s.get('filled_trades')}笔, "
                            f"盈亏比={bt_s.get('profit_factor')}")

            # ── Step 6: 计算置信度 ──
            confidence = calc_confidence_score(ai, bt_report)

            # ── Step 6.5: 猎杀爆仓分析 ──
            liquidation_hunt = None
            if config.LIQUIDATION_HUNT_ENABLED:
                ls_ratio = sentiment.get("ls_ratio", {}).get("current_ratio")
                vwap_col = "VWAP"
                if vwap_col in df_view.columns:
                    vwap = float(df_view.iloc[-1][vwap_col])
                    if np.isnan(vwap):
                        vwap = None
                else:
                    vwap = None
                if ls_ratio is not None and vwap is not None:
                    hunt_result = analyze_liquidation_hunt(
                        ls_ratio=ls_ratio,
                        vwap=vwap,
                        current_price=float(df_view["close"].iloc[-1]),
                        leverage_levels=config.HUNT_LEVERAGE_LEVELS,
                        ls_long_extreme=config.LS_LONG_EXTREME,
                        ls_short_extreme=config.LS_SHORT_EXTREME,
                    )
                    liquidation_hunt = hunt_result.to_dict()
                    # 记录猎杀信号
                    if hunt_result.mode != "none":
                        logger.info(f"  [{symbol}] 💀 猎杀信号: {hunt_result.mode_cn} "
                                    f"挂单{hunt_result.recommended_entry:.6f}")

            # ── Step 6.6: 资金卫士分析 ──
            capital_plan = None
            if config.CAPITAL_GUARD_ENABLED:
                ai_direction = ai.get("direction", "neutral")
                ai_entry = ai.get("entry")
                cp = integrate_with_hunt(
                    symbol=symbol,
                    direction=ai_direction,
                    current_price=float(df_view["close"].iloc[-1]),
                    ai_entry_price=ai_entry,
                    hunt_result=liquidation_hunt,
                    total_capital=config.TOTAL_CAPITAL,
                    trading_ratio=config.TRADING_CAPITAL_RATIO,
                    reserve_ratio=config.RESERVE_CAPITAL_RATIO,
                    leverage=config.DEFAULT_LEVERAGE,
                )
                capital_plan = cp.to_dict()
                logger.info(f"  [{symbol}] 🛡️ 资金卫士: 安全距离={cp.safety_distance_pct:.1f}% "
                            f"等级={cp.safety_level}")

            logger.info(f"  [{symbol}] 置信度: {confidence['score']}/100 "
                        f"{'⚠️ 红标' if confidence.get('red_flag') else ''}")

            entry = {
                "symbol": symbol,
                "status": "ready",
                "rank": 0,  # 待排序后赋值
                "vol_24h_usd": coin.get("volCcy24h_usd", 0),
                "vol_fmt": coin.get("vol_fmt", ""),
                "market_cap": coin.get("market_cap", 0),
                "mcap_fmt": coin.get("mcap_fmt", ""),
                "current_signal": current_signal,
                "confidence": confidence,
                "liquidation_hunt": liquidation_hunt,
                "capital_plan": capital_plan,
                "backtest_summary": bt_report.get("summary") if bt_report else None,
                "backtest_by_direction": bt_report.get("by_direction") if bt_report else None,
            }
            results.append(entry)

        except Exception as e:
            logger.exception(f"  [{symbol}] 处理异常: {e}")
            results.append({
                "symbol": symbol, "status": "error", "rank": 0,
                "vol_24h_usd": coin.get("volCcy24h_usd", 0),
                "vol_fmt": coin.get("vol_fmt", ""),
                "market_cap": coin.get("market_cap", 0),
                "mcap_fmt": coin.get("mcap_fmt", ""),
                "confidence": {"score": 0, "reasons": [f"异常: {str(e)}"],
                               "warnings": [], "red_flag": True},
            })

    # ── Step 7: 排序 & 赋排名 ──
    results.sort(key=lambda x: x.get("confidence", {}).get("score", 0), reverse=True)

    rank = 0
    for r in results:
        if r["status"] == "ready" and not r.get("confidence", {}).get("red_flag"):
            rank += 1
            r["rank"] = rank
        else:
            r["rank"] = 0

    # ── 打印汇总 ──
    logger.info(f"\n{'='*60}")
    logger.info("📊 决策排名:")
    for r in results:
        if r["rank"] > 0:
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(r["rank"], "  ")
            vol = r.get('vol_24h_usd', 0)
            if vol >= 1e9: vf = f"${vol/1e9:.2f}B"
            elif vol >= 1e6: vf = f"${vol/1e6:.2f}M"
            else: vf = f"${vol:,.0f}"
            logger.info(f"  {medal}{r['rank']}. {r['symbol']}  "
                        f"置信度 {r['confidence']['score']}/100  "
                        f"24h成交 {vf}")
        else:
            logger.info(f"  ⚠ {r['symbol']}  不推荐 "
                        f"({r.get('status','?')}) "
                        f"{r['confidence'].get('warnings',[])}")
    logger.info("=" * 60)

    return results


# ══════════════════════════════════════════════════════════════════
#  缓存
# ══════════════════════════════════════════════════════════════════

def save_decision_cache(results: List[Dict], config: Config,
                        coins_meta: List[Dict] = None) -> str:
    """缓存决策结果到 JSON 文件。"""
    cache_path = os.path.join(config.OUTPUT_DIR, "decision_latest.json")
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    cache = {
        "updated_at": datetime.now().isoformat(),
        "date": date.today().isoformat(),
        "coins_meta": coins_meta or [],
        "results": results,
    }

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, default=str)

    logger.info(f"决策缓存已保存: {cache_path}")
    return cache_path


def load_decision_cache(config: Config) -> Optional[Dict]:
    """加载缓存的决策结果。"""
    cache_path = os.path.join(config.OUTPUT_DIR, "decision_latest.json")
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
