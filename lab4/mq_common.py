import os
from dataclasses import dataclass

import pika


@dataclass(frozen=True)
class MQSettings:
    host: str = os.getenv("MQ_HOST", "localhost")
    # у тебя сейчас слушает 5673 — оставляем дефолтом так
    port: int = int(os.getenv("MQ_PORT", "5673"))
    user: str = os.getenv("MQ_USER", "guest")
    password: str = os.getenv("MQ_PASSWORD", "guest")
    vhost: str = os.getenv("MQ_VHOST", "/")

    exchange: str = os.getenv("MQ_EXCHANGE", "api.direct")
    exchange_type: str = "direct"

    requests_queue: str = "api.requests"
    requests_rk: str = "api.requests"

    responses_queue: str = "api.responses"
    responses_rk: str = "api.responses"

    retry_queue: str = "api.requests.retry"
    retry_rk: str = "api.requests.retry"

    dlq_queue: str = "api.requests.dlq"
    dlq_rk: str = "api.requests.dlq"

    retry_delay_ms: int = int(os.getenv("MQ_RETRY_DELAY_MS", "5000"))  # 5 сек
    max_retries: int = int(os.getenv("MQ_MAX_RETRIES", "3"))
    rpc_timeout_s: int = int(os.getenv("MQ_RPC_TIMEOUT_S", "30"))


def connect(settings: MQSettings) -> pika.BlockingConnection:
    credentials = pika.PlainCredentials(settings.user, settings.password)
    params = pika.ConnectionParameters(
        host=settings.host,
        port=settings.port,
        virtual_host=settings.vhost,
        credentials=credentials,
        heartbeat=60,
        blocked_connection_timeout=30,
    )
    return pika.BlockingConnection(params)


def declare_topology(ch: pika.adapters.blocking_connection.BlockingChannel, s: MQSettings) -> None:
    ch.exchange_declare(exchange=s.exchange, exchange_type=s.exchange_type, durable=True)

    # requests
    ch.queue_declare(queue=s.requests_queue, durable=True)
    ch.queue_bind(queue=s.requests_queue, exchange=s.exchange, routing_key=s.requests_rk)

    # responses (на случай если reply_to не передали)
    ch.queue_declare(queue=s.responses_queue, durable=True)
    ch.queue_bind(queue=s.responses_queue, exchange=s.exchange, routing_key=s.responses_rk)

    # retry queue: TTL -> DLX обратно в requests
    ch.queue_declare(
        queue=s.retry_queue,
        durable=True,
        arguments={
            "x-message-ttl": s.retry_delay_ms,
            "x-dead-letter-exchange": s.exchange,
            "x-dead-letter-routing-key": s.requests_rk,
        },
    )
    ch.queue_bind(queue=s.retry_queue, exchange=s.exchange, routing_key=s.retry_rk)

    # DLQ
    ch.queue_declare(queue=s.dlq_queue, durable=True)
    ch.queue_bind(queue=s.dlq_queue, exchange=s.exchange, routing_key=s.dlq_rk)
