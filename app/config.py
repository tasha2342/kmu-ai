import os
import yaml
import toml

from pathlib import Path

from typing import Any

from app.models.config import EnvConfig, Config, MilvusConfig


# 환경 변수
env = EnvConfig()

project_name = "unknown"
project_version = "unknown"
pyproject_toml_file = Path("pyproject.toml")
if pyproject_toml_file.is_file():
    data = toml.load(pyproject_toml_file)
    project_name = data.get("project", {}).get("name")
    project_version = data.get("project", {}).get("version")

# FastAPI 설정
fastapi_config: dict[str, Any] = {
    "title": env.APP_TITLE,
    "version": project_version,
    "description": env.APP_DESCRIPTION,
    "root_path": env.APP_ROOT_PATH.rstrip("/"),
    "openapi_url": "/openapi.json" if env.ENABLE_OPENAPI else None,
    "docs_url": None,
    "redoc_url": None
}


CONFIG_FILE = "./configs/config.yaml"

# 설정 파일 존재 여부 확인
if not os.path.isfile(CONFIG_FILE):
    raise FileNotFoundError(f"'{CONFIG_FILE}' 설정 파일을 찾을 수 없습니다.")

# yaml 설정 파일 읽기
with open(CONFIG_FILE, "r", encoding="utf-8") as file:
    raw_config = yaml.load(file, Loader=yaml.FullLoader)

config = Config(
    database=raw_config["database"],
    redis=raw_config["redis"],
    auth=raw_config["auth"],
    s3=raw_config["s3"],
    doc_parser=raw_config.get("doc_parser") or {},
    memgraph=raw_config["memgraph"],
    # milvus 섹션이 없는 기존 config.yaml도 기동되게 기본값(enabled=False)을 쓴다.
    milvus=raw_config.get("milvus") or MilvusConfig(),
    chatbot=raw_config["chatbot"],
    external_links=raw_config.get("external_links", []),
)
