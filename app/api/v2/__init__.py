from fastapi import APIRouter

from app.api.v2.endpoints import tasks

router = APIRouter(prefix="/api/v2")

router.include_router(tasks.router)
