from datetime import datetime

from loophedge.memory.skills import SkillsRepo


class LessonsLog:
    def __init__(self, skills_repo: SkillsRepo):
        self.skills = skills_repo

    def append(self, actor: str, ts: datetime, summary: str) -> None:
        existing = self.skills.read("LESSONS.md")
        entry = f"- {ts.isoformat()} [{actor}] {summary}\n"
        new = existing.rstrip() + "\n" + entry
        self.skills.write("LESSONS.md", new, actor=actor, reason=f"new lesson")

    def recent(self, n: int = 20) -> list[str]:
        body = self.skills.read("LESSONS.md")
        bullets = [ln for ln in body.splitlines() if ln.startswith("- ")]
        return bullets[-n:]
