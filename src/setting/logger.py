
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from src.setting.config import DIR_LOG


def get_logger(name: str) -> logging.Logger:
    _logger = logging.getLogger(name)

    if not _logger.handlers:
        os.makedirs(DIR_LOG, exist_ok=True)
        log_file = DIR_LOG / "server.log"
        log_file = os.path.join(os.getcwd(), log_file)
        
        _logger.setLevel(logging.INFO)

        formatter = logging.Formatter(
            fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt = "%Y-%m-%d %H:%M:%S"
        )

        # STDOUT/STDERR: For warnings/errors only to keep the mcp's stdio transport clean.
        stderr_h = logging.StreamHandler(sys.stderr)
        stderr_h.setLevel(logging.WARNING) 
        stderr_h.setFormatter(formatter)
        _logger.addHandler(stderr_h)

        # 4. ARCHIVO ROTATIVO: Toda la info sin llenar el disco (potencia técnica)
        file_h = RotatingFileHandler(log_file, maxBytes=2*1024*1024, backupCount=2)
        file_h.setLevel(logging.INFO)
        file_h.setFormatter(formatter)
        _logger.addHandler(file_h)
    
    return _logger
