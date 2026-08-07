from typing import Optional, Any

from neo4j import GraphDatabase, Driver, Session

from app.config import config
from app.utils.logger import get_logger


logger = get_logger("memgraph", log_dir="logs")


class MemgraphManager:
    """Memgraph 관리 클래스"""
    
    def __init__(self):
        uri = f"bolt://{config.memgraph.host}:{config.memgraph.port}"
        
        auth = None
        if config.memgraph.username and config.memgraph.password:
            auth = (config.memgraph.username, config.memgraph.password)
        
        # 기본값(connection_timeout=30s, max_transaction_retry_time=30s)에 맡기면 Memgraph가
        # 죽어있을 때 execute_read/execute_write 한 번이 수십 초씩 걸린다(직접 겪은 문제: 시민 챗봇의
        # GIS 조회가 55초 넘게 걸리다 폴백됨). 이 인스턴스는 미배포 상태라 짧게 끊어서 빨리 실패시킨다.
        self.driver: Driver = GraphDatabase.driver(
            uri,
            auth=auth,
            encrypted=config.memgraph.encrypted,
            connection_timeout=3,
            max_transaction_retry_time=3,
        )
        logger.info(f"Memgraph Manager가 초기화되었습니다.")
    
    def close(self):
        """드라이버 연결을 종료합니다."""
        
        if self.driver:
            self.driver.close()
            logger.info("Memgraph 연결이 종료되었습니다.")
    
    def get_session(self) -> Session:
        """세션을 반환합니다."""
        
        return self.driver.session()
    
    def execute_read(self, query: str, parameters: Optional[dict] = None) -> list[dict]:
        """읽기 쿼리를 실행하고 결과를 반환합니다.
        
        Args:
            query (str): Cypher 쿼리
            parameters (Optional[dict]): 쿼리 파라미터
        
        Returns:
            list[dict]: 쿼리 결과
        """
        
        def _execute_read_tx(tx):
            result = tx.run(query, parameters or {})
            return [record.data() for record in result]
        
        with self.get_session() as session:
            return session.execute_read(_execute_read_tx)
    
    def execute_write(self, query: str, parameters: Optional[dict] = None) -> Any:
        """쓰기 쿼리를 실행합니다.
        
        Args:
            query (str): Cypher 쿼리
            parameters (Optional[dict]): 쿼리 파라미터
        
        Returns:
            Any: 쿼리 결과
        """
        
        def _execute_write_tx(tx):
            result = tx.run(query, parameters or {})
            return result.consume()
        
        with self.get_session() as session:
            return session.execute_write(_execute_write_tx)
    
    def health_check(self) -> bool:
        """Memgraph 연결 상태를 확인합니다.
        
        Returns:
            bool: 연결 성공 여부
        """
        
        try:
            with self.get_session() as session:
                session.run("RETURN 1")
            return True
        except Exception:
            logger.exception("Memgraph 연결 확인 중 오류가 발생했습니다.")
            return False


_memgraph_manager: Optional[MemgraphManager] = None


def get_memgraph_manager() -> MemgraphManager:
    """Memgraph Manager 인스턴스를 반환합니다.
    
    Returns:
        MemgraphManager: Memgraph Manager 인스턴스
    """
    
    global _memgraph_manager
    if _memgraph_manager is None:
        _memgraph_manager = MemgraphManager()
    return _memgraph_manager
