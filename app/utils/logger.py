import logging
import os
import pytz
import orjson

from datetime import datetime

from logging.handlers import TimedRotatingFileHandler

from rich.logging import RichHandler
from rich.console import Console

from app.config import env
from app.utils.common import get_timezone
import app.utils.common as util


# Rich Console 인스턴스를 전역으로 관리
_rich_console = None

def get_rich_console() -> Console:
    """Rich Console 인스턴스를 반환합니다.
    
    Returns:
        Console: Rich Console 인스턴스
    """
    
    global _rich_console
    if _rich_console is None:
        is_docker = util.is_docker_environment()
        _rich_console = Console(
            color_system="256" if is_docker else "auto",
            width=120 if is_docker else None,
        )
    return _rich_console


class WorkerIdFilter(logging.Filter):
    def filter(self, record):
        record.worker_id = os.getpid()
        return True


class CustomRichHandler(RichHandler):
    def __init__(self, *args, timezone=pytz.UTC, **kwargs):
        tz = timezone or pytz.UTC

        def _log_time_format(dt: datetime) -> str:
            if dt.tzinfo is None:
                dt = tz.localize(dt)
            dt = dt.astimezone(tz)
            return util.format_datetime(dt, "%Y-%m-%d %H:%M:%S")

        is_docker = util.is_docker_environment()
        
        # 전역 Console 인스턴스 사용
        console = get_rich_console()
        
        super().__init__(
            level=env.LOG_LEVEL.upper(),
            console=console,
            show_time=True,
            show_level=True,
            show_path=not is_docker,
            markup=True,
            log_time_format=_log_time_format,
            **kwargs,
        )
        self.timezone = tz

    def format(self, record):
        worker_msg = f"[{record.worker_id}] " if hasattr(record, "worker_id") else ""
        record.msg = f"{worker_msg}[cyan]{record.name:15}[/cyan] - {record.getMessage()}"
        record.args = ()  # 추가 문자열 포맷팅 방지
        return super().format(record)

class MakeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """시간 기반 파일 Handler 클래스"""
    
    def __init__(self, filename, when="midnight", interval=1, backupCount=30, encoding="utf8", utc=True):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        super().__init__(filename, when, interval, backupCount, encoding, utc)

class JsonFormatter(logging.Formatter):
    """JSONL 형식으로 로그를 포맷하는 클래스"""
    
    def __init__(self, timezone=pytz.UTC):
        super().__init__()
        self.timezone = timezone
    
    def format(self, record: logging.LogRecord) -> str:
        """로그 레코드를 JSON 형식으로 포맷합니다.

        Args:
            record (logging.LogRecord): 로그 Record

        Returns:
            str: JSON 형식의 로그 문자열
        """
        
        dt = datetime.fromtimestamp(record.created, pytz.UTC).astimezone(self.timezone)
        
        # 원본 메시지만 사용
        original_message = record.getMessage().split("-", 1)[-1].strip()
        
        log_entry = {
            "timestamp": util.format_datetime(dt, "%Y-%m-%d %H:%M:%S.%f")[:-3],
            "level": record.levelname,
            "logger": record.name,
            "worker_id": getattr(record, "worker_id", os.getpid()),
            "message": original_message,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # 예외 정보가 있는 경우 추가
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
            
        return orjson.dumps(log_entry).decode()


logger_handlers = {}


def get_logger(
    name: str,
    level=None,
    log_dir=None,
    timezone=None,
    stream=True,
    file=True,
    backup_count=None
) -> logging.Logger:
    """Logger 인스턴스를 생성하여 반환합니다.
    
    이미 생성된 Logger 인스턴스가 있는 경우 해당 인스턴스를 반환합니다.

    Args:
        name (str): Logger 이름
        level (str, optional): 로그 레벨
        log_dir (str, optional): 로그 디렉토리
        timezone (str, optional): 시간대
        stream (bool, optional): 콘솔 출력 여부
        file (bool, optional): 파일 출력 여부
        backup_count (int, optional): 백업 파일 수

    Returns:
        logging.Logger: Logger 인스턴스
    """
    
    if name in logger_handlers:
        return logger_handlers[name]
    
    logger = logging.getLogger(name)
    if timezone is None:
        timezone = get_timezone(env.TZ)

    if logger.name != "root":
        if level is None:
            log_level = env.LOG_LEVEL
            logger.setLevel(log_level.upper())
        else:
            logger.setLevel(level)

    # 상위 로거로 전파하지 않도록 설정
    logger.propagate = False

    if stream:
        stream_handler = CustomRichHandler(timezone=timezone)
        stream_handler.addFilter(WorkerIdFilter())
        logger.addHandler(stream_handler)

    if file and log_dir is not None:
        path_name = name.replace(".", "/")
        os.makedirs(os.path.join(log_dir, path_name), exist_ok=True)
        log_file = os.path.join(log_dir, path_name, "log.jsonl")

        file_handler = MakeTimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=backup_count or env.LOG_BACKUP_COUNT,
            encoding="utf8",
            utc=True
        )
        file_formatter = JsonFormatter(timezone=timezone)
        file_handler.setFormatter(file_formatter)
        file_handler.addFilter(WorkerIdFilter())
        logger.addHandler(file_handler)

    logger_handlers[name] = logger
    return logger

def refresh_log_level() -> None:
    """로그 레벨을 새로 설정합니다."""
    
    log_level = env.LOG_LEVEL.upper()
    for handler in logger_handlers.values():
        handler.setLevel(log_level)

def get_api_logger() -> logging.Logger:
    """API 로그 출력을 위한 Logger 인스턴스를 생성하여 반환합니다.

    Returns:
        logging.Logger: Logger 인스턴스
    """
    
    return get_logger("api", log_dir="logs")
