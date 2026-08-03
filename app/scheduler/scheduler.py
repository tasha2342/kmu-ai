import os

from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from apscheduler.executors.asyncio import AsyncIOExecutor

from app.scheduler.jobs import __all__ as jobs
from app.utils.logger import get_logger


class Scheduler:
    """APScheduler를 사용한 백그라운드 작업 스케줄러 클래스"""
    
    def __init__(self):
        self.logger = get_logger("scheduler", log_dir="logs")
        
        # 즉시 실행 플래그
        self._immediate_jobs: dict[str, bool] = {}
        
        # 작업 저장소 설정
        jobstores = {
            "default": MemoryJobStore()
        }
        
        # 실행자 설정 (AsyncIO 실행자, 스레드 10개, 프로세스 3개)
        executors = {
            "default": AsyncIOExecutor(),
            "threadpool": ThreadPoolExecutor(int(os.getenv("SCHEDULER_THREAD_POOL_SIZE", 10))),
            "processpool": ProcessPoolExecutor(int(os.getenv("SCHEDULER_PROCESS_POOL_SIZE", 3)))
        }
        
        # 작업 기본 설정
        job_defaults = {
            "coalesce": True,
            "max_instances": int(os.getenv("SCHEDULER_MAX_INSTANCES", len(jobs) - 1))
        }
        
        # 스케줄러 생성
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults
        )
        
        self._is_running = False
    
    @property
    def is_running(self) -> bool:
        """스케줄러 실행 상태를 반환합니다.
        
        Returns:
            bool: 스케줄러 실행 중 여부
        """
        
        return self._is_running
    
    def start(self) -> None:
        """스케줄러를 시작합니다."""
        
        if not self._is_running:
            self.scheduler.start()
            self._is_running = True
            self.logger.info("스케줄러가 시작되었습니다.")
    
    def stop(self) -> None:
        """스케줄러를 종료합니다."""
        
        if self._is_running:
            self.scheduler.shutdown()
            self._is_running = False
            self.logger.info("스케줄러가 종료되었습니다.")
    
    def add_job(
        self,
        func: Callable,
        job_id: str,
        trigger: str = "interval",
        run_immediately: bool = False,
        **kwargs: Any
    ) -> None:
        """스케줄러에 작업을 추가합니다.
        
        Args:
            func (Callable): 실행할 함수
            job_id (str): 작업 ID
            trigger (str, optional): 트리거 유형 (interval, cron, date). 기본값: interval
            **kwargs: 추가 매개변수
        """
        
        try:
            self.scheduler.add_job(
                func=func,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                **kwargs
            )
            self._immediate_jobs[job_id] = run_immediately
            self.logger.info(f"작업 추가 성공: {job_id}, 즉시 실행: {run_immediately}, 트리거: {trigger}, 매개변수: {kwargs}")
        except Exception:
            self.logger.exception(f"작업 추가 실패: {job_id}")
    
    def remove_job(self, job_id: str) -> None:
        """스케줄러에서 작업을 제거합니다.
        
        Args:
            job_id (str): 작업 ID
        """
        
        try:
            self.scheduler.remove_job(job_id)
            self.logger.info(f"작업 제거 성공: {job_id}")
        except Exception:
            self.logger.exception(f"작업 제거 실패: {job_id}")
    
    def get_jobs(self) -> list:
        """스케줄러에 등록된 모든 작업을 조회합니다.
        
        Returns:
            list: 작업 목록
        """
        
        return self.scheduler.get_jobs()

    def get_run_immediate_flag(self, job_id: str) -> bool:
        """등록된 작업의 즉시 실행 여부 반환
        
        Args:
            job_id (str): 작업 ID
        
        Returns:
            bool: 즉시 실행 여부
        """
        
        return self._immediate_jobs.get(job_id, False)


scheduler = Scheduler() 
