import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import sessionmaker

from loophedge.memory.skills import SkillsRepo
from loophedge.models import Strategy


class StrategyRegistry:
    def __init__(self, session_factory: sessionmaker, skills_repo: SkillsRepo):
        self.session_factory = session_factory
        self.skills = skills_repo

    def register_pending(self, name: str, source_code: str,
                          hyperparams: dict, actor: str) -> str:
        relpath = f"strategies/pending/{name}.py"
        self.skills.write(relpath, source_code, actor=actor,
                           reason=f"genesis proposed {name}")
        sid = str(uuid.uuid4())
        with self.session_factory() as s:
            s.add(Strategy(id=sid, name=name, status="pending",
                            source_path=relpath, hyperparams=hyperparams,
                            created_at=datetime.now(UTC)))
            s.commit()
        return sid

    def promote(self, name: str, actor: str, reason: str) -> None:
        self._move(name, src_sub="pending", dst_sub="active", actor=actor, reason=reason)
        with self.session_factory() as s:
            row = s.query(Strategy).filter_by(name=name).one()
            row.status = "active"
            row.promoted_at = datetime.now(UTC)
            row.promoted_reason = reason
            row.source_path = f"strategies/active/{name}.py"
            s.commit()

    def retire(self, name: str, actor: str, reason: str) -> None:
        # Auto-detect whether the strategy is currently active or pending.
        src_sub = "active"
        if not (self.skills.root / "strategies" / "active" / f"{name}.py").exists():
            src_sub = "pending"
        self._move(name, src_sub=src_sub, dst_sub="retired", actor=actor, reason=reason)
        with self.session_factory() as s:
            row = s.query(Strategy).filter_by(name=name).one()
            row.status = "retired"
            row.retired_at = datetime.now(UTC)
            row.retired_reason = reason
            row.source_path = f"strategies/retired/{name}.py"
            s.commit()

    def list_active(self) -> list[Strategy]:
        with self.session_factory() as s:
            return s.query(Strategy).filter_by(status="active").all()

    def list_pending(self) -> list[Strategy]:
        with self.session_factory() as s:
            return s.query(Strategy).filter_by(status="pending").all()

    def _move(self, name: str, src_sub: str, dst_sub: str, actor: str, reason: str) -> None:
        src = self.skills.root / "strategies" / src_sub / f"{name}.py"
        if not src.exists():
            raise FileNotFoundError(f"strategy {name} not in {src_sub}/")
        content = src.read_text(encoding="utf-8")
        src.unlink()
        self.skills.write(f"strategies/{dst_sub}/{name}.py", content,
                           actor=actor, reason=f"{src_sub}->{dst_sub}: {reason}")
        # also stage the deletion so the repo reflects it
        self.skills._repo.index.remove([f"strategies/{src_sub}/{name}.py"], working_tree=True)
        self.skills._repo.index.commit(f"{actor}: removed {name} from {src_sub}/ ({reason})")
