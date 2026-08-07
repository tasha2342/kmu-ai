import os
import asyncio
import aiofiles.os as aios

from typing import Optional

from huggingface_hub import snapshot_download

import app.utils.resource_manager as rm


EXCLUDE_FILE_LIST = [
    # Git files
    ".gitattributes",
    # README
    "README.md",
    # Image files
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.svg",
    # Not needed files
    "consolidated.safetensors",
]  # 모델 다운로드 시 제외 파일 목록

SNAPSHOT_MAX_WORKERS = 8


def _snapshot_download_sync(model_id: str, model_dir: str, token: Optional[str]) -> str:
    """Hugging Face에서 모델을 다운로드합니다.
    
    Args:
        model_id (str): 모델 ID
        model_dir (str): 모델 다운로드 경로
        token (Optional[str]): Hugging Face API 토큰
    
    Returns:
        str: 모델 다운로드 경로
    """
    
    return snapshot_download(
        repo_id=model_id,
        local_dir=model_dir,
        ignore_patterns=EXCLUDE_FILE_LIST,
        token=token,
        max_workers=SNAPSHOT_MAX_WORKERS,
    )


class LocalModel:
    def __init__(self, model_id: str, api_key: Optional[str] = None):
        self.model_id = model_id
        self.api_key = api_key
        self.model_dir = rm.get_model_path(self.model_id)

    async def clean_model(self):
        """기존에 다운로드된 모델 파일을 삭제합니다."""

        if await aios.path.exists(self.model_dir):
            for root, dirs, files in os.walk(self.model_dir, topdown=False):
                for name in files:
                    await aios.remove(os.path.join(root, name))
                for name in dirs:
                    await aios.rmdir(os.path.join(root, name))
            await aios.rmdir(self.model_dir)

    async def download_model(self):
        """모델을 Hugging Face에서 다운로드합니다."""

        await self.clean_model()
        await asyncio.to_thread(
            _snapshot_download_sync,
            self.model_id,
            str(self.model_dir),
            self.api_key,
        )

    async def is_downloaded(self) -> bool:
        """모델이 다운로드되어 있는지 확인합니다.

        모델 디렉토리가 존재하고, 필수 파일들이 있는지 확인합니다.

        Returns:
            bool: 모델이 다운로드되어 있으면 True, 아니면 False
        """

        # 모델 디렉토리가 존재하는지 확인
        if not self.model_dir.exists():
            return False

        # 디렉토리에 파일이 있는지 확인
        try:
            entries = await aios.listdir(self.model_dir)
            if not entries:
                return False

            # 필수 파일 확인 (config.json 또는 safetensors/bin 파일)
            has_config = any(entry == "config.json" for entry in entries)
            has_weights = any(
                entry.endswith(".safetensors") or entry.endswith(".bin")
                for entry in entries
            )

            # config.json이 있거나 weight 파일이 있으면 다운로드된 것으로 판단
            return has_config or has_weights
        except OSError:
            return False
