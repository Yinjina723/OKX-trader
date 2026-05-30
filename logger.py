# logger.py
"""
全局日志配置：控制台 + 按天滚动的文件（OUTPUT_DIR/botlog.txt），最多保留 7 天。
主程序与 Web 启动时调用 setup_logger(config) 确保日志落盘。
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler


def setup_logger(config):
    """
    配置全局日志记录器，输出到控制台和文件。
    日志文件位于 config.OUTPUT_DIR，按天分割（bot.txt, bot.txt.2026-02-24 等）。
    """
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    if logger.hasHandlers():
        logger.handlers.clear()

    log_dir = config.OUTPUT_DIR
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "botlog.txt")

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
    )

    # 按天分割日志：每天 0 点生成一个新文件，最多保留 7 天
    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
        utc=False,
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger