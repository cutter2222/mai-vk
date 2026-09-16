"""Ошибка API в формате контракта: {"error": {code, message, details?}}."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def body(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


def not_found(code: str, message: str) -> ApiError:
    return ApiError(404, code, message)
