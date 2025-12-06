from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.db.models import TaskStatus, TaskPriority


class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.todo
    due_date: Optional[datetime] = None


# Заявка на создание задачи — общая для v1 и v2 (без priority)
class TaskCreate(TaskBase):
    pass

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    due_date: Optional[datetime] = None


class TaskV1(BaseModel):
    id: int
    title: str
    description: Optional[str]
    status: TaskStatus
    due_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        # для pydantic v2, чтобы работал from_orm()
        from_attributes = True


class TaskV2(TaskV1):
    priority: TaskPriority

    class Config:
        from_attributes = True
