"""应用日志初始化。"""

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """使用统一格式初始化根日志；不记录文档正文和密钥。"""

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    # DEBUG 也禁止第三方 HTTP/SQL 调试日志输出头部和请求参数。
    for name in ("httpx", "httpcore", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(logging.WARNING)
