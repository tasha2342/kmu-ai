import aiohttp


class NodeClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token
    
    async def _make_request(self, method: str, endpoint: str, timeout: aiohttp.ClientTimeout | None = None, **kwargs) -> tuple[int, dict]:
        session_kwargs = {}
        if timeout is not None:
            session_kwargs["timeout"] = timeout
        async with aiohttp.ClientSession(**session_kwargs) as session:
            kwargs["headers"] = {"Authorization": f"Bearer {self.token}"}
            async with getattr(session, method)(
                f"{self.base_url}{endpoint}",
                **kwargs
            ) as response:
                return response.status, await response.json()

    async def run_model(self, model_name: str, device_ids: list[int], gpu_memory_utilization: float) -> tuple[int, dict]:
        """노드에서 모델을 실행합니다.
        
        Args:
            model_name (str): 실행할 모델명
            device_ids (list[int]): 할당할 디바이스 ID 목록
            gpu_memory_utilization (float): 각 디바이스(GPU)에서 사용할 최대 메모리 비율
        
        Returns:
			tuple[int, dict]: 응답 상태 코드, JSON 데이터
        """
        
        return await self._make_request(
            "post", f"/v1/model/run/{model_name}",
            timeout=aiohttp.ClientTimeout(total=1800),
            json={
                "device_ids": device_ids,
                "gpu_memory_utilization": gpu_memory_utilization,
            }
        )

    async def stop_model(self, model_name: str) -> tuple[int, dict]:
        """노드에서 실행 중인 모델을 중지합니다.
        
        Args:
            model_name (str): 중지할 모델명
        
        Returns:
			tuple[int, dict]: 응답 상태 코드, JSON 데이터
        """
        
        return await self._make_request(
            "post", f"/v1/model/stop/{model_name}"
        )
