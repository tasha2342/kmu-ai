"""Gemma-4-31B 텍스트 생성 / 비전 표 독해.

두 곳에 씁니다.

- **R7 (end-to-end)**: 검색 근거를 넣고 답을 생성. rag-test의 E4_gen(79.2%)에 대응.
- **T3/T4 (표 비전)**: 값 셀이 빈 표를 이미지로 보여 주고 읽게 함. E5(100%)에 대응.

rag-test의 E5는 질문 ID → 이미지 키를 **손으로 매핑**했습니다(`QUESTION_IMAGES` 딕셔너리).
그래서 검색 단계가 없었고 recall@k가 정의상 1.0이었습니다. 여기서는 그 매핑을 쓰지 않고
**검색된 표 청크의 `table_id`로 이미지를 찾습니다.** 리포트 10.5절이 남긴 "E5 → RAG 통합"입니다.
결과적으로 T3/T4 수치는 E5의 100%보다 낮게 나올 수 있지만, 그게 실제 시스템의 성능입니다.
"""

from __future__ import annotations

import re

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional


MODEL_ID = "google/gemma-4-31B-it"

TEXT_SYSTEM = (
    "너는 계명대학교 규정집을 읽고 답하는 도우미다.\n"
    "규칙:\n"
    "1) [검색 근거]에 있는 내용만으로 답한다. 없으면 '자료에서 확인할 수 없습니다'라고 답한다.\n"
    "2) 날짜는 네 종류가 있고 서로 다르다 — 파일명 시행일(현행), 최신 부칙 시행일,\n"
    "   조항 개정일, 과거 부칙 시행일. 질문이 묻는 종류를 골라 답한다.\n"
    "3) 표의 금액·인원 셀이 비어 있으면 추정하지 말고 모른다고 답한다.\n"
    "4) 답은 짧게. 날짜는 `날짜: YYYY-MM-DD`, 수치는 `값: N` 형식으로 첫 줄에 쓴다."
)

VISION_SYSTEM = (
    "너는 한국 대학 규정집의 표 이미지를 읽는다.\n"
    "이미지에 보이는 것만으로 답한다. 셀이 비어 있거나 ∅ 로 표시돼 있으면 모른다고 답한다.\n"
    "봉급·인원 수치를 지어내지 마라. 값이 있으면 그 값을 정확히 한국어로 짧게 답한다."
)

ANSWER_LINE_RE = re.compile(r"^\s*(?:날짜|값)\s*:\s*(.+)$", re.MULTILINE)


@lru_cache(maxsize=1)
def _load(vision: bool) -> tuple[Any, Any]:
    """모델과 프로세서를 로드합니다. 텍스트/비전 모두 같은 체크포인트입니다."""

    import torch
    from transformers import AutoProcessor, AutoModelForImageTextToText

    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model = AutoModelForImageTextToText.from_pretrained(
        MODEL_ID,
        dtype=torch.bfloat16,
        device_map="auto",
    ).eval()
    return processor, model


def _run(messages: list[dict[str, Any]], max_new_tokens: int = 256) -> str:
    processor, model = _load(vision=True)
    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        add_generation_prompt=True,
    ).to(model.device)

    input_len = inputs["input_ids"].shape[-1]

    import torch

    with torch.no_grad():
        outputs = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)

    response = processor.decode(outputs[0][input_len:], skip_special_tokens=False)

    # Gemma 4는 thinking을 끌 수 없습니다(상위 CLAUDE.md 참고). 사고 과정을 답변에서 걷어냅니다.
    try:
        parsed = processor.parse_response(response)
        content = parsed["content"] if isinstance(parsed, dict) else str(parsed)
    except Exception:
        content = response
    content = re.sub(r"<thought>.*?</thought>", "", content, flags=re.DOTALL)
    # 템플릿 잔여 토큰. `<turn|>`이 답변 끝에 붙어 나와 정규화를 방해합니다.
    for token in ("<end_of_turn>", "<eos>", "<turn|>", "<start_of_turn>"):
        content = content.replace(token, "")
    return content.strip()


def generate_answer(question: str, evidence: str, max_new_tokens: int = 256) -> str:
    """검색 근거를 넣고 답을 생성합니다. (R7)"""

    user = f"[검색 근거]\n{evidence}\n\n[질문]\n{question}"
    return _run(
        [
            {"role": "system", "content": [{"type": "text", "text": TEXT_SYSTEM}]},
            {"role": "user", "content": [{"type": "text", "text": user}]},
        ],
        max_new_tokens=max_new_tokens,
    )


def answer_with_images(question: str, image_paths: list[Path], max_new_tokens: int = 256) -> str:
    """표 이미지를 보고 답합니다. (T3/T4)"""

    from PIL import Image

    images = [Image.open(p).convert("RGB") for p in image_paths if Path(p).exists()]
    if not images:
        return ""

    content: list[dict[str, Any]] = [{"type": "image", "image": img} for img in images]
    content.append({"type": "text", "text": question})
    return _run(
        [
            {"role": "system", "content": [{"type": "text", "text": VISION_SYSTEM}]},
            {"role": "user", "content": content},
        ],
        max_new_tokens=max_new_tokens,
    )


def extract_answer_value(text: str) -> str:
    """`날짜: 2020-12-15` / `값: 267` 형식의 첫 줄을 뽑습니다.

    리포트 10.3절이 지적한 "생성 답변이 장황해 정규화에 실패한다"를 막기 위한 것입니다.
    형식이 안 지켜졌으면 원문을 그대로 돌려줍니다(채점기가 알아서 정규화합니다).
    """

    m = ANSWER_LINE_RE.search(text or "")
    return m.group(1).strip() if m else (text or "").strip()
