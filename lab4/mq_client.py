import json
import uuid
import time

import pika

from lab4.mq_common import MQSettings, connect, declare_topology


def rpc_call(
    ch, conn, s: MQSettings,
    version: str, action: str, data: dict,
    auth: str, timeout_s: int,
    request_id: str | None = None,
):
    req_id = request_id or str(uuid.uuid4())
    req = {
        "id": req_id,
        "version": version,
        "action": action,
        "data": data,
        "auth": auth,
    }

    result = {"resp": None}

    # reply queue (эксклюзивная)
    q = ch.queue_declare(queue="", exclusive=True)
    reply_queue = q.method.queue

    def on_resp(ch_, method, props, body):
        if props.correlation_id == req_id:
            result["resp"] = json.loads(body.decode("utf-8"))

    ch.basic_consume(queue=reply_queue, on_message_callback=on_resp, auto_ack=True)

    ch.basic_publish(
        exchange=s.exchange,
        routing_key=s.requests_rk,
        properties=pika.BasicProperties(
            correlation_id=req_id,
            reply_to=reply_queue,
            delivery_mode=2,
            content_type="application/json",
        ),
        body=json.dumps(req, ensure_ascii=False).encode("utf-8"),
    )

    start = time.time()
    while result["resp"] is None and (time.time() - start) < timeout_s:
        conn.process_data_events(time_limit=0.5)

    return result["resp"]


def main():
    s = MQSettings()

    conn = connect(s)
    ch = conn.channel()
    declare_topology(ch, s)

    # 1) health_check
    r1 = rpc_call(ch, conn, s, "v1", "health_check", {}, auth="", timeout_s=s.rpc_timeout_s)
    print("1) health_check:", r1)

    # retry demo (только если добавила simulate_temp_error в воркер)
    tmp = rpc_call(
        ch, conn, s,
        "v1", "health_check",
        {"simulate_temp_error": True},
        auth="",
        timeout_s=s.rpc_timeout_s
    )
    print("simulate retry:", tmp)

    # создадим уникального юзера
    email = f"student_{uuid.uuid4().hex[:6]}@example.com"
    password = "qwerty123"
    full_name = "Vika Student"

    # 2) register
    r2 = rpc_call(
        ch, conn, s,
        "v1", "register",
        {"email": email, "password": password, "full_name": full_name},
        auth="",
        timeout_s=s.rpc_timeout_s
    )
    print("2) register:", r2)

    # 3) login
    r3 = rpc_call(
        ch, conn, s,
        "v1", "login",
        {"email": email, "password": password},
        auth="",
        timeout_s=s.rpc_timeout_s
    )
    print("3) login:", r3)

    token = None
    if r3 and r3.get("status") == "ok":
        token = r3["data"]["access_token"]

    if not token:
        print("No token, stop.")
        return

    # 4) create_task v1
    r4 = rpc_call(
        ch, conn, s,
        "v1", "create_task",
        {"title": "Buy milk", "description": "demo task", "due_date": None},
        auth=token,
        timeout_s=s.rpc_timeout_s
    )
    print("4) create_task v1:", r4)

    task_id = None
    if r4 and r4.get("status") == "ok":
        task_id = r4["data"]["id"]


    same_id = "IDEMPOTENCY-DEMO-123"

    r4a = rpc_call(
        ch, conn, s,
        "v1", "create_task",
        {"title": "Idem task", "description": "should not duplicate"},
        auth=token,
        timeout_s=s.rpc_timeout_s
    )
    print("create_task (first):", r4a)

    # повторим тот же запрос, но с тем же id
    # Для этого rpc_call надо уметь принимать request_id (добавь параметр в функцию)
    r4b = rpc_call(
        ch, conn, s,
        "v1", "create_task",
        {"title": "Idem task", "description": "should not duplicate"},
        auth=token,
        timeout_s=s.rpc_timeout_s,
        request_id=same_id
    )
    r4c = rpc_call(
        ch, conn, s,
        "v1", "create_task",
        {"title": "Idem task", "description": "should not duplicate"},
        auth=token,
        timeout_s=s.rpc_timeout_s,
        request_id=same_id
    )
    print("create_task (idem #1):", r4b)
    print("create_task (idem #2):", r4c)

    # 5) list_tasks
    r5 = rpc_call(ch, conn, s, "v1", "list_tasks", {}, auth=token, timeout_s=s.rpc_timeout_s)
    print("5) list_tasks:", r5)

    # 6) update_task v2 (с приоритетом)
    if task_id:
        r6 = rpc_call(
            ch, conn, s,
            "v2", "update_task",
            {"task_id": task_id, "priority": "high", "status": "in_progress"},
            auth=token,
            timeout_s=s.rpc_timeout_s
        )
        print("6) update_task v2:", r6)

    # DLQ demo
    bad = rpc_call(ch, conn, s, "v1", "abracadabra", {}, auth=token, timeout_s=s.rpc_timeout_s)
    print("bad action:", bad)


if __name__ == "__main__":
    main()
