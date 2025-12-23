from __future__ import annotations

import os
import uuid
from datetime import date

from dotenv import load_dotenv

from courseuni_client import CourseUniClient, ApiError


def _rand_email() -> str:
    return f"student_{uuid.uuid4().hex[:8]}@example.com"


def main() -> None:
    load_dotenv()

    base_url = os.getenv("BASE_URL", "http://localhost:3000")
    api_key = os.getenv("API_KEY", "secretapikey123")

    client = CourseUniClient(base_url=base_url, api_key=api_key)

    print("BASE_URL =", base_url)
    print("API_KEY  =", api_key)

    print("\n1) health check (v1)")
    try:
        print(client.health_v1())
    except ApiError as e:
        print("health error:", e)

    print("\n2) create user (v2)")
    try:
        user = client.create_user_v2(
            first_name="Vika",
            last_name="Student",
            email=_rand_email(),
            birthday=str(date(2004, 5, 20)),
            bio="client demo",
            role="student",
        )
        print("created user:", user)
    except ApiError as e:
        print("create user error:", e)
        return

    print("\n3) create course (v2)")
    try:
        course = client.create_course_v2(
            title="Intro to REST",
            description="demo course",
            duration_hours=10,
            rating=4.6,
            level="beginner",
        )
        print("created course:", course)
    except ApiError as e:
        print("create course error:", e)
        return

    print("\n4) create enrollment with idempotency-key (v2)")
    idem = str(uuid.uuid4())
    try:
        enr1 = client.create_enrollment_v2(
            user_id=user.get("_id") or user.get("id"),
            course_id=course.get("_id") or course.get("id"),
            status="enrolled",
            completion_percent=15,
            idempotency_key=idem,
        )
        print("created:", enr1)
    except ApiError as e:
        print("create enrollment error:", e)
        return

    print("\n5) repeat same POST with same Idempotency-Key (should NOT duplicate)")
    try:
        enr2 = client.create_enrollment_v2(
            user_id=user.get("_id") or user.get("id"),
            course_id=course.get("_id") or course.get("id"),
            status="active",
            completion_percent=15,
            idempotency_key=idem,
        )
        print("reused:", enr2)
    except ApiError as e:
        print("repeat enrollment error:", e)


    print("\n9) idempotent operation demo: DELETE course twice")
    course_id = course.get("_id") or course.get("id")

    try:
        client.delete_course_v2(course_id)
        print("first delete: OK (204)")
    except ApiError as e:
        print("first delete error:", e)

    try:
        client.delete_course_v2(course_id)
        print("second delete: OK (should not change anything)")
    except ApiError as e:
        print("second delete expected behavior (often 404):", e)


    print("\n6) list courses with pagination + include=title,level,rating")
    try:
        courses = client.list_courses_v2(page=1, limit=5, include="title,level,rating")
        print("courses:", courses)
    except ApiError as e:
        print("list courses error:", e)

    print("\n7) show error handling: get non-existing course id")
    try:
        client.get_course_v2("000000000000000000000000")
    except ApiError as e:
        print("expected error:", e)

    print("\n8) internal stats (v2) - optional demo endpoint")
    try:
        stats = client.stats_v2()
        print("stats:", stats)
    except ApiError as e:
        print("stats error:", e)


if __name__ == "__main__":
    main()
