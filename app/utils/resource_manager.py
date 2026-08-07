import os

from pathlib import Path

from app.config import env


ROOT_PATH = Path(os.getcwd()).joinpath("resources")


def get_root_path() -> Path:
    """리소스 루트 경로를 반환합니다.

    Returns:
        str: 리소스 루트 경로
    """
    
    return ROOT_PATH

def get_model_path(model_id: str) -> Path:
    """모델 경로를 반환합니다.

    Args:
        model_id (str): 모델 ID
        
    Returns:
        Path: 모델 경로
    """
    
    model_id_dir = model_id.replace("/", "--")
    return get_root_path().joinpath("models", model_id_dir)
