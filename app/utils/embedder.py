"""임베딩을 kmu-ai 컨테이너 안에서 직접 수행합니다.

임베딩 모델은 별도 추론 서버(TEI/vLLM 등)로 분리하지 않고 이 프로세스에 올린다.
모델과 관련된 처리를 kmu-ai가 전부 책임진다는 설계 원칙에 맞춘 것이고, 실제로도
임베딩 모델은 생성 모델과 달리 작아서(예: KURE-v1 = XLM-RoBERTa large, float32 2.2GB)
API 프로세스에 함께 올려도 부담이 없다. 덕분에 임베딩 모델도 `/v1/model/*`의
추가/실행/중지 흐름을 그대로 따르고, 네트워크 홉과 별도 컨테이너가 사라진다.

가중치는 `ModelProvider.LOCAL` 모델과 동일하게 `resources/models/<org>--<name>`에서
읽는다. (`app.utils.local_model.LocalModel`이 Hugging Face에서 내려받는 그 경로)

sentence-transformers 런타임을 통째로 얹지 않고, 저장소에 함께 배포되는
`modules.json` / `1_Pooling/config.json` / `sentence_bert_config.json`을 직접 읽어
풀링·정규화·최대 길이를 재현한다. transformers + torch만으로 동일한 벡터가 나온다.
"""

import asyncio
import json
import os

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.utils.logger import get_logger
import app.utils.resource_manager as rm


logger = get_logger("embedder", log_dir="logs")


DEFAULT_MAX_SEQ_LENGTH = 512
"""`sentence_bert_config.json`이 없을 때 쓰는 최대 토큰 길이"""

DEFAULT_BATCH_SIZE = 16
"""한 번의 forward에 넣을 문장 수 (CPU 기준 메모리와 지연의 절충값)"""

_TORCH_THREADS = int(os.getenv("EMBEDDING_TORCH_THREADS", "0")) or None
"""torch 연산 스레드 수 (0/미설정이면 torch 기본값)"""


@dataclass
class _PoolingConfig:
    """sentence-transformers 파이프라인에서 재현해야 하는 후처리 설정"""

    mode: str
    """풀링 방식 (`cls` 또는 `mean`)"""
    normalize: bool
    """L2 정규화 여부"""
    max_seq_length: int
    """최대 토큰 길이"""


def _read_json(path: Path) -> Optional[dict | list]:
    """JSON 파일을 읽습니다. (없거나 깨졌으면 None)"""

    try:
        with path.open(encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError):
        return None


def _load_pooling_config(model_dir: Path) -> _PoolingConfig:
    """모델 디렉토리에서 풀링/정규화/최대 길이 설정을 읽습니다.

    Args:
        model_dir (Path): 모델 디렉토리

    Returns:
        _PoolingConfig: 풀링 설정 (파일이 없으면 mean 풀링 + 정규화 없음)
    """

    mode = "mean"
    pooling = _read_json(model_dir.joinpath("1_Pooling", "config.json"))
    if isinstance(pooling, dict):
        if pooling.get("pooling_mode_cls_token"):
            mode = "cls"
        elif pooling.get("pooling_mode_mean_tokens"):
            mode = "mean"

    # modules.json에 Normalize 단계가 있으면 벡터를 L2 정규화해야 한다.
    # (정규화하면 코사인 거리와 내적이 같아지므로 pgvector 검색 결과가 학습 시점과 일치한다)
    normalize = False
    modules = _read_json(model_dir.joinpath("modules.json"))
    if isinstance(modules, list):
        normalize = any(
            str(module.get("type", "")).endswith("Normalize")
            for module in modules
            if isinstance(module, dict)
        )

    max_seq_length = DEFAULT_MAX_SEQ_LENGTH
    sbert = _read_json(model_dir.joinpath("sentence_bert_config.json"))
    if isinstance(sbert, dict) and isinstance(sbert.get("max_seq_length"), int):
        max_seq_length = sbert["max_seq_length"]

    return _PoolingConfig(mode=mode, normalize=normalize, max_seq_length=max_seq_length)


