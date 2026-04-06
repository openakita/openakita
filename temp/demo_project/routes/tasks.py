from fastapi import APIRouter, HTTPException, status, Depends
from typing import List, Optional
from sqlalchemy.orm import Session
from models import Task, TaskCreate, TaskUpdate, TaskListResponse, MessageResponse, TaskStatus, TaskDB, UserDB
from main import get_db
from auth import get_current_active_user

router = APIRouter()


@router.post("/", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(
    task: TaskCreate, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_active_user)
):
    """创建新任务"""
    # 检查用户是否存在
    if not db.query(UserDB).filter(UserDB.id == task.user_id).first():
        raise HTTPException(status_code=404, detail="User not found")
    
    db_task = TaskDB(
        title=task.title,
        description=task.description,
        status=task.status,
        due_date=task.due_date,
        user_id=task.user_id
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    
    return Task(
        id=db_task.id, title=db_task.title, description=db_task.description,
        status=db_task.status, due_date=db_task.due_date, user_id=db_task.user_id,
        created_at=db_task.created_at, updated_at=db_task.updated_at
    )


@router.get("/", response_model=TaskListResponse)
async def get_tasks(
    skip: int = 0,
    limit: int = 100,
    status_filter: Optional[str] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_active_user)
):
    """获取任务列表（支持分页和筛选）"""
    query = db.query(TaskDB)
    
    if status_filter:
        query = query.filter(TaskDB.status == status_filter)
    if user_id:
        query = query.filter(TaskDB.user_id == user_id)
    
    total = query.count()
    db_tasks = query.offset(skip).limit(limit).all()
    
    tasks = [
        Task(
            id=t.id, title=t.title, description=t.description,
            status=t.status, due_date=t.due_date, user_id=t.user_id,
            created_at=t.created_at, updated_at=t.updated_at
        ) for t in db_tasks
    ]
    return TaskListResponse(tasks=tasks, total=total)


@router.get("/{task_id}", response_model=Task)
async def get_task(
    task_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_active_user)
):
    """获取指定任务"""
    db_task = db.query(TaskDB).filter(TaskDB.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    return Task(
        id=db_task.id, title=db_task.title, description=db_task.description,
        status=db_task.status, due_date=db_task.due_date, user_id=db_task.user_id,
        created_at=db_task.created_at, updated_at=db_task.updated_at
    )


@router.put("/{task_id}", response_model=Task)
async def update_task(
    task_id: int, 
    task: TaskUpdate, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_active_user)
):
    """更新任务信息"""
    db_task = db.query(TaskDB).filter(TaskDB.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    update_data = task.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_task, field, value)
    
    db.commit()
    db.refresh(db_task)
    
    return Task(
        id=db_task.id, title=db_task.title, description=db_task.description,
        status=db_task.status, due_date=db_task.due_date, user_id=db_task.user_id,
        created_at=db_task.created_at, updated_at=db_task.updated_at
    )


@router.delete("/{task_id}", response_model=MessageResponse)
async def delete_task(
    task_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_active_user)
):
    """删除任务"""
    db_task = db.query(TaskDB).filter(TaskDB.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    db.delete(db_task)
    db.commit()
    return MessageResponse(message=f"Task {task_id} deleted successfully")
