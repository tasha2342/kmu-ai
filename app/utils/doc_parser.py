"""문서 파싱 진입점입니다.

과거 외부 Doc Parser HTTP API를 호출했으나, 현재는 로컬 파서
(`app.utils.local_doc_parse`)로 위임합니다. import 경로 호환을 위해 모듈명을 유지합니다.
"""

from typing import Any, Optional

from app.utils.local_doc_parse import parse_local_document, parsed_to_legacy_dict
from app.utils.logger import get_logger


logger = get_logger("doc_parser", log_dir="logs")


async def parse_document(file_data: bytes, file_name: str) -> Optional[dict[str, Any]]:
    """문서를 로컬에서 파싱하여 텍스트와 메타데이터를 추출합니다.

    Args:
        file_data (bytes): 파일 데이터
        file_name (str): 파일명

    Returns:
        Optional[dict[str, Any]]: 파싱 결과 (contents / metadata / total_pages)
    """

    parsed = await parse_local_document(file_data, file_name, for_chat=False)
    result = parsed_to_legacy_dict(parsed)
    if result is None:
        logger.error(f"문서 로컬 파싱에 실패하였습니다. (file_name={file_name})")
    return result
