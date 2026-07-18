import importlib.util
from pathlib import Path
from types import ModuleType

from loophedge.memory.skills import SkillsRepo


def load_strategy(name: str, skills_repo: SkillsRepo) -> ModuleType:
    for sub in ("active", "pending"):
        path = skills_repo.root / "strategies" / sub / f"{name}.py"
        if path.exists():
            return _load_module(path, name)
    raise FileNotFoundError(f"strategy {name} not found in active/ or pending/")


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"loophedge_strategies.{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for attr in ("NAME", "DEFAULT_HYPERPARAMS", "generate_signals"):
        if not hasattr(module, attr):
            raise AttributeError(f"strategy {name} missing required attribute {attr}")
    return module
