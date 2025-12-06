from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.db.session import Base, engine
from app.api.v1 import router as v1_router
from app.api.v2 import router as v2_router

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Task Manager API",
    version="1.0.0",
    description="Система задач с версионностью API (v1, v2) и JWT-аутентификацией",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_rate_limit_headers(request: Request, call_next):
    response = await call_next(request)
    if hasattr(request.state, "x_limit_remaining"):
        response.headers["X-Limit-Remaining"] = str(request.state.x_limit_remaining)
    if hasattr(request.state, "retry_after") and request.state.retry_after:
        response.headers["Retry-After"] = str(request.state.retry_after)
    return response

app.include_router(v1_router)
app.include_router(v2_router)
