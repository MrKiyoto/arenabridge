"""
Точка входа для запуска сервера через python -m server.
"""

import sys
import uvicorn
from .config import load_config

def main():
    config = load_config()
    uvicorn.run(
        "server.main:app",
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        access_log=False if config.log_level != "DEBUG" else True,
    )

if __name__ == "__main__":
    main()
