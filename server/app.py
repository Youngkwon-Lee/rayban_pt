"""FastAPI application wiring for the rayban local bridge.

Application setup only: the app object, static mounts, the API-key/HUD-token
middleware, and router registration.  Configuration, mutable state, and domain
helpers live in :mod:`bridge_core`; endpoints live in :mod:`routers`.

``bridge_core`` is re-exported here so that existing tooling which does
``import app`` keeps seeing the helper names it used to.  Values that are
patched at runtime (``DB_PATH``, ``BRIDGE_API_KEY``, ...) must be set on
``bridge_core`` itself, because that is where every reader looks them up.
"""

import bridge_core as core
from bridge_core import *  # noqa: F401,F403  (backwards-compatible re-export)
from bridge_core import (  # noqa: F401  (names the app layer uses directly)
    ROOT,
    FastAPI,
    JSONResponse,
    Request,
    StaticFiles,
)

app = FastAPI(title="rayban-local-bridge", version="0.4.0")

app.mount(
    "/glass-app",
    StaticFiles(directory=str(ROOT / "static" / "glass-webapp"), html=True),
    name="glass-webapp",
)

app.mount(
    "/neural-band-console",
    StaticFiles(directory=str(ROOT / "static" / "neural-band-console"), html=True),
    name="neural-band-console",
)

@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    path = request.url.path
    is_public_prefix = any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in PUBLIC_PATH_PREFIXES
    )

    if (
        path in PUBLIC_PATHS
        or is_public_prefix
        or (core.ALLOW_DOCS_WITHOUT_AUTH and path in DOC_PATHS)
    ):
        return await call_next(request)

    if _is_hud_test_request(request):
        return await call_next(request)

    incoming_key = request.headers.get("x-api-key", "") or request.query_params.get("api_key", "")
    incoming_hud_token = request.headers.get("x-hud-token", "") or request.query_params.get("hud_token", "")
    hud_token_allowed = (
        path != HUD_TOKEN_ISSUE_PATH
        and any(path.startswith(prefix) for prefix in HUD_TOKEN_AUTH_PATH_PREFIXES)
    )
    if core.BRIDGE_API_KEY:
        if incoming_key == core.BRIDGE_API_KEY:
            return await call_next(request)
        if incoming_hud_token and hud_token_allowed:
            try:
                _decode_hud_scope_token(incoming_hud_token)
            except Exception as exc:
                return JSONResponse(
                    status_code=401,
                    content={
                        "code": "INVALID_HUD_SCOPE_TOKEN",
                        "message": str(exc),
                    },
                )
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={
                "code": "UNAUTHORIZED",
                "message": "유효한 x-api-key 헤더 또는 HUD scope token이 필요합니다.",
            },
        )

    if incoming_hud_token and hud_token_allowed:
        try:
            _decode_hud_scope_token(incoming_hud_token)
        except Exception as exc:
            return JSONResponse(
                status_code=401,
                content={
                    "code": "INVALID_HUD_SCOPE_TOKEN",
                    "message": str(exc),
                },
            )
        return await call_next(request)

    if not core.REQUIRE_API_KEY or core.ALLOW_INSECURE_LAN or _is_loopback_host(_client_host(request)):
        return await call_next(request)

    if path in DOC_PATHS:
        message = "LAN에서 API 문서를 보려면 BRIDGE_API_KEY를 설정하거나 ALLOW_DOCS_WITHOUT_AUTH=true를 명시하세요."
    else:
        message = "LAN 요청에는 BRIDGE_API_KEY 설정이 필요합니다. server/run_lan_bridge.sh를 다시 실행해 생성된 키를 앱에 입력하세요."
    return JSONResponse(
        status_code=503,
        content={
            "code": "BRIDGE_API_KEY_REQUIRED",
            "message": message,
        },
    )


# ── routers ─────────────────────────────────────────────────────────────────

from routers.consents import router as consents_router
from routers.ingest import router as ingest_router
from routers.events import router as events_router
from routers.hud_candidates import router as hud_candidates_router
from routers.moai_sync import router as moai_sync_router
from routers.charts import router as charts_router
from routers.media import router as media_router
from routers.visit_sessions import router as visit_sessions_router
from routers.glass import router as glass_router
from routers.agent_gateway import router as agent_gateway_router
from routers.system import router as system_router

app.include_router(consents_router)
app.include_router(ingest_router)
app.include_router(events_router)
app.include_router(hud_candidates_router)
app.include_router(moai_sync_router)
app.include_router(charts_router)
app.include_router(media_router)
app.include_router(visit_sessions_router)
app.include_router(glass_router)
app.include_router(agent_gateway_router)
app.include_router(system_router)
