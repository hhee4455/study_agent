"""FastAPI application — agent_system dashboard (read-only skeleton)."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.budget import router as budget_router
from .api.debates import router as debates_router
from .api.events import router as events_router
from .api.members import router as members_router
from .api.plan import router as plan_router
from .state_reader import get_state

app = FastAPI(title="agent-system dashboard")
app.include_router(members_router)
app.include_router(plan_router)
app.include_router(debates_router)
app.include_router(budget_router)
app.include_router(events_router)


def get_ws_root() -> Path:
    """기본 ws_root = $AGENT_WS_ROOT 또는 cwd. 다른 라우터의 get_ws_root 와 일관."""
    return Path(os.environ.get("AGENT_WS_ROOT", str(Path.cwd())))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
def api_state(ws_root: Path = Depends(get_ws_root)) -> dict:
    """전체 스냅샷 — lead.dashboard.collect_state 의 직렬화 결과 (5s 캐시)."""
    return get_state(ws_root)


# React SPA 정적 서빙 — `web/static/` 은 `web/frontend/` 의 vite build 산출물.
# 빌드 안 됐으면 (개발 모드) mount 생략하고 API 만 노출.
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    # /assets/* → static/assets/* (JS/CSS 번들)
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/")
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str = "") -> FileResponse:
        """SPA 라우팅 fallback — API 라우트가 매치 안 되면 index.html 반환."""
        return FileResponse(_STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    # 127.0.0.1 only — 외부 노출 금지 (project.md 안전 가드).
    # 포트 8765 는 ./scripts/web.sh 와 통일.
    uvicorn.run("web.server:app", host="127.0.0.1", port=8765, reload=False)
