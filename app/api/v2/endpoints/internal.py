from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import deps
from app.db.models import UserDB, TaskDB, TaskStatus

router = APIRouter(tags=["internal v2"], prefix="/internal")


@router.get("/stats")
def get_internal_stats(
    db: Session = Depends(deps.get_db),
    _rate = Depends(deps.rate_limit_dependency),
    current_user=Depends(deps.get_current_user),
):
    users_count = db.query(UserDB).count()
    tasks_count = db.query(TaskDB).count()

    tasks_by_status = (
        db.query(TaskDB.status, func.count(TaskDB.id))
        .group_by(TaskDB.status)
        .all()
    )

    tasks_by_status_dict = {
        status.value if hasattr(status, "value") else str(status): count
        for status, count in tasks_by_status
    }

    return {
        "users_count": users_count,
        "tasks_count": tasks_count,
        "tasks_by_status": tasks_by_status_dict,
    }
