import os
import fcntl
import asyncio

from contextlib import asynccontextmanager

from string import Template


lock_root_path = Template("/tmp/locks/${lock_name}.lock")


@asynccontextmanager
async def lock(lock_name: str):
    """멀티 프로세스 환경에서 Lock 파일 접근을 동기화합니다.
    
    Args:
		lock_name (str): Lock 이름
    """
    
    # 락 파일 열기
    lock_file_path = lock_root_path.substitute(lock_name=lock_name)
    os.makedirs(os.path.dirname(lock_file_path), mode=0o777, exist_ok=True)
    lock_fd = os.open(lock_file_path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        # 다른 프로세스가 락을 해제할 때까지 대기
        await asyncio.get_event_loop().run_in_executor(
            None, fcntl.flock, lock_fd, fcntl.LOCK_EX
        )
        yield
    finally:
        # 락 해제
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

def is_locked(lock_name: str) -> bool:
    """락이 획득되어 있는지 확인합니다.

    Args:
        lock_name (str): Lock 이름

    Returns:
    	bool: 다른 프로세스가 락을 보유하고 있으면 True
    """
    
    try:
        # 락 시도
        lock_file_path = lock_root_path.substitute(lock_name=lock_name)
        os.makedirs(os.path.dirname(lock_file_path), mode=0o777, exist_ok=True)
        lock_fd = os.open(lock_file_path, os.O_RDWR | os.O_CREAT, 0o666)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # 락 획득 성공
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            # 락 획득 실패
            return True
        finally:
            os.close(lock_fd)
    except Exception:
        return False
