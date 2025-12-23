from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ApiError(Exception):
    status_code: int
    message: str
    details: Any = None
    url: Optional[str] = None
    method: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    response_text: Optional[str] = None

    def __str__(self) -> str:
        base = f"API error {self.status_code}: {self.message}"
        if self.method and self.url:
            base = f"{self.method} {self.url} -> " + base
        if self.details is not None:
            return base + f" | details={self.details}"
        return base
