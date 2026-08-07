import sys
import traceback
import time

from typing import Callable

from fastapi import FastAPI, Request, HTTPException

from app.responses.exception import InternalServerErrorResponse
from app.utils.logger import get_api_logger
import app.utils.common as util


api_logger = get_api_logger()


def init(app: FastAPI):
    @app.middleware("http")
    async def _middleware(request: Request, call_next: Callable):
        client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
        method = request.method
        path = str(request.url.path)
        if request.url.query:
            path += f"?{request.url.query}"
        http_version = request.scope.get("http_version", "")
        request_id = f"req-{util.generate_id(k=16)}"
        
        request_info = f"{method} {path} HTTP/{http_version}"
        client_info = f"[dim]([cyan]{request_id}[/cyan]@[yellow]{client_ip}[/yellow])[/dim]"
        
        start_time = time.time()
        api_logger.info(f'[bold][Request][/bold] "{request_info}"   {client_info}')
        
        try:
            response = await call_next(request)
            
            process_time = time.time() - start_time
            api_logger.info(f'[bold][Response][/bold] Time: [green]{process_time:.4f}s[/green], "{request_info} : [blue]{response.status_code}[/blue]"   {client_info}')
            return response
        except HTTPException as exc:
            process_time = time.time() - start_time
            api_logger.exception(f'[bold][Response][/bold] Time: [red]{process_time:.4f}s[/red], "{request_info}"   {client_info}')
            
            raise exc
        except Exception as exc:
            process_time = time.time() - start_time
            
            # 상대 경로, 라인 번호, 함수 이름 추출
            _, _, exc_traceback = sys.exc_info()
            tb = traceback.extract_tb(exc_traceback)
            filename, line_number, func_name, _ = tb[-1]
            relative_path = filename.split("app/")[-1] if "app/" in filename else filename
            
            api_logger.exception(
                f'[bold][Response][/bold] Time: [red]{process_time:.4f}s[/red], "{request_info}"   {client_info}\n'
                f"File: [magenta]{relative_path}[/magenta], Line: [yellow]{line_number}[/yellow], Function: [cyan]{func_name}[/cyan] >"
            )
            
            return InternalServerErrorResponse(message="서버 내부 오류가 발생했습니다.")
