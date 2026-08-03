import functools
import time

from logging import Logger

from typing import Callable

from app.utils.logger import get_logger


def create_job(job_name: str, show_start_end_log: bool = True) -> tuple[Logger, Callable]:
    """작업을 생성하는 함수입니다.
    
    Args:
        job_name (str): 작업 이름
        show_start_end_log (bool): 시작/종료 로그를 출력하는 데코레이터를 포함합니다.
    
    Returns:
        tuple: (logger, job_decorator) - 로거 객체, 작업 데코레이터를 포함하는 튜플
    """
    
    # 로거 생성
    logger = get_logger(f"job.{job_name}", log_dir="logs")
    
    # 작업 데코레이터
    def job_decorator(func: Callable) -> Callable:
        """작업 실행 전후에 로그를 출력하는 데코레이터입니다.
        
        Args:
            func (Callable): 실행할 작업 함수
            
        Returns:
            Callable: 데코레이터
        """
        
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                if show_start_end_log:
                    logger.debug("작업을 시작합니다.")
                    start_time = time.time()
                    result = await func(*args, **kwargs)
                    end_time = time.time()
                    logger.debug(f"작업이 완료되었습니다. ({end_time - start_time:.2f}s)")
                else:
                    result = await func(*args, **kwargs)
                return result
            except Exception as exc:
                logger.exception("작업 실행 중 오류가 발생했습니다.")
                raise exc
        return wrapper
    
    return logger, job_decorator
