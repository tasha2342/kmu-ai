from app.scheduler.jobs.base import create_job


logger, job_decorator = create_job("dummy")


# 작업 함수 정의
@job_decorator
async def job():
    logger.info("Hello World!")
