from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpCheckResult:
    ok: bool
    status: int | None
    error: str
    duration_ms: int


def http_get_json(url: str, timeout_s: float = 3.0) -> tuple[HttpCheckResult, dict | None]:
    start = time.time()
    try:
        req = Request(url, method="GET", headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout_s) as resp:
            status = getattr(resp, "status", None) or 200
            data = resp.read()
        try:
            body = json.loads(data.decode("utf-8", errors="replace"))
        except Exception:
            body = None
        return HttpCheckResult(True, int(status), "", int((time.time() - start) * 1000)), body
    except URLError as e:
        return HttpCheckResult(False, None, f"URLError: {e}", int((time.time() - start) * 1000)), None
    except socket.timeout:
        return HttpCheckResult(False, None, "timeout", int((time.time() - start) * 1000)), None
    except Exception as e:
        return HttpCheckResult(False, None, repr(e), int((time.time() - start) * 1000)), None

