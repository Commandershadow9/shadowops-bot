"""
Logging-Setup für ShadowOps Bot
Colored Console + File Logging
"""

import logging
import coloredlogs
from pathlib import Path
from datetime import datetime


def setup_logger(name: str = "shadowops", debug: bool = False) -> logging.Logger:
    """
    Erstellt und konfiguriert Logger mit Console + File Output

    Args:
        name: Logger-Name
        debug: Debug-Modus aktivieren

    Returns:
        Konfigurierter Logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.handlers.clear()

    # Console Handler mit Farben
    coloredlogs.install(
        level='DEBUG' if debug else 'INFO',
        logger=logger,
        fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        level_styles={
            'debug': {'color': 'cyan'},
            'info': {'color': 'green'},
            'warning': {'color': 'yellow', 'bold': True},
            'error': {'color': 'red', 'bold': True},
            'critical': {'color': 'red', 'bold': True, 'background': 'white'},
        },
        field_styles={
            'asctime': {'color': 'blue'},
            'levelname': {'bold': True},
            'name': {'color': 'magenta'},
        }
    )

    # File Handler
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f"shadowops_{datetime.now().strftime('%Y%m%d')}.log"

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    return logger
