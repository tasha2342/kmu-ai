import builtins
import os
import sys

from pathlib import Path

from types import ModuleType

from typing import Iterable, List


_GUARD_FLAG = "_jdone_runtime_guard_enabled"
_GUARD_MARKER_PATH = Path("/app/.jdone_guard")

_SUSPICIOUS_ENVS: Iterable[str] = (
    "PYTHONINSPECT",
    "PYTHONBREAKPOINT",
    "PYTHONDEBUG",
    "PYCHARM_HOSTED",
)

_BLOCKED_MODULE_PREFIXES: Iterable[str] = (
    "pdb",
    "ipdb",
    "pydevd",
    "debugpy",
)


def _already_enabled() -> bool:
    """가드가 이미 적용되었는지 확인합니다.
    
    Returns:
		bool: 가드가 이미 적용되었으면 True, 그렇지 않으면 False
    """

    return getattr(sys, _GUARD_FLAG, False) is True

def _mark_enabled() -> None:
    """가드 적용 여부를 전역 상태로 기록합니다."""

    setattr(sys, _GUARD_FLAG, True)

def _blocked_trace(*_args: object, **_kwargs: object) -> None:
    """추적 및 프로파일링을 시도할 때 예외를 발생시킵니다."""

    raise RuntimeError("이 런타임에서는 추적과 프로파일링이 비활성화되어 있습니다.")

def _check_existing_hooks() -> List[str]:
    """이미 활성화된 디버깅 훅 및 환경 변수를 탐지합니다.
    
    Returns:
		List[str]: 감지된 트리거의 설명 목록
    """

    triggers: List[str] = []
    if sys.gettrace() is not None:
        triggers.append("추적 훅이 이미 활성화되어 있습니다")
    if getattr(sys, "getprofile", lambda: None)() is not None:
        triggers.append("프로파일링 훅이 이미 활성화되어 있습니다")

    env_hits = [name for name in _SUSPICIOUS_ENVS if os.getenv(name)]
    if env_hits:
        triggers.append("디버깅용 환경 변수가 설정되어 있습니다: " + ", ".join(env_hits))

    loaded_debuggers = [
        name
        for name in sys.modules
        if any(name.startswith(prefix) for prefix in _BLOCKED_MODULE_PREFIXES)
    ]
    if loaded_debuggers:
        triggers.append("디버거 관련 모듈이 이미 로드되었습니다: " + ", ".join(sorted(loaded_debuggers)))

    return triggers

def _patch_import() -> None:
    """디버거 관련 모듈의 import를 차단합니다."""

    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: dict | None = None,
        locals: dict | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> ModuleType:
        lower_name = name.lower()
        if any(lower_name.startswith(prefix) for prefix in _BLOCKED_MODULE_PREFIXES):
            raise RuntimeError(f"디버거 모듈 '{name}'는 불허됩니다.")
        return original_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded_import  # type: ignore[assignment]

def _patch_breakpoint() -> None:
    """`breakpoint()` 내장 함수를 비활성화합니다."""

    def guarded_breakpoint(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("breakpoint()는 이 런타임에서 사용할 수 없습니다.")

    builtins.breakpoint = guarded_breakpoint  # type: ignore[assignment]

def enforce_runtime_guard() -> None:
    """인터랙티브 디버깅 훅을 탐지하고 비활성화합니다."""

    if not _GUARD_MARKER_PATH.exists():
        return

    if _already_enabled():
        return

    triggers = _check_existing_hooks()
    if triggers:
        raise RuntimeError("런타임 보호 로직이 감지했습니다: " + "; ".join(triggers))

    sys.settrace = _blocked_trace  # type: ignore[assignment]
    if hasattr(sys, "setprofile"):
        sys.setprofile = _blocked_trace  # type: ignore[assignment]

    _patch_import()
    _patch_breakpoint()
    _mark_enabled()

    # 차단된 모듈이 지연 로드되는 상황을 방지하기 위해 이미 로드된 항목을 제거합니다.
    for prefix in _BLOCKED_MODULE_PREFIXES:
        for name in list(sys.modules.keys()):
            if name.startswith(prefix):
                del sys.modules[name]


__all__ = ["enforce_runtime_guard"]
