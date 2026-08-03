import aiohttp

from typing import Optional, Any

from app.config import config
from app.utils.logger import get_logger


logger = get_logger("doc_parser", log_dir="logs")


async def parse_document(file_data: bytes, file_name: str) -> Optional[dict[str, Any]]:
    """문서를 파싱하여 텍스트와 메타데이터를 추출합니다.
    
    Args:
        file_data (bytes): 파일 데이터
        file_name (str): 파일명
    
    Returns:
        Optional[dict[str, Any]]: 파싱 결과 (contents: [{"content": str, "page": int}], metadata 등)
    """
    
    try:
        form = aiohttp.FormData()
        form.add_field("file", file_data, filename=file_name)
        
        timeout = aiohttp.ClientTimeout(total=600)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {}
            if config.doc_parser.api_key:
                headers["Authorization"] = f"Bearer {config.doc_parser.api_key}"
            
            async with session.post(config.doc_parser.api_url, data=form, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"문서 파싱에 실패하였습니다. (HTTP {response.status}): {error_text}")
                    return None
                
                result = await response.json()
                
                if not result.get("results") or len(result["results"]) == 0:
                    logger.error("문서 파싱 결과가 비어있습니다.")
                    return None
                
                # 첫 번째 결과 반환
                parsed_doc = result["results"][0]
                
                return {
                    "contents": parsed_doc.get("contents", []),
                    "metadata": parsed_doc.get("metadata", {}),
                    "total_pages": result.get("total_pages", 0)
                }
    except aiohttp.ClientError:
        logger.exception("문서 파싱 API 호출 중 네트워크 오류가 발생했습니다.")
        return None
    except Exception:
        logger.exception("문서 파싱 중 알 수 없는 오류가 발생했습니다.")
        return None
