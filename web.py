from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse


def redirect(location: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(location, status_code=status_code)


def flash(request: Request, message: str, level: str = "info") -> None:
    # Store one-time messages in the session; templates pop them.
    request.session.setdefault("_flashes", []).append({"message": message, "level": level})  # type: ignore[attr-defined]

