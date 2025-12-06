from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api import deps
from app.schemas.users import UserCreate, UserLogin, UserOut, Token
from app.db.models import UserDB

router = APIRouter(tags=["auth v1"])


@router.post("/auth/register", response_model=UserOut)
def register_user(
    user_in: UserCreate,
    db: Session = Depends(deps.get_db),
    _rate = Depends(deps.rate_limit_dependency),
):
    existing = deps.get_user_by_email(db, user_in.email)
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    user = UserDB(
        email=user_in.email,
        password_hash=deps.hash_password(user_in.password),
        full_name=user_in.full_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/auth/login", response_model=Token)
def login(
    user_in: UserLogin,
    db: Session = Depends(deps.get_db),
    _rate = Depends(deps.rate_limit_dependency),
):
    user = deps.get_user_by_email(db, user_in.email)
    if not user or not deps.verify_password(user_in.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password",
        )

    token = deps.create_access_token({"sub": str(user.id)})
    return {"access_token": token, "token_type": "bearer"}
