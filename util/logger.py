import logging
import os
from logging.handlers import TimedRotatingFileHandler

import util.config

LOGGER_NAME = "crawler"
LOG_PATH = os.path.join(os.getcwd(), "logs", f"{LOGGER_NAME}.log")
LOGGER_PRINT_FORMAT = "\033[1;33m%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)s) %(funcName)s:\033[0m\n%(message)s"
LOGGER_FILE_FORMAT = "%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)s) %(funcName)s:\n%(message)s"
logging.basicConfig(format=LOGGER_PRINT_FORMAT)

def get_logger() -> logging.Logger:
    global logger
    return logger

def init_logger() -> logging.Logger:
    global logger
    try:
        return logger
    except NameError:
        config_dict = util.config.get_config_dict()
        is_debug = config_dict["logger"]["debug"]
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        logger = logging.getLogger(LOGGER_NAME)
        handler = TimedRotatingFileHandler(LOG_PATH, when="midnight", interval=1, encoding="UTF-8")
        handler.setFormatter(logging.Formatter(LOGGER_FILE_FORMAT))
        # 从配置文件接收是否打印debug日志
        if is_debug:
            logger.setLevel(level=logging.DEBUG)
            handler.level = logging.DEBUG
        else:
            logger.setLevel(logging.INFO)
            handler.level = logging.INFO
        logger.addHandler(handler)
        return logger