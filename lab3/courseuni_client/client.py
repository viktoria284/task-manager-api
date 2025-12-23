from __future__ import annotations

import time
from typing import Any, Optional

import requests

from .exceptions import ApiError


class CourseUniClient:
    """
    Небольшой устойчивый HTTP-клиент для CourseUni REST API.

    Особенности:
    - API-key аутентификация через заголовок X-API-Key
    - обработка rate limit (429 + Retry-After)
    - поддержка идемпотентности (Idempotency-Key для POST /enrollments)
    - базовые CRUD операции + пагинация (page/limit) + include=field1,field2 (v2)
    """

    def __init__(self, base_url: str, api_key: str, *, timeout: int = 15, max_retries_429: int = 3) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries_429 = max_retries_429

        self.session = requests.Session()
        self.session.headers.update({
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # --------------------- low-level ---------------------

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Any = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        url = self._url(path)

        extra_headers: dict[str, str] = {}
        if headers:
            extra_headers.update(headers)

        attempt = 0
        while True:
            attempt += 1
            resp = self.session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=extra_headers,
                timeout=self.timeout,
            )

            # rate limiting
            if resp.status_code == 429:
                if attempt > self.max_retries_429:
                    raise ApiError(
                        status_code=429,
                        message="Too Many Requests (rate limit exceeded)",
                        details={"retry_after": resp.headers.get("Retry-After")},
                        url=url,
                        method=method,
                        headers=dict(resp.headers),
                        response_text=resp.text,
                    )
                retry_after = resp.headers.get("Retry-After", "1")
                try:
                    wait_s = int(retry_after)
                except ValueError:
                    wait_s = 1
                time.sleep(max(wait_s, 1))
                continue

            # no content
            if resp.status_code == 204:
                return None

            # errors
            if resp.status_code >= 400:
                details = None
                message = resp.reason or "Request failed"
                try:
                    details = resp.json()
                    # часто API кладет текст ошибки в поля error/detail/message
                    if isinstance(details, dict):
                        message = details.get("error") or details.get("detail") or details.get("message") or message
                except Exception:
                    details = None

                raise ApiError(
                    status_code=resp.status_code,
                    message=message,
                    details=details,
                    url=url,
                    method=method,
                    headers=dict(resp.headers),
                    response_text=resp.text,
                )

            # ok (json)
            if resp.content:
                try:
                    return resp.json()
                except Exception:
                    # если вдруг вернули не-json
                    return resp.text
            return None

    # --------------------- service endpoints ---------------------

    def health_v1(self) -> Any:
        # этот эндпоинт в README отмечен как публичный
        return self._request("GET", "/api/v1/health")

    # --------------------- v2 users ---------------------

    def list_users_v2(self, *, page: int | None = None, limit: int | None = None, include: str | None = None) -> Any:
        params: dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if limit is not None:
            params["limit"] = limit
        if include:
            params["include"] = include
        return self._request("GET", "/api/v2/users", params=params)

    def create_user_v2(
        self,
        *,
        first_name: str,
        last_name: str,
        email: str,
        birthday: str | None = None,
        bio: str | None = None,
        role: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
        }
        if birthday:
            payload["birthday"] = birthday
        if bio:
            payload["bio"] = bio
        if role:
            payload["role"] = role
        return self._request("POST", "/api/v2/users", json_body=payload)

    def get_user_v2(self, user_id: str) -> Any:
        return self._request("GET", f"/api/v2/users/{user_id}")

    def patch_user_v2(self, user_id: str, **fields: Any) -> Any:
        # fields: firstName, lastName, email, birthday, bio, role
        return self._request("PATCH", f"/api/v2/users/{user_id}", json_body=fields)

    def delete_user_v2(self, user_id: str) -> None:
        self._request("DELETE", f"/api/v2/users/{user_id}")
        return None

    # --------------------- v2 courses ---------------------

    def list_courses_v2(
        self,
        *,
        page: int | None = None,
        limit: int | None = None,
        include: str | None = None,
        min_rating: float | None = None,
        level: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if limit is not None:
            params["limit"] = limit
        if include:
            params["include"] = include
        if min_rating is not None:
            params["minRating"] = min_rating
        if level:
            params["level"] = level
        return self._request("GET", "/api/v2/courses", params=params)

    def create_course_v2(
        self,
        *,
        title: str,
        description: str,
        duration_hours: int,
        rating: float | None = None,
        level: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "durationHours": duration_hours,
        }
        if rating is not None:
            payload["rating"] = rating
        if level:
            payload["level"] = level
        return self._request("POST", "/api/v2/courses", json_body=payload)

    def get_course_v2(self, course_id: str) -> Any:
        return self._request("GET", f"/api/v2/courses/{course_id}")

    def patch_course_v2(self, course_id: str, **fields: Any) -> Any:
        # fields: title, description, durationHours, rating, level
        return self._request("PATCH", f"/api/v2/courses/{course_id}", json_body=fields)

    def delete_course_v2(self, course_id: str) -> None:
        self._request("DELETE", f"/api/v2/courses/{course_id}")
        return None

    # --------------------- v2 enrollments ---------------------

    def list_enrollments_v2(self, *, page: int | None = None, limit: int | None = None, include: str | None = None) -> Any:
        params: dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if limit is not None:
            params["limit"] = limit
        if include:
            params["include"] = include
        return self._request("GET", "/api/v2/enrollments", params=params)

    def create_enrollment_v2(
        self,
        *,
        user_id: str,
        course_id: str,
        status: str | None = None,
        completion_percent: int | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"userId": user_id, "courseId": course_id}
        if status:
            payload["status"] = status
        if completion_percent is not None:
            payload["completionPercent"] = completion_percent

        headers: dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        return self._request("POST", "/api/v2/enrollments", json_body=payload, headers=headers)

    def get_enrollment_v2(self, enrollment_id: str) -> Any:
        return self._request("GET", f"/api/v2/enrollments/{enrollment_id}")

    def patch_enrollment_v2(self, enrollment_id: str, **fields: Any) -> Any:
        # fields: status, completionPercent
        return self._request("PATCH", f"/api/v2/enrollments/{enrollment_id}", json_body=fields)

    def delete_enrollment_v2(self, enrollment_id: str) -> None:
        self._request("DELETE", f"/api/v2/enrollments/{enrollment_id}")
        return None

    # --------------------- v2 internal (не обязателен, но полезен для демо) ---------------------

    def stats_v2(self) -> Any:
        return self._request("GET", "/api/v2/internal/stats")
