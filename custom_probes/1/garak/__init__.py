# Shim so custom probes are loadable as garak.probes.<module>
# This dir must be first on PYTHONPATH when running garak.
import importlib.util
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_path = str(_root)
_self = sys.modules["garak"]
_sys_path_before = list(sys.path)
sys.path = [p for p in sys.path if Path(p).resolve() != _root]
_removed = len(sys.path) < len(_sys_path_before)
del sys.modules["garak"]
import garak as _real_garak
_real_probes = importlib.import_module("garak.probes")
if _removed:
    sys.path.insert(0, _path)
sys.modules["garak"] = _self

# Merge real garak.probes with our custom probe .py files
_probes_dir = Path(__file__).resolve().parent / "probes"
_merged = type(sys)("garak.probes")
_merged.__dict__.update({k: v for k, v in _real_probes.__dict__.items() if not k.startswith("_")})
sys.modules["garak.probes"] = _merged
globals()["probes"] = _merged
for f in sorted(_probes_dir.glob("*.py")):
    if f.stem.startswith("_") or f.name == "__init__.py":
        continue
    mod_name = f.stem
    spec = importlib.util.spec_from_file_location("garak.probes." + mod_name, f)
    if spec is None or spec.loader is None:
        continue
    mod = importlib.util.module_from_spec(spec)
    sys.modules["garak.probes." + mod_name] = mod
    spec.loader.exec_module(mod)
    _merged.__dict__[mod_name] = mod

_d = {k: v for k, v in _real_garak.__dict__.items()}
_d["probes"] = _merged
globals().update(_d)

# Register custom probe classes in garak's plugin cache so CLI accepts them
try:
    _plugins_mod = __import__("garak._plugins", fromlist=["PluginCache"])
    _cache = _plugins_mod.PluginCache.instance()
    for _mod_name, _mod in list(_merged.__dict__.items()):
        if not isinstance(_mod, type(sys)) or not getattr(_mod, "__file__", ""):
            continue
        if str(Path(_mod.__file__).resolve()).startswith(str(_probes_dir.resolve())):
            for _attr in dir(_mod):
                if _attr.startswith("_"):
                    continue
                _klass = getattr(_mod, _attr, None)
                if isinstance(_klass, type) and issubclass(_klass, _real_probes.Probe):
                    _key = f"probes.{_mod_name}.{_attr}"
                    if _key not in _cache.get("probes", {}):
                        try:
                            _cache.setdefault("probes", {})[_key] = _plugins_mod.PluginCache.plugin_info(_klass)
                        except Exception:
                            pass
except Exception:
    pass
