import json
from typing import Any


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def json_loads(s: str | None, default: Any) -> Any:
    if not s:
        return default
    return json.loads(s)

