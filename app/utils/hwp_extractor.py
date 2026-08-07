"""학칙·규정 HWP 문서에서 본문 텍스트와 표를 추출합니다.

`pyhwp` 패키지가 제공하는 CLI 도구(`hwp5txt`, `hwp5html`)를 subprocess로 호출합니다.
파이썬 API를 직접 쓰지 않는 이유는, HWP 파일마다 손상된 레코드가 섞여 있어
라이브러리 내부에서 예외가 나면 프로세스 상태가 오염되는 경우가 있기 때문입니다.
별도 프로세스로 격리하면 문서 한 개가 깨져도 나머지 적재가 그대로 진행됩니다.

추출 전략의 근거는 `rag-test/docs/rag_test_report.md` 3절·6.2절입니다.

- 본문은 `hwp5txt`로 뽑습니다. 다만 `hwp5txt`는 표를 `<표>` placeholder로만 남기므로
  표 안의 수치는 본문 텍스트만으로는 절대 복구할 수 없습니다(리포트 6.2절).
- 그래서 표는 `hwp5html`로 HTML을 생성한 뒤 `<table>`을 따로 파싱합니다.
  이 표 청크를 추가한 것만으로 표 질의 정확도가 45.8% → 79.2%로 올랐습니다(리포트 5.1절 E3).
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata

from pathlib import Path
from typing import Any, Optional

from lxml import html as lxml_html

from app.utils.logger import get_logger


try:
    logger = get_logger("hwp_extractor", log_dir="logs")
except OSError:
    # 로그 디렉터리에 쓸 수 없는 환경(테스트·샌드박스)에서 임포트 자체가 실패하면
    # 청킹 테스트까지 같이 죽습니다. 이때는 콘솔 전용 Logger로 물러섭니다.
    logger = get_logger("hwp_extractor")


# 추출 타임아웃(초). 손상된 HWP에서 hwp5html이 멈추는 사례가 있어 상한을 둡니다.
EXTRACT_TIMEOUT = 120

# 텍스트 캐시 디렉터리 이름. 181개 문서 재추출은 수 분이 걸리므로 기본으로 캐싱합니다.
CACHE_DIR_NAME = ".cache"


def _find_hwp5_binary(name: str) -> Optional[str]:
    """`pyhwp` CLI 실행 파일의 절대 경로를 찾습니다.

    현재 파이썬(`sys.executable`)과 같은 venv의 `bin/`을 먼저 봅니다.
    시스템에 다른 버전의 pyhwp가 깔려 있어도 앱이 쓰는 venv의 것을 쓰도록 하기 위함입니다.
    venv에 없으면 `PATH`에서 찾습니다.

    Args:
        name (str): 실행 파일 이름 (예: "hwp5txt")

    Returns:
        Optional[str]: 실행 파일 절대 경로. 찾지 못하면 None
    """

    venv_bin = Path(sys.executable).parent / name
    if venv_bin.exists() and os.access(venv_bin, os.X_OK):
        return str(venv_bin)

    return shutil.which(name)


def _resolve_hwp_path(hwp_path: str | Path) -> Optional[Path]:
    """HWP 파일 경로를 실제 디스크상의 경로로 보정합니다.

    코퍼스 파일명이 전부 NFD(자모 분리)로 저장돼 있어, 코드에서 NFC 문자열로 만든
    경로를 그대로 열면 `FileNotFoundError`가 납니다. 같은 디렉터리를 훑어
    유니코드 정규화만 다른 파일을 찾아 실제 경로로 바꿔 줍니다.

    Args:
        hwp_path (str | Path): HWP 파일 경로

    Returns:
        Optional[Path]: 실제 존재하는 파일 경로. 없으면 None
    """

    path = Path(hwp_path)
    if path.exists():
        return path

    parent = path.parent
    if not parent.is_dir():
        return None

    target = unicodedata.normalize("NFC", path.name)
    for entry in os.listdir(parent):
        if unicodedata.normalize("NFC", entry) == target:
            return parent / entry

    return None


def _run(cmd: list[str]) -> Optional[subprocess.CompletedProcess]:
    """외부 명령을 실행하고 결과를 반환합니다.

    실패해도 예외를 밖으로 던지지 않습니다. 문서 한 개의 추출 실패가
    181개 문서 적재 전체를 멈추게 해서는 안 되기 때문입니다.

    Args:
        cmd (list[str]): 실행할 명령과 인자

    Returns:
        Optional[subprocess.CompletedProcess]: 실행 결과. 실행 자체가 실패하면 None
    """

    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=EXTRACT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        logger.exception(f"명령 실행에 실패했습니다: {' '.join(cmd)}")
        return None


def _cache_file(path: Path, cache_dir: Optional[str | Path]) -> Path:
    """텍스트 캐시 파일 경로를 만듭니다.

    Args:
        path (Path): 원본 HWP 파일 경로
        cache_dir (Optional[str | Path]): 캐시 디렉터리. None이면 원본 옆의 `.cache`

    Returns:
        Path: 캐시 파일 경로 (`{cache_dir}/{stem}.txt`)
    """

    base = Path(cache_dir) if cache_dir else path.parent / CACHE_DIR_NAME
    return base / f"{unicodedata.normalize('NFC', path.stem)}.txt"


def extract_text(
    hwp_path: str | Path,
    cache_dir: Optional[str | Path] = None,
    use_cache: bool = True
) -> Optional[str]:
    """HWP 문서의 본문 텍스트를 추출합니다.

    `hwp5txt`를 subprocess로 호출합니다. 추출은 문서당 수백 ms ~ 수 초가 걸리므로
    결과를 `.cache/{파일명}.txt`에 저장해 재적재 시 재사용합니다.

    표는 `<표>` placeholder로만 남습니다. 표 내용이 필요하면 `extract_tables()`를 함께 쓰세요.

    Args:
        hwp_path (str | Path): HWP 파일 경로
        cache_dir (Optional[str | Path]): 캐시 디렉터리. None이면 원본 파일 옆의 `.cache`
        use_cache (bool): 캐시 사용 여부

    Returns:
        Optional[str]: 추출한 본문 텍스트. 실패하면 None
    """

    path = _resolve_hwp_path(hwp_path)
    if path is None:
        logger.error(f"HWP 파일을 찾을 수 없습니다: {hwp_path}")
        return None

    cache_file = _cache_file(path, cache_dir)
    if use_cache and cache_file.exists():
        try:
            cached = cache_file.read_text(encoding="utf-8")
            if cached.strip():
                return cached
        except OSError:
            logger.exception(f"텍스트 캐시를 읽지 못했습니다: {cache_file}")

    binary = _find_hwp5_binary("hwp5txt")
    if binary is None:
        logger.error("hwp5txt 실행 파일을 찾을 수 없습니다. pyhwp 설치를 확인하세요.")
        return None

    result = _run([binary, str(path)])
    if result is None:
        return None

    if result.returncode != 0 or not result.stdout.strip():
        logger.error(
            f"본문 추출에 실패했습니다: {path.name} "
            f"(returncode={result.returncode}, stderr={result.stderr.strip()[:200]})"
        )
        return None

    text = result.stdout
    if use_cache:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(text, encoding="utf-8")
        except OSError:
            # 캐시 저장 실패는 추출 결과를 버릴 이유가 되지 않습니다.
            logger.exception(f"텍스트 캐시를 저장하지 못했습니다: {cache_file}")

    return text


def _cell_text(cell: Any) -> str:
    """표 셀 엘리먼트의 텍스트를 공백 정규화해 반환합니다.

    HWP에서 변환된 HTML은 셀 안에 `<p>`가 여러 개 들어가 줄바꿈이 섞입니다.
    Markdown 표로 직렬화할 때 행이 깨지므로 모든 공백을 단일 스페이스로 접습니다.

    Args:
        cell (Any): lxml `<td>`/`<th>` 엘리먼트

    Returns:
        str: 정규화한 셀 텍스트
    """

    return " ".join((cell.text_content() or "").split())


def extract_tables(hwp_path: str | Path, doc_id: Optional[str] = None) -> list[dict[str, Any]]:
    """HWP 문서의 표를 추출합니다.

    `hwp5html`로 HTML을 생성한 뒤 `lxml`로 `<table>`을 파싱합니다.
    반환 포맷은 rag-test `eval/tables/*.json`의 `tables` 항목과 동일합니다.

    `nonempty_cells`는 비어 있지 않은 셀 수입니다. 봉급 금액 그리드처럼
    HWP 단계부터 셀이 비어 있는 표가 있어(리포트 6.2절), 이 값으로
    "값이 없는 표"를 판별해 청킹 단계에서 추정 금지 경고를 붙입니다.

    Args:
        hwp_path (str | Path): HWP 파일 경로
        doc_id (Optional[str]): 문서번호. 표 ID 접두어로 씁니다. None이면 파일 stem 사용

    Returns:
        list[dict[str, Any]]: `{"table_id", "rows", "nonempty_cells"}` 리스트. 실패하면 빈 리스트
    """

    path = _resolve_hwp_path(hwp_path)
    if path is None:
        logger.error(f"HWP 파일을 찾을 수 없습니다: {hwp_path}")
        return []

    binary = _find_hwp5_binary("hwp5html")
    if binary is None:
        logger.error("hwp5html 실행 파일을 찾을 수 없습니다. pyhwp 설치를 확인하세요.")
        return []

    prefix = doc_id or unicodedata.normalize("NFC", path.stem)

    try:
        with tempfile.TemporaryDirectory(prefix="hwp5html_") as work_dir:
            out_path = os.path.join(work_dir, "document.html")
            result = _run([binary, "--html", "--output", out_path, str(path)])
            if result is None:
                return []

            if result.returncode != 0 or not os.path.exists(out_path):
                logger.error(
                    f"표 추출에 실패했습니다: {path.name} "
                    f"(returncode={result.returncode}, stderr={result.stderr.strip()[:200]})"
                )
                return []

            document = lxml_html.parse(out_path)
            tables: list[dict[str, Any]] = []

            for index, table in enumerate(document.xpath("//table")):
                rows: list[list[str]] = []
                for row in table.xpath(".//tr"):
                    cells = [_cell_text(cell) for cell in row.xpath("./td|./th")]
                    if cells:
                        rows.append(cells)

                if not rows:
                    continue

                tables.append(
                    {
                        "table_id": f"{prefix}-T{index}",
                        "rows": rows,
                        "nonempty_cells": sum(1 for row in rows for cell in row if cell.strip()),
                    }
                )

            return tables
    except Exception:
        logger.exception(f"표 추출 중 예상치 못한 오류가 발생했습니다: {path.name}")
        return []


async def extract_text_async(
    hwp_path: str | Path,
    cache_dir: Optional[str | Path] = None,
    use_cache: bool = True
) -> Optional[str]:
    """HWP 문서의 본문 텍스트를 비동기로 추출합니다.

    `extract_text()`는 subprocess를 동기로 기다리므로, FastAPI 요청 핸들러 등
    이벤트 루프 위에서 직접 부르면 루프가 막힙니다. 별도 스레드로 위임합니다.

    Args:
        hwp_path (str | Path): HWP 파일 경로
        cache_dir (Optional[str | Path]): 캐시 디렉터리
        use_cache (bool): 캐시 사용 여부

    Returns:
        Optional[str]: 추출한 본문 텍스트. 실패하면 None
    """

    return await asyncio.to_thread(extract_text, hwp_path, cache_dir, use_cache)


async def extract_tables_async(
    hwp_path: str | Path,
    doc_id: Optional[str] = None
) -> list[dict[str, Any]]:
    """HWP 문서의 표를 비동기로 추출합니다.

    Args:
        hwp_path (str | Path): HWP 파일 경로
        doc_id (Optional[str]): 문서번호. 표 ID 접두어로 사용

    Returns:
        list[dict[str, Any]]: 표 리스트. 실패하면 빈 리스트
    """

    return await asyncio.to_thread(extract_tables, hwp_path, doc_id)
