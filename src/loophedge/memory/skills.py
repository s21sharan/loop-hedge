from pathlib import Path

import git


class SkillsRepo:
    """File-backed memory for skill markdown + strategy code, every write committed."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        if not (self.root / ".git").exists():
            raise ValueError(f"{root} is not a git repo")
        self._repo = git.Repo(self.root)

    def read(self, relpath: str) -> str:
        path = self._safe_path(relpath)
        return path.read_text(encoding="utf-8")

    def write(self, relpath: str, content: str, actor: str, reason: str) -> str:
        path = self._safe_path(relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._repo.index.add([str(path.relative_to(self.root))])
        commit = self._repo.index.commit(f"{actor}: {reason}")
        return commit.hexsha

    def read_strategy(self, name: str) -> str:
        for sub in ("active", "pending"):
            candidate = self.root / "strategies" / sub / f"{name}.py"
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        raise FileNotFoundError(f"strategy {name} not found in active/ or pending/")

    def _safe_path(self, relpath: str) -> Path:
        path = (self.root / relpath).resolve()
        if not str(path).startswith(str(self.root)):
            raise ValueError(f"{relpath} resolves outside skills root")
        return path
