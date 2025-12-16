from typing import Dict, Tuple, Optional
from fastapi import Request, HTTPException, status, Depends, Header
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from app.api import deps
from app.db.models import UserDB

# ключ: (user_id, path, idem_key) -> (status_code, body)
_idempotency_store: Dict[Tuple[int, str, str], Tuple[int, dict]] = {}


async def idempotency_dependency(
    request: Request,
    idem_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user: UserDB = Depends(deps.get_current_user),
):
    if request.method.upper() != "POST":
        return

    if not idem_key:
        request.state.idem_reused = False
        return

    key = (current_user.id, request.url.path, idem_key)

    if key in _idempotency_store:
        status_code, body = _idempotency_store[key]
        request.state.idem_reused = True
        request.state.idem_response = (status_code, body)
    else:
        request.state.idem_reused = False
        request.state.idem_key = key


def save_idempotent_response(request: Request, status_code: int, body: dict):
    if getattr(request.state, "idem_reused", False):
        return
    key = getattr(request.state, "idem_key", None)
    if key:
        safe_body = jsonable_encoder(body)
        _idempotency_store[key] = (status_code, safe_body)