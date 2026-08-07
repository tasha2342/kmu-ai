import re

from typing import Any


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 100,
    metadata: dict[str, Any] = None
) -> list[dict[str, Any]]:
    """텍스트를 청크로 분할합니다.
    
    전략 우선순위:
    1. 시맨틱 분할
    2. 문장 경계로 분할
    3. 고정 크기로 분할
    
    Args:
        text (str): 분할할 텍스트
        chunk_size (int): 청크의 목표 크기 (문자 수)
        chunk_overlap (int): 청크 간 오버랩 크기
        metadata (dict[str, Any]): 청크에 포함할 메타데이터
    
    Returns:
        list[dict[str, Any]]: 청크 리스트 (text, metadata 포함)
    """
    
    if not text or not text.strip():
        return []
    
    if metadata is None:
        metadata = {}
    
    # 1단계: 문단으로 분할 (빈 줄 기준)
    paragraphs = re.split(r'\n\s*\n', text)
    
    chunks = []
    current_chunk = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        # 문단이 chunk_size보다 작으면 현재 청크에 추가
        if len(current_chunk) + len(para) <= chunk_size:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
        else:
            # 현재 청크를 저장
            if current_chunk:
                chunks.append(current_chunk)
            
            # 문단이 chunk_size보다 크면 문장 단위로 분할
            if len(para) > chunk_size:
                sentences = _split_into_sentences(para)
                current_chunk = ""
                
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) <= chunk_size:
                        if current_chunk:
                            current_chunk += " " + sentence
                        else:
                            current_chunk = sentence
                    else:
                        if current_chunk:
                            chunks.append(current_chunk)
                        
                        # 문장이 chunk_size보다 크면 고정 크기로 분할
                        if len(sentence) > chunk_size:
                            fixed_chunks = _split_by_fixed_size(sentence, chunk_size, chunk_overlap)
                            chunks.extend(fixed_chunks[:-1])
                            current_chunk = fixed_chunks[-1] if fixed_chunks else ""
                        else:
                            current_chunk = sentence
            else:
                current_chunk = para
    
    # 마지막 청크 추가
    if current_chunk:
        chunks.append(current_chunk)
    
    # 오버랩 적용 및 메타데이터 추가
    result_chunks = []
    for i, chunk in enumerate(chunks):
        chunk_metadata = {
            **metadata,
            "chunk_index": i,
            "total_chunks": len(chunks)
        }
        
        result_chunks.append({
            "text": chunk,
            "metadata": chunk_metadata
        })
    
    # 오버랩 처리 (다음 청크의 시작 부분을 현재 청크에 추가)
    if chunk_overlap > 0 and len(result_chunks) > 1:
        for i in range(len(result_chunks) - 1):
            next_text = result_chunks[i + 1]["text"]
            overlap_text = next_text[:chunk_overlap]
            result_chunks[i]["text"] += "\n" + overlap_text
    
    return result_chunks

def _split_into_sentences(text: str) -> list[str]:
    """텍스트를 문장으로 분할합니다.
    
    Args:
        text (str): 분할할 텍스트
    
    Returns:
        list[str]: 문장 리스트
    """
    
    # 한글/영문 문장 경계 패턴 - . ! ? 뒤에 공백이나 줄바꿈이 오는 경우
    sentence_pattern = r'(?<=[.!?])\s+(?=[A-Z가-힣])|(?<=[.!?。！？])\s*'
    sentences = re.split(sentence_pattern, text)
    return [s.strip() for s in sentences if s.strip()]

def _split_by_fixed_size(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """텍스트를 고정 크기로 분할합니다.
    
    Args:
        text (str): 분할할 텍스트
        chunk_size (int): 청크 크기
        chunk_overlap (int): 오버랩 크기
    
    Returns:
        list[str]: 청크 리스트
    """
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - chunk_overlap
    
    return chunks

def estimate_chunk_count(text: str, chunk_size: int = 500) -> int:
    """텍스트의 예상 청크 개수를 계산합니다.
    
    Args:
        text (str): 텍스트
        chunk_size (int): 청크 크기
    
    Returns:
        int: 예상 청크 개수
    """
    
    if not text:
        return 0
    
    # 전체 길이 / 청크 크기
    estimated = max(1, len(text) // chunk_size)
    return estimated
