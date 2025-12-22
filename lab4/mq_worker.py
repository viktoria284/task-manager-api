import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pika
from jose import JWTError, jwt
from sqlalchemy import Column, DateTime, JSON, String
from sqlalchemy.orm import Session

from lab4.mq_common import MQSettings, connect, declare_topology

from app.api import deps
from app.core.config import settings
from app.db.models import UserDB, TaskDB
from app.db.session import Base, SessionLocal, engine


# ---------- Логи ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("lab4_worker.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("mq-worker")


# ---------- Идемпотентность через БД ----------
class ProcessedRequestDB(Base):
    __tablename__ = "processed_requests"

    id = Column(String(64), primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    response_json = Column(JSON, nullable=False)


def ensure_tables():
    # создаст users/tasks (если их ещё нет) + processed_requests
    Base.metadata.create_all(bind=engine)


# ---------- Helpers ----------
def _safe_json_loads(raw: bytes) -> Dict[str, Any]:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Invalid JSON: {e}")


def _make_resp(req_id: str, status: str, data: Any = None, error: Optional[str] = None) -> Dict[str, Any]:
    return {"correlation_id": req_id, "status": status, "data": data, "error": error}


def _publish_response(
    ch: pika.adapters.blocking_connection.BlockingChannel,
    s: MQSettings,
    props: pika.BasicProperties,
    body: Dict[str, Any],
):
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")

    # RPC-style: если client прислал reply_to — отвечаем туда (НЕ queue_declare!)
    if props.reply_to:
        ch.basic_publish(
            exchange="",
            routing_key=props.reply_to,
            properties=pika.BasicProperties(
                correlation_id=props.correlation_id,
                delivery_mode=2,
                content_type="application/json",
            ),
            body=payload,
        )
        return

    # fallback: общая очередь ответов
    ch.basic_publish(
        exchange=s.exchange,
        routing_key=s.responses_rk,
        properties=pika.BasicProperties(
            correlation_id=props.correlation_id,
            delivery_mode=2,
            content_type="application/json",
        ),
        body=payload,
    )


def _get_retry_count(props: pika.BasicProperties) -> int:
    h = props.headers or {}
    try:
        return int(h.get("x-retry-count", 0))
    except Exception:
        return 0


def _republish_to_retry(
    ch: pika.adapters.blocking_connection.BlockingChannel,
    s: MQSettings,
    req_body: Dict[str, Any],
    props: pika.BasicProperties,
):
    retry_count = _get_retry_count(props) + 1
    new_props = pika.BasicProperties(
        correlation_id=props.correlation_id,
        reply_to=props.reply_to,
        delivery_mode=2,
        content_type="application/json",
        headers={"x-retry-count": retry_count},
    )
    ch.basic_publish(
        exchange=s.exchange,
        routing_key=s.retry_rk,
        properties=new_props,
        body=json.dumps(req_body, ensure_ascii=False).encode("utf-8"),
    )
    return retry_count


def _send_to_dlq(
    ch: pika.adapters.blocking_connection.BlockingChannel,
    s: MQSettings,
    req_body: Dict[str, Any],
    props: pika.BasicProperties,
    reason: str,
):
    dlq_payload = {
        "failed_at": datetime.utcnow().isoformat(),
        "reason": reason,
        "request": req_body,
        "correlation_id": props.correlation_id,
        "reply_to": props.reply_to,
        "headers": props.headers or {},
    }
    ch.basic_publish(
        exchange=s.exchange,
        routing_key=s.dlq_rk,
        properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
        body=json.dumps(dlq_payload, ensure_ascii=False, default=str).encode("utf-8"),
    )


def _auth_user_from_token(db: Session, token: str) -> UserDB:
    # допускаем "Bearer xxx" и просто "xxx"
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise ValueError("Invalid token")

    user_id = payload.get("sub")
    if not user_id:
        raise ValueError("Invalid token payload (no sub)")

    user = db.query(UserDB).filter(UserDB.id == int(user_id)).first()
    if not user:
        raise ValueError("User not found")
    return user


def _task_to_dict(t: TaskDB) -> Dict[str, Any]:
    return {
        "id": t.id,
        "owner_id": t.owner_id,
        "title": t.title,
        "description": t.description,
        "status": str(t.status),
        "priority": str(t.priority),
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def handle_request(db: Session, req: Dict[str, Any]) -> Dict[str, Any]:
    req_id = str(req.get("id") or "")
    version = str(req.get("version") or "")
    action = str(req.get("action") or "")
    data = req.get("data") or {}
    auth = str(req.get("auth") or "")

    if data.get("simulate_temp_error") is True:
        raise Exception("Simulated temporary error")

    if not req_id or not version or not action:
        return _make_resp(req_id or "unknown", "error", error="Missing required fields: id/version/action")

    # v1.health_check без токена
    if version == "v1" and action == "health_check":
        return _make_resp(req_id, "ok", data={"status": "ok"})

    # v1.register / v1.login — без токена (как в HTTP-версии)
    if version == "v1" and action == "register":
        email = data.get("email")
        password = data.get("password")
        full_name = data.get("full_name")
        if not email or not password or not full_name:
            return _make_resp(req_id, "error", error="email/password/full_name required")

        existing = deps.get_user_by_email(db, email)
        if existing:
            return _make_resp(req_id, "error", error="User already exists")

        user = UserDB(email=email, password_hash=deps.hash_password(password), full_name=full_name)
        db.add(user)
        db.commit()
        db.refresh(user)
        return _make_resp(req_id, "ok", data={"id": user.id, "email": user.email, "full_name": user.full_name})

    if version == "v1" and action == "login":
        email = data.get("email")
        password = data.get("password")
        if not email or not password:
            return _make_resp(req_id, "error", error="email/password required")

        user = deps.get_user_by_email(db, email)
        if not user or not deps.verify_password(password, user.password_hash):
            return _make_resp(req_id, "error", error="Incorrect email or password")

        token = deps.create_access_token({"sub": str(user.id)})
        return _make_resp(req_id, "ok", data={"access_token": token, "token_type": "bearer"})

    # Всё остальное — требует JWT в поле auth
    if not auth:
        return _make_resp(req_id, "error", error="auth (JWT token) required for this action")

    try:
        current_user = _auth_user_from_token(db, auth)
    except ValueError as e:
        return _make_resp(req_id, "error", error=str(e))

    # ---------- TASKS ----------
    if action == "create_task":
        title = data.get("title")
        description = data.get("description")
        due_date = data.get("due_date")

        if not title:
            return _make_resp(req_id, "error", error="title required")

        task = TaskDB(
            owner_id=current_user.id,
            title=title,
            description=description,
        )

        # v2 поддерживает priority
        if version == "v2" and data.get("priority"):
            task.priority = data["priority"]

        # due_date: "YYYY-MM-DD" или ISO
        if due_date:
            try:
                task.due_date = datetime.fromisoformat(due_date)
            except Exception:
                return _make_resp(req_id, "error", error="due_date must be ISO format, e.g. 2025-12-31 or 2025-12-31T10:00:00")

        db.add(task)
        db.commit()
        db.refresh(task)
        return _make_resp(req_id, "ok", data=_task_to_dict(task))

    if action == "list_tasks":
        tasks = db.query(TaskDB).filter(TaskDB.owner_id == current_user.id).order_by(TaskDB.id.desc()).all()
        return _make_resp(req_id, "ok", data=[_task_to_dict(t) for t in tasks])

    if action == "get_task":
        task_id = data.get("task_id")
        if not task_id:
            return _make_resp(req_id, "error", error="task_id required")
        t = db.query(TaskDB).filter(TaskDB.id == int(task_id), TaskDB.owner_id == current_user.id).first()
        if not t:
            return _make_resp(req_id, "error", error="Task not found")
        return _make_resp(req_id, "ok", data=_task_to_dict(t))

    if action == "update_task":
        task_id = data.get("task_id")
        if not task_id:
            return _make_resp(req_id, "error", error="task_id required")
        t = db.query(TaskDB).filter(TaskDB.id == int(task_id), TaskDB.owner_id == current_user.id).first()
        if not t:
            return _make_resp(req_id, "error", error="Task not found")

        for field in ["title", "description", "status", "priority"]:
            if field in data and data[field] is not None:
                if field == "priority" and version != "v2":
                    continue
                setattr(t, field, data[field])

        if "due_date" in data:
            if data["due_date"] is None:
                t.due_date = None
            else:
                try:
                    t.due_date = datetime.fromisoformat(data["due_date"])
                except Exception:
                    return _make_resp(req_id, "error", error="due_date must be ISO format")

        t.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(t)
        return _make_resp(req_id, "ok", data=_task_to_dict(t))

    if action == "delete_task":
        task_id = data.get("task_id")
        if not task_id:
            return _make_resp(req_id, "error", error="task_id required")
        t = db.query(TaskDB).filter(TaskDB.id == int(task_id), TaskDB.owner_id == current_user.id).first()
        if not t:
            return _make_resp(req_id, "error", error="Task not found")
        db.delete(t)
        db.commit()
        return _make_resp(req_id, "ok", data={"deleted": True, "task_id": int(task_id)})

    return _make_resp(req_id, "error", error=f"Unknown action: {version}.{action}")


def on_message(ch, method, props, body, s: MQSettings):
    db = SessionLocal()
    req_body = {}
    req_id = "unknown"

    try:
        req_body = _safe_json_loads(body)
        req_id = str(req_body.get("id") or "unknown")

        # 1) идемпотентность: если уже есть готовый ответ — вернём его
        cached = db.query(ProcessedRequestDB).filter(ProcessedRequestDB.id == req_id).first()
        if cached:
            resp = cached.response_json
            _publish_response(ch, s, props, resp)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            log.info("idem-replay: %s %s.%s", req_id, req_body.get("version"), req_body.get("action"))
            return

        # 2) обработка
        resp = handle_request(db, req_body)

        # 3) если ошибка “временная” — делаем retry через retry-queue
        # для лабы считаем временной: любые исключения (они ловятся ниже)
        # а resp со status=error — это бизнес-ошибка, её НЕ ретраим
        if resp["status"] == "error":
            # отправим копию в DLQ как "unrecoverable"
            _send_to_dlq(ch, s, req_body, props, reason=resp["error"] or "error")
            _publish_response(ch, s, props, resp)

            # сохраняем идемпотентно (чтобы второй раз не гонять)
            db.add(ProcessedRequestDB(id=req_id, response_json=resp))
            db.commit()

            ch.basic_ack(delivery_tag=method.delivery_tag)
            log.error("failed: %s %s.%s error=%s", req_id, req_body.get("version"), req_body.get("action"), resp["error"])
            return

        # 4) успех — сохраняем ответ (идемпотентность)
        db.add(ProcessedRequestDB(id=req_id, response_json=resp))
        db.commit()

        # 5) ответ клиенту
        _publish_response(ch, s, props, resp)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        log.info("ok: %s %s.%s", req_id, req_body.get("version"), req_body.get("action"))

    except ValueError as e:
        # невалидный JSON/поля — это “фатально”
        resp = _make_resp(req_id, "error", error=str(e))
        _send_to_dlq(ch, s, req_body, props, reason=str(e))
        _publish_response(ch, s, props, resp)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        log.error("bad-request: %s err=%s", req_id, e)

    except Exception as e:
        # это “временный сбой”: retry N раз, потом DLQ+ответ
        retry_count = _get_retry_count(props)
        if retry_count < s.max_retries:
            new_retry = _republish_to_retry(ch, s, req_body, props)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            log.warning("retry #%s for %s because %s", new_retry, req_id, e)
            return

        # retries exhausted
        reason = f"retries exhausted: {e}"
        _send_to_dlq(ch, s, req_body, props, reason=reason)
        resp = _make_resp(req_id, "error", error=reason)
        _publish_response(ch, s, props, resp)

        # сохраняем, чтобы не дублировать после повторной отправки того же id
        try:
            db.add(ProcessedRequestDB(id=req_id, response_json=resp))
            db.commit()
        except Exception:
            db.rollback()

        ch.basic_ack(delivery_tag=method.delivery_tag)
        log.error("dead-lettered: %s err=%s", req_id, e)

    finally:
        db.close()


def main():
    ensure_tables()
    s = MQSettings()

    conn = connect(s)
    ch = conn.channel()
    declare_topology(ch, s)

    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(
        queue=s.requests_queue,
        on_message_callback=lambda ch_, method, props, body: on_message(ch_, method, props, body, s),
        auto_ack=False,
    )

    log.info("worker started, consuming %s on %s:%s", s.requests_queue, s.host, s.port)
    ch.start_consuming()


if __name__ == "__main__":
    main()
