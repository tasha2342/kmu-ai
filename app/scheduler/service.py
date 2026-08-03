import asyncio
import inspect

from typing import Any

from app.scheduler.scheduler import scheduler
import app.utils.common as util


class SchedulerService:
    """백그라운드 스케줄러 서비스 관리 클래스"""

    def __init__(self):
        self.scheduler = scheduler
    
    def start(self) -> None:
        """워커 서비스를 시작합니다."""

        if not self.scheduler.is_running:
            self.scheduler.start()
            
            # 즉시 실행 작업 실행
            for job in self.scheduler.get_jobs():
                if hasattr(self.scheduler, "get_run_immediate_flag") and self.scheduler.get_run_immediate_flag(job.id):
                    func = job.func
                    args = job.args if hasattr(job, "args") else ()
                    kwargs = job.kwargs if hasattr(job, "kwargs") else {}
                    if inspect.iscoroutinefunction(func):
                        asyncio.create_task(func(*args, **kwargs))
                    else:
                        func(*args, **kwargs)
    
    def stop(self) -> None:
        """워커 서비스를 종료합니다."""
        
        if self.scheduler.is_running:
            self.scheduler.stop()

    def get_job_status(self) -> list[dict[str, Any]]:
        """등록된 모든 작업의 상태 정보를 반환합니다.
        
        Returns:
            list[dict[str, Any]]: 작업 상태 정보 목록
        """
        
        jobs = self.scheduler.get_jobs()
        job_status = []
        
        for job in jobs:
            job_status.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": util.format_datetime(job.next_run_time) if job.next_run_time else None,
                "trigger": str(job.trigger),
                "executor": job.executor
            })
        
        return job_status


scheduler_service = SchedulerService()
