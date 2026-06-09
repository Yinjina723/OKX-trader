# prepare_data.py
"""数据预下载脚本 —— 一次性拉取历史K线和资金费率，存为 Parquet 供回测使用

用法:
    python prepare_data.py                    # 下载 config.json 中 SYMBOL 列表全部交易对
    python prepare_data.py ALLO-USDT-SWAP     # 下载指定交易对

输出: {HISTORY_DIR}/kline_{symbol}.parquet + funding_{symbol}.parquet
"""

import os
import sys
import logging

import pandas as pd

from config import Config
from okx_client import OKXClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


def download_symbol(client: OKXClient, symbol: str, history_dir: str) -> bool:
    """下载单个交易对的 K 线和资金费率数据。"""
    safe_name = symbol.replace("-", "_")
    success = True

    # ═══ 1. K线数据 ═══
    log.info(f"[{symbol}] 下载日线K线 (最多300根)...")
    try:
        raw = client.get_klines(symbol, bar="1D", limit=300)
        if not raw:
            log.error(f"[{symbol}] K线数据为空")
            return False

        rows = client.parse_klines(raw)
        df_new = pd.DataFrame(rows)
        df_new["timestamp"] = pd.to_datetime(df_new["timestamp"], unit="ms")

        os.makedirs(history_dir, exist_ok=True)
        kline_path = os.path.join(history_dir, f"kline_{safe_name}.csv")

        # ── 合并已有数据（保留API返回范围之外的旧K线）──
        if os.path.exists(kline_path):
            df_old = pd.read_csv(kline_path, parse_dates=["timestamp"])
            df = pd.concat([df_old, df_new], ignore_index=True)
            log.info(f"[{symbol}] 合并旧数据 {len(df_old)} 根 + 新数据 {len(df_new)} 根")
        else:
            df = df_new

        # 去重（同一天以新数据为准，保留最后出现的）+ 排序
        df = df.drop_duplicates(subset=["timestamp"], keep="last")
        df = df.sort_values("timestamp").reset_index(drop=True)

        df.to_csv(kline_path, index=False)
        log.info(f"[{symbol}] K线已保存: {kline_path} ({len(df)} 根, "
                 f"{df['timestamp'].iloc[0].strftime('%Y-%m-%d')} → "
                 f"{df['timestamp'].iloc[-1].strftime('%Y-%m-%d')})")
    except Exception as e:
        log.error(f"[{symbol}] K线下载失败: {e}")
        success = False

    # ═══ 2. 1H K线数据（高频模式用）═══
    log.info(f"[{symbol}] 下载1H K线 (最多300根)...")
    try:
        raw_1h = client.get_klines(symbol, bar="1H", limit=300)
        if raw_1h:
            rows_1h = client.parse_klines(raw_1h)
            df_1h = pd.DataFrame(rows_1h)
            df_1h["timestamp"] = pd.to_datetime(df_1h["timestamp"], unit="ms")
            df_1h = df_1h.sort_values("timestamp").reset_index(drop=True)

            kline_1h_path = os.path.join(history_dir, f"kline_1H_{safe_name}.csv")
            if os.path.exists(kline_1h_path):
                df_old_1h = pd.read_csv(kline_1h_path, parse_dates=["timestamp"])
                df_1h = pd.concat([df_old_1h, df_1h], ignore_index=True)
                df_1h = df_1h.drop_duplicates(subset=["timestamp"], keep="last")
                df_1h = df_1h.sort_values("timestamp").reset_index(drop=True)
                log.info(f"[{symbol}] 合并旧1H数据 {len(df_old_1h)} 根 + 新 {len(rows_1h)} 根")

            df_1h.to_csv(kline_1h_path, index=False)
            log.info(f"[{symbol}] 1H K线已保存: {kline_1h_path} ({len(df_1h)} 根)")
    except Exception as e:
        log.warning(f"[{symbol}] 1H下载失败(非致命): {e}")

    # ═══ 3. 资金费率历史 ═══
    log.info(f"[{symbol}] 下载资金费率历史...")
    try:
        funding_raw = client.get_funding_rate_history(symbol, limit=90)
        if funding_raw:
            fdf = pd.DataFrame(funding_raw)
            fdf["fundingRate"] = fdf["fundingRate"].astype(float)
            fdf["fundingTime"] = pd.to_datetime(fdf["fundingTime"].astype(int), unit="ms")
            fdf = fdf.sort_values("fundingTime").reset_index(drop=True)

            funding_path = os.path.join(history_dir, f"funding_{safe_name}.csv")
            fdf.to_csv(funding_path, index=False)
            log.info(f"[{symbol}] 费率已保存: {funding_path} ({len(fdf)} 条)")
        else:
            log.warning(f"[{symbol}] 资金费率数据为空（部分交易对不支持）")
    except Exception as e:
        log.warning(f"[{symbol}] 费率下载失败(非致命): {e}")

    return success


def main():
    cfg = Config("config.json")
    client = OKXClient(cfg)
    history_dir = cfg.HISTORY_DIR

    # 命令行可指定交易对
    if len(sys.argv) > 1:
        symbols = [sys.argv[1]]
    else:
        symbols = cfg.SYMBOLS

    print("=" * 60)
    print("  数据预下载")
    print("=" * 60)
    print(f"  交易对: {symbols}")
    print(f"  保存路径: {history_dir}")
    print()

    for symbol in symbols:
        ok = download_symbol(client, symbol, history_dir)
        if ok:
            print(f"  ✅ {symbol} 下载完成")
        else:
            print(f"  ❌ {symbol} 下载失败")
        print()

    print("预下载完成！现在可以运行 backtest.py 进行回测了。")


if __name__ == "__main__":
    main()
