from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple
from fastapi import Request
import time

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import UserDB

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

security = HTTPBearer()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60

_rate_limit_store: Dict[str, Tuple[int, float]] = {}  # ip -> (count, window_start)


async def rate_limit_dependency(request: Request):
    """
    Простейший rate limiting по IP:
    - 60 запросов в минуту.
    - При превышении — 429 + Retry-After.
    """
    identifier = request.client.host or "unknown"
    now = time.time()

    count, window_start = _rate_limit_store.get(identifier, (0, now))

    # новое окно, если прошло больше минуты
    if now - window_start > RATE_LIMIT_WINDOW_SECONDS:
        count = 0
        window_start = now

    count += 1
    remaining = max(RATE_LIMIT_REQUESTS - count, 0)

    _rate_limit_store[identifier] = (count, window_start)

    # сохраняем значения в request.state, чтобы потом добавить их в заголовки
    request.state.x_limit_remaining = remaining
    retry_after = 0
    if count > RATE_LIMIT_REQUESTS:
        retry_after = int(RATE_LIMIT_WINDOW_SECONDS - (now - window_start))
    request.state.retry_after = retry_after

    if count > RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
            headers={
                "X-Limit-Remaining": "0",
                "Retry-After": str(retry_after),
            },
        )


# ====== хэширование паролей ======

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ====== JWT ======

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_user_by_email(db: Session, email: str) -> Optional[UserDB]:
    return db.query(UserDB).filter(UserDB.email == email).first()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> UserDB:
    """
    Берём токен из стандартной схемы HTTP Bearer.
    Благодаря этому в Swagger появится кнопка Authorize.
    """
    token = credentials.credentials

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = db.query(UserDB).filter(UserDB.id == int(user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user

