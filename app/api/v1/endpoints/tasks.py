from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from fastapi import Request
from fastapi.responses import JSONResponse
from app.core.idempotency import idempotency_dependency, save_idempotent_response

from app.api import deps
from app.db.models import TaskDB
from app.schemas.tasks import TaskCreate, TaskV1, TaskUpdate
from app.db.models import TaskStatus
from datetime import datetime

router = APIRouter(tags=["tasks v1"], prefix="/tasks")

@router.get("/", response_model=List[TaskV1])
def list_tasks(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_user),
    _rate = Depends(deps.rate_limit_dependency),
):
    q = (
        db.query(TaskDB)
        .filter(TaskDB.owner_id == current_user.id)
        .order_by(TaskDB.created_at.desc())
    )

    tasks = q.limit(limit).offset(offset).all()
    return tasks


@router.post("/", response_model=TaskV1, status_code=status.HTTP_201_CREATED)
def create_task(
    task_in: TaskCreate,
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
        due_date=task_in.due_date,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    data = TaskV1.from_orm(task).dict()
    save_idempotent_response(request, status.HTTP_201_CREATED, data)
    return data


@router.get("/{task_id}", response_model=TaskV1)
def get_task(
    task_id: int,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_user),
    _rate = Depends(deps.rate_limit_dependency),
):
    task = (
        db.query(TaskDB)
        .filter(TaskDB.id == task_id, TaskDB.owner_id == current_user.id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}", response_model=TaskV1)
def update_task(
    task_id: int,
    task_in: TaskUpdate,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_user),
    _rate = Depends(deps.rate_limit_dependency),
):
    task = (
        db.query(TaskDB)
        .filter(TaskDB.id == task_id, TaskDB.owner_id == current_user.id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task_in.title is not None:
        task.title = task_in.title
    if task_in.description is not None:
        task.description = task_in.description
    if task_in.status is not None:
        task.status = task_in.status
    if task_in.due_date is not None:
        task.due_date = task_in.due_date

    task.updated_at = datetime.utcnow()

    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_user),
    _rate = Depends(deps.rate_limit_dependency),
):
    task = (
        db.query(TaskDB)
        .filter(TaskDB.id == task_id, TaskDB.owner_id == current_user.id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    db.delete(task)
    db.commit()
    return
