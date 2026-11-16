"""FastAPI 시작점. PyCharm에서 이 파일을 실행하면 서버가 뜬다."""
import logging
import sys
from pathlib import Path

if __name__ == "__main__":
    # 파일로 직접 실행할 때도 `app` 패키지를 찾을 수 있게 프로젝트 최상위를 경로에 넣는다.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import backup, config
from app.routers import comments, feed, games, me, search, settings, survey
from app.lifecycle import lifespan
from app.security import OriginCheckMiddleware, SecurityHeadersMiddleware

app = FastAPI(title="GamePick", docs_url="/docs" if config.DEBUG else None, redoc_url=None,
              openapi_url="/openapi.json" if config.DEBUG else None, lifespan=lifespan)
app.add_middleware(OriginCheckMiddleware)      # 안쪽: 출처가 다르면 403 (C10)
app.add_middleware(SecurityHeadersMiddleware)  # 바깥쪽: 403 응답에도 보안 헤더
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

for module in (survey, feed, games, search, me, settings, comments):
    app.include_router(module.router)


# 오류는 모두 {"error": "..."} 형식
@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    """예상 못 한 오류: 내부 경로·코드를 보여 주지 않음 (로그에만)"""
    logging.getLogger("uvicorn.error").exception("처리 못 한 오류: %s %s", request.method, request.url.path)
    return JSONResponse({"error": "서버 오류가 났어요. 잠시 후 다시 시도하세요."}, status_code=500)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first["loc"] if p not in ("body", "query", "path"))
    if first["type"] == "value_error":  # 직접 쓴 한글 검사 문구는 그대로 화면에 보여 줄 수 있게
        return JSONResponse({"error": first["msg"].removeprefix("Value error, ")}, status_code=422)
    return JSONResponse({"error": f"{where}: {first['msg']}" if where else first["msg"]}, status_code=422)


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root():
    """첫 주소 = 홈 화면을 바로 200으로 (C10: Replit 헬스 체크가 /에 요청하는데, 넘기기(3xx)를 통과로 치는지
    문서에 없어서). 설문을 안 했으면 홈이 설문으로 보낸다. 사용자를 만들지 않아 빠름"""
    return FileResponse(config.STATIC_DIR / "pages" / "home.html", media_type="text/html")


@app.get("/api/health")
def health():
    """backup: 숫자·시각만 (경로·키 없음, DEBUG=false에서도)"""
    return {"status": "ok", "backup": backup.status.public()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, reload=config.DEBUG)
