from fastapi import APIRouter
from app.api.v2.endpoints import tasks, internal

router = APIRouter(prefix="/api/v2")
router.include_router(tasks.router)
router.include_router(internal.router)