class LocalEmbedder:
    """로컬 가중치를 프로세스 안에서 실행하는 임베딩 모델

    Args:
        model_id (str): 모델 ID (예: `nlpai-lab/KURE-v1`)
    """

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.model_dir = rm.get_model_path(model_id)
        self._tokenizer = None
        self._model = None
        self._pooling: Optional[_PoolingConfig] = None
        self._dimensions: Optional[int] = None
        # forward는 GIL을 놓는 CPU 작업이라 이벤트 루프를 막지 않도록 스레드로 넘긴다.
        # 다만 동시에 여러 개를 돌리면 스레드 경합으로 오히려 느려지므로 워커는 1개만 둔다.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"embed-{model_id}")
        self._load_lock = asyncio.Lock()

    @property
    def is_loaded(self) -> bool:
        """모델이 메모리에 올라와 있는지 여부"""

        return self._model is not None

    @property
    def dimensions(self) -> Optional[int]:
        """임베딩 차원 (로드 전에는 None)"""

        return self._dimensions

    def _load_sync(self):
        """모델과 토크나이저를 메모리에 올립니다. (스레드에서 실행)"""

        import torch
        from transformers import AutoModel, AutoTokenizer

        if _TORCH_THREADS:
            torch.set_num_threads(_TORCH_THREADS)

        if not self.model_dir.exists():
            raise FileNotFoundError(
                f"모델 가중치가 없습니다. 모델을 다시 추가해 내려받아야 합니다. (path={self.model_dir})"
            )

        self._pooling = _load_pooling_config(self.model_dir)
        self._tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
        model = AutoModel.from_pretrained(str(self.model_dir), dtype=torch.float32)
        model.eval()
        self._model = model
        self._dimensions = int(model.config.hidden_size)

        # 토크나이저가 더 짧은 한계를 알고 있으면 그쪽을 따른다.
        tokenizer_max = getattr(self._tokenizer, "model_max_length", None)
        if isinstance(tokenizer_max, int) and 0 < tokenizer_max < self._pooling.max_seq_length:
            self._pooling.max_seq_length = tokenizer_max

        logger.info(
            f"임베딩 모델을 로드했습니다. "
            f"(model_id={self.model_id}, dim={self._dimensions}, pooling={self._pooling.mode}, "
            f"normalize={self._pooling.normalize}, max_seq_length={self._pooling.max_seq_length})"
        )

    def _encode_sync(self, texts: list[str], batch_size: int) -> tuple[list[list[float]], int]:
        """텍스트를 임베딩합니다. (스레드에서 실행)

        Returns:
            tuple[list[list[float]], int]: 임베딩 벡터 목록, 입력 토큰 수 합계
        """

        import torch

        vectors: list[list[float]] = []
        total_tokens = 0

        with torch.inference_mode():
            for start in range(0, len(texts), batch_size):
                batch = texts[start:start + batch_size]
                encoded = self._tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=self._pooling.max_seq_length,
                    return_tensors="pt",
                )
                attention_mask = encoded["attention_mask"]
                total_tokens += int(attention_mask.sum().item())

                hidden = self._model(**encoded).last_hidden_state

                if self._pooling.mode == "cls":
                    pooled = hidden[:, 0]
                else:
                    mask = attention_mask.unsqueeze(-1).to(hidden.dtype)
                    pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)

                if self._pooling.normalize:
                    pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)

                vectors.extend(pooled.tolist())

        return vectors, total_tokens

    async def load(self):
        """모델을 메모리에 올립니다. (이미 올라와 있으면 아무것도 하지 않음)"""

        if self.is_loaded:
            return

        async with self._load_lock:
            if self.is_loaded:
                return
            await asyncio.get_running_loop().run_in_executor(self._executor, self._load_sync)

    async def unload(self):
        """모델을 메모리에서 내립니다."""

        async with self._load_lock:
            self._model = None
            self._tokenizer = None
            self._pooling = None
            self._dimensions = None

        import gc

        gc.collect()
        logger.info(f"임베딩 모델을 메모리에서 내렸습니다. (model_id={self.model_id})")

    async def encode(
        self,
        texts: list[str],
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> tuple[list[list[float]], int]:
        """텍스트 목록을 임베딩합니다.

        Args:
            texts (list[str]): 임베딩할 텍스트 목록
            batch_size (int): 배치 크기 (Default: `DEFAULT_BATCH_SIZE`)

        Returns:
            tuple[list[list[float]], int]: 임베딩 벡터 목록, 입력 토큰 수 합계
        """

        if not texts:
            return [], 0

        await self.load()
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, self._encode_sync, texts, batch_size
        )


_embedders: dict[str, LocalEmbedder] = {}
"""모델 ID별 임베더 캐시 (가중치를 요청마다 다시 읽지 않도록 프로세스 수명 동안 유지)

캐시는 프로세스 단위다. gunicorn 워커가 4개면 임베딩 요청을 받은 워커마다 가중치를
따로 올리므로 최대 `워커 수 x 모델 크기`(KURE-v1 기준 약 2.3GB)만큼 메모리를 쓴다.
지연 로딩이라 실제로는 임베딩을 처리한 워커만 올라온다. 메모리가 빠듯하면
`scripts/launch.sh`의 워커 수를 줄이는 쪽으로 조절한다.
"""

_registry_lock = asyncio.Lock()


async def get_embedder(model_id: str) -> LocalEmbedder:
    """모델 ID에 해당하는 임베더를 반환합니다. (없으면 생성)

    Args:
        model_id (str): 모델 ID

    Returns:
        LocalEmbedder: 임베더
    """

    embedder = _embedders.get(model_id)
    if embedder is not None:
        return embedder

    async with _registry_lock:
        embedder = _embedders.get(model_id)
        if embedder is None:
            embedder = LocalEmbedder(model_id)
            _embedders[model_id] = embedder
        return embedder


async def unload_embedder(model_id: str):
    """모델 ID에 해당하는 임베더를 메모리에서 내립니다.

    Args:
        model_id (str): 모델 ID
    """

    async with _registry_lock:
        embedder = _embedders.pop(model_id, None)

    if embedder is not None:
        await embedder.unload()


def is_loaded(model_id: str) -> bool:
    """모델이 메모리에 올라와 있는지 확인합니다.

    Args:
        model_id (str): 모델 ID

    Returns:
        bool: 로드되어 있으면 True
    """

    embedder = _embedders.get(model_id)
    return embedder is not None and embedder.is_loaded
