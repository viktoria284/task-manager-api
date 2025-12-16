from typing import List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from fastapi import Request
from fastapi.responses import JSONResponse
from app.core.idempotency import idempotency_dependency, save_idempotent_response

from app.api import deps
from app.db.models import TaskDB, TaskStatus, TaskPriority
from app.schemas.tasks import TaskCreate, TaskV2

router = APIRouter(tags=["tasks v2"], prefix="/tasks")


class TaskCreateV2(TaskCreate):
    priority: TaskPriority = TaskPriority.medium


@router.get("/", response_model=List[TaskV2])
def list_tasks_v2(
    status_filter: Optional[TaskStatus] = None,
    priority_filter: Optional[TaskPriority] = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_user),
    _rate = Depends(deps.rate_limit_dependency),
):
    q = db.query(TaskDB).filter(TaskDB.owner_id == current_user.id)
    if status_filter:
        q = q.filter(TaskDB.status == status_filter)
    if priority_filter:
        q = q.filter(TaskDB.priority == priority_filter)

    tasks = (
        q.order_by(TaskDB.created_at.desc())
         .limit(limit)
         .offset(offset)
         .all()
    )
    return tasks


@router.post("/", response_model=TaskV2, status_code=status.HTTP_201_CREATED)
def create_task_v2(
    task_in: TaskCreateV2,
    request: Request,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_user),
    _idem = Depends(idempotency_dependency),
    _rate = Depends(deps.rate_limit_dependency),
):
    if getattr(request.state, "idem_reused", False):
        status_code, body = request.state.idem_response
        return JSONResponse(status_code=status_code, content=body)

    task = TaskDB(
        owner_id=current_user.id,
        title=task_in.title,
        description=task_in.description,
        status=task_in.status,
        priority=task_in.priority,
        due_date=task_in.due_date,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    data = TaskV2.from_orm(task).dict()
    save_idempotent_response(request, status.HTTP_201_CREATED, data)
    return data


@router.get("/fields")
def list_tasks_with_fields(
    include: Optional[str] = Query(
        default=None,
        description="Список полей через запятую, которые нужно вернуть (например: title,status,priority)",
    ),
    status_filter: Optional[TaskStatus] = None,
    priority_filter: Optional[TaskPriority] = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_user),
    _rate = Depends(deps.rate_limit_dependency),
):
    q = db.query(TaskDB).filter(TaskDB.owner_id == current_user.id)
    if status_filter:
        q = q.filter(TaskDB.status == status_filter)
    if priority_filter:
        q = q.filter(TaskDB.priority == priority_filter)

    tasks = (
        q.order_by(TaskDB.created_at.desc())
         .limit(limit)
         .offset(offset)
         .all()
    )
    
    if not include:
        return [TaskV2.from_orm(t).dict() for t in tasks]

    requested_fields: Set[str] = {f.strip() for f in include.split(",") if f.strip()}

    result = []
    for t in tasks:
        full = TaskV2.from_orm(t).dict()
        partial = {k: v for k, v in full.items() if k in requested_fields}
        result.append(partial)

    return result