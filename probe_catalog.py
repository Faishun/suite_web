from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass

from suite_web.settings import Settings


_PROBE_TOKEN_RE = re.compile(r"\b([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)\b")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass(frozen=True)
class ProbeOption:
    value: str
    label: str
    description: str = ""  # Short description / goal when available


_CACHE: dict[str, tuple[float, list[str], str]] = {}
# probe_name -> { "goal": str, "description": str }
_CACHE_DESCRIPTIONS: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}
_DESCRIPTION_TTL_S = 300.0


def _cache_get(key: str, ttl_s: float) -> tuple[list[str], str] | None:
    ts, items, err = _CACHE.get(key, (0.0, [], ""))
    if time.time() - ts <= ttl_s:
        return items, err
    return None


def _cache_set(key: str, items: list[str], err: str) -> None:
    _CACHE[key] = (time.time(), items, err)


def list_garak_probes(ttl_s: float = 30.0) -> tuple[list[str], str]:
    cached = _cache_get("garak_probes", ttl_s)
    if cached is not None:
        return cached

    try:
        out = subprocess.check_output([sys.executable, "-m", "garak", "--list_probes"], stderr=subprocess.STDOUT, text=True)
        cleaned = strip_ansi(out)
        probes = sorted(set(_PROBE_TOKEN_RE.findall(cleaned)))
        _cache_set("garak_probes", probes, "")
        return probes, ""
    except Exception as e:
        _cache_set("garak_probes", [], f"Failed to list garak probes: {e!r}")
        return [], f"Failed to list garak probes: {e!r}"


def list_garak_detectors(ttl_s: float = 60.0) -> tuple[list[str], str]:
    """Return sorted list of Garak detector names (e.g. always.Fail, dan.DAN). Cached."""
    cached = _cache_get("garak_detectors", ttl_s)
    if cached is not None:
        return cached

    try:
        out = subprocess.check_output([sys.executable, "-m", "garak", "--list_detectors"], stderr=subprocess.STDOUT, text=True)
        cleaned = strip_ansi(out)
        detectors = sorted(set(_PROBE_TOKEN_RE.findall(cleaned)))
        _cache_set("garak_detectors", detectors, "")
        return detectors, ""
    except Exception as e:
        _cache_set("garak_detectors", [], f"Failed to list garak detectors: {e!r}")
        return [], f"Failed to list garak detectors: {e!r}"


def _garak_probe_descriptions_script() -> str:
    """Return Python script that reads probe names from stdin (JSON array) and prints JSON dict name -> {goal, description}."""
    return r"""
import json, sys
try:
    names = json.load(sys.stdin)
except Exception:
    names = []
out = {}
for full_name in names:
    if not isinstance(full_name, str) or '.' not in full_name:
        continue
    parts = full_name.split('.', 1)
    mod_name, class_name = parts[0], parts[1]
    try:
        mod = __import__('garak.probes.' + mod_name, fromlist=[class_name])
        cls = getattr(mod, class_name, None)
        if cls is None:
            continue
        inst = cls()
        goal = getattr(inst, 'goal', None) or ''
        desc = getattr(inst, 'description', None) or ''
        out[full_name] = {'goal': goal, 'description': desc}
    except Exception:
        pass
print(json.dumps(out))
"""


def get_garak_probe_descriptions(probe_names: list[str], ttl_s: float = _DESCRIPTION_TTL_S) -> dict[str, dict[str, str]]:
    """Return dict probe_name -> {goal, description} for each probe that could be loaded. Cached."""
    cache_key = "garak_descriptions"
    ts, data = _CACHE_DESCRIPTIONS.get(cache_key, (0.0, {}))
    if time.time() - ts <= ttl_s and data:
        return data
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _garak_probe_descriptions_script()],
            input=json.dumps(probe_names),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            _CACHE_DESCRIPTIONS[cache_key] = (time.time(), data)
            return data
    except Exception:
        pass
    return {}


def list_augustus_detectors(settings: Settings, ttl_s: float = 60.0) -> tuple[list[str], str]:
    """Return sorted list of Augustus detector names. Parses 'augustus list' output. Cached."""
    cached = _cache_get("augustus_detectors", ttl_s)
    if cached is not None:
        return cached

    try:
        cmd: list[str]
        cwd = settings.augustus_dir
        if settings.augustus_bin:
            cmd = [settings.augustus_bin, "list"]
        else:
            cmd = ["go", "run", "./cmd/augustus", "list"]
        out = subprocess.check_output(cmd, cwd=str(cwd), stderr=subprocess.DEVNULL, text=True, timeout=60)
        cleaned = strip_ansi(out)
        detectors: list[str] = []
        in_detectors = False
        for line in cleaned.splitlines():
            if line.strip().startswith("Detectors ("):
                in_detectors = True
                continue
            if in_detectors:
                if not line.strip():
                    break
                if line.strip().startswith("- "):
                    name = line.strip()[2:].strip()
                    if name and "." in name:
                        detectors.append(name)
        detectors = sorted(set(detectors))
        _cache_set("augustus_detectors", detectors, "")
        return detectors, ""
    except Exception as e:
        _cache_set("augustus_detectors", [], f"Failed to list augustus detectors: {e!r}")
        return [], f"Failed to list augustus detectors: {e!r}"


def list_augustus_probes(settings: Settings, ttl_s: float = 60.0) -> tuple[list[str], str]:
    cached = _cache_get("augustus_probes", ttl_s)
    if cached is not None:
        return cached

    # Best-effort: use `augustus list` if the binary exists. Otherwise try `go run`.
    try:
        cmd: list[str]
        cwd = settings.augustus_dir
        if settings.augustus_bin:
            cmd = [settings.augustus_bin, "list"]
        else:
            cmd = ["go", "run", "./cmd/augustus", "list"]

        out = subprocess.check_output(cmd, cwd=str(cwd), stderr=subprocess.STDOUT, text=True)
        # Heuristic parse: probe names are usually like "dan.BasicDAN" and appear as tokens.
        cleaned = strip_ansi(out)
        probes = sorted(set(_PROBE_TOKEN_RE.findall(cleaned)))
        _cache_set("augustus_probes", probes, "")
        return probes, ""
    except Exception as e:
        _cache_set("augustus_probes", [], f"Failed to list augustus probes: {e!r}")
        return [], f"Failed to list augustus probes: {e!r}"


def get_augustus_probe_descriptions(
    settings: Settings, ttl_s: float = _DESCRIPTION_TTL_S
) -> dict[str, dict[str, str]]:
    """Return dict probe_name -> {description, goal} when augustus list --json is supported. Cached."""
    cache_key = "augustus_descriptions"
    ts, data = _CACHE_DESCRIPTIONS.get(cache_key, (0.0, {}))
    if time.time() - ts <= ttl_s and data:
        return data
    try:
        cmd: list[str]
        cwd = settings.augustus_dir
        if settings.augustus_bin:
            cmd = [settings.augustus_bin, "list", "-j"]
        else:
            cmd = ["go", "run", "./cmd/augustus", "list", "-j"]
        out = subprocess.check_output(cmd, cwd=str(cwd), stderr=subprocess.DEVNULL, text=True, timeout=60)
        raw = json.loads(out)
        # Normalize to {name: {goal, description}} to match Garak shape
        data = {}
        for name, meta in raw.items():
            if isinstance(meta, dict):
                data[name] = {
                    "goal": meta.get("goal") or "",
                    "description": meta.get("description") or "",
                }
            else:
                data[name] = {"goal": "", "description": ""}
        _CACHE_DESCRIPTIONS[cache_key] = (time.time(), data)
        return data
    except Exception:
        pass
    return {}


def build_probe_options(values: list[str]) -> list[ProbeOption]:
    return [ProbeOption(value=v, label=v) for v in values]


def strip_ansi(text: str) -> str:
    # Garak/Augustus output can include ANSI color sequences; if we don't strip them,
    # extracted tokens can look like "0mdan.Foo" and then runs fail with "Unknown probes".
    if not text:
        return ""
    return _ANSI_ESCAPE_RE.sub("", text)


