"""보안 (4.5): 보안 헤더 미들웨어, 요청 횟수 제한. 입력 검증은 각 라우터의 pydantic 모델에서."""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app import config

# 외부 리소스(썸네일, 영상, 글꼴)를 쓰게 되면 해당 출처만 골라서 추가한다.
CSP = "; ".join([
    "default-src 'self'",
    "img-src 'self' data:",
    "style-src 'self'",
    "script-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
])

# /docs(Swagger UI)는 DEBUG일 때만 열리고, CDN 스크립트와 인라인 초기화 코드가 필요하다.
DOCS_CSP = "; ".join([
    "default-src 'self'",
    "img-src 'self' data: https://fastapi.tiangolo.com",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "object-src 'none'",
    "frame-ancestors 'none'",
])
DOCS_PATHS = ("/docs", "/docs/oauth2-redirect")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path in DOCS_PATHS:
            response.headers["Content-Security-Policy"] = DOCS_CSP
        if request.url.path.startswith("/static/"):
            # 매번 바뀌었는지 확인 (안 바뀌었으면 304로 브라우저 사본 사용) → 고친 CSS·JS가 바로 반영됨
            response.headers.setdefault("Cache-Control", "no-cache")
        return response


# ---------- 출처 검사 (C10) ----------
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """쓰기 요청에 Origin 헤더가 있으면 우리 주소(config.ALLOWED_ORIGINS)일 때만 통과, 다르면 403.
    Origin이 없는 요청(같은 사이트의 일부 요청·스크립트·테스트)은 그대로 통과 — SameSite=lax 쿠키가 기존 보호"""

    async def dispatch(self, request, call_next):
        origin = request.headers.get("origin")
        if request.method in UNSAFE_METHODS and origin is not None and origin.rstrip("/") not in config.ALLOWED_ORIGINS:
            return JSONResponse({"error": "허용되지 않은 출처의 요청입니다."}, status_code=403)
        return await call_next(request)


# ---------- 실제 사용자 IP (C10) ----------
def client_ip(request: Request) -> str:
    """요청 횟수 제한에 쓰는 IP.
    PROXY_HOPS = 0(로컬): 접속 주소 그대로. N > 0(배포): X-Forwarded-For의 오른쪽에서 N번째
    (프록시가 오른쪽에 붙이므로, 사용자가 헤더를 꾸며 왼쪽에 넣은 값은 무시됨). 헤더 칸이 모자라면 맨 왼쪽"""
    hops = config.PROXY_HOPS
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if parts:
            return parts[-hops] if len(parts) >= hops else parts[0]
    return request.client.host if request.client else "-"


def forwarded_hops(request: Request) -> int:
    """X-Forwarded-For 칸 수 (배포 후 PROXY_HOPS를 맞추려고 로그에만 남김, IP는 안 남김)"""
    return len([p for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()])


# ---------- 요청 횟수 제한 ----------
RATE_WINDOW_SECONDS = 60
RATE_LIMITS = {          # 1분에 허용하는 요청 수
    "reaction": 30,      # 반응 (따봉·스토어·관심 없음·따봉 취소)
    "search": 60,        # 검색
    "suggest": 60,       # 자동완성 (입력할 때마다 불리므로 검색과 따로 셈)
}

# 댓글 추천·비추천 (C9): 사용자 기준만 1분에 30번 (같은 와이파이 여러 명이 막히지 않게)
COMMENT_VOTE_PER_MINUTE = 30


class RateLimiter:
    """메모리에 최근 요청 시각을 모아 두는 간단한 제한기. 서버를 다시 켜면 초기화된다."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str, limit: int, now: float, window: float = RATE_WINDOW_SECONDS) -> bool:
        """이번 요청이 허용되면 True. window = 몇 초 동안 limit번까지"""
        hits = self._hits[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()


def rate_limit(scope: str):
    """FastAPI 의존성. 사용자(쿠키)와 IP 둘 다 세서 하나라도 넘으면 429."""
    limit = RATE_LIMITS[scope]

    def check(request: Request) -> None:
        now = time.monotonic()
        keys = [f"{scope}:ip:{client_ip(request)}"]
        uid = request.cookies.get("gp_uid")
        if uid:
            keys.append(f"{scope}:uid:{uid[:64]}")
        allowed = [limiter.hit(key, limit, now) for key in keys]
        if not all(allowed):
            raise HTTPException(429, "요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
                                headers={"Retry-After": str(RATE_WINDOW_SECONDS)})

    return check


def comment_vote_rate_limit(request: Request) -> None:
    """FastAPI 의존성 (C9). 쿠키가 없으면(사용자 확인 전에 거절될 요청) IP로 센다"""
    uid = request.cookies.get("gp_uid")
    key = f"comment_vote:uid:{uid[:64]}" if uid else f"comment_vote:ip:{client_ip(request)}"
    if not limiter.hit(key, COMMENT_VOTE_PER_MINUTE, time.monotonic()):
        raise HTTPException(429, "요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
                            headers={"Retry-After": str(RATE_WINDOW_SECONDS)})


HOUR_SECONDS = 60 * 60


def check_user_create(request: Request) -> None:
    """쿠키 없는 요청으로 새 사용자를 만들기 직전에 호출 (C10). IP당 1시간 USER_CREATE_PER_HOUR명 넘으면 429"""
    if not limiter.hit(f"user-create:ip:{client_ip(request)}", config.USER_CREATE_PER_HOUR, time.monotonic(), HOUR_SECONDS):
        raise HTTPException(429, "새 사용자를 너무 많이 만들고 있어요. 잠시 후 다시 시도하세요.",
                            headers={"Retry-After": str(HOUR_SECONDS)})


# 댓글 쓰기·고치기 (C8-c): 합쳐서 1분에 5번(사용자·IP 각각), 하루 50번(사용자)
COMMENT_PER_MINUTE = 5
COMMENT_PER_DAY = 50
DAY_SECONDS = 24 * 60 * 60


def comment_rate_limit(request: Request) -> None:
    """FastAPI 의존성. 하루 제한은 사용자 기준만 (같은 와이파이를 쓰는 여러 사람이 막히지 않게)"""
    now = time.monotonic()
    uid = (request.cookies.get("gp_uid") or "-")[:64]
    ip = client_ip(request)
    checks = [(f"comment:ip:{ip}", COMMENT_PER_MINUTE, RATE_WINDOW_SECONDS),
              (f"comment:uid:{uid}", COMMENT_PER_MINUTE, RATE_WINDOW_SECONDS),
              (f"comment-day:uid:{uid}", COMMENT_PER_DAY, DAY_SECONDS)]
    for key, limit, window in checks:
        if not limiter.hit(key, limit, now, window):
            day = window == DAY_SECONDS
            raise HTTPException(429, "오늘 쓸 수 있는 댓글 수를 넘었어요. 내일 다시 써 주세요." if day
                                else "댓글을 너무 빨리 쓰고 있어요. 잠시 후 다시 시도하세요.",
                                headers={"Retry-After": str(window if day else RATE_WINDOW_SECONDS)})
