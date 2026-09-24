"""Task Graph : plan d'exécution déclaratif (DAG) piloté par le supervisor.

Le plan est une donnée validée (Pydantic), pas du code : ids uniques,
dépendances existantes, absence de cycle, agents connus. Le supervisor
n'exécute que les tâches « prêtes » (toutes dépendances terminées).
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


AgentName = Literal["collector", "prefilter", "research", "review", "editorial"]
Status = Literal["pending", "running", "done", "failed", "skipped"]


class Task(BaseModel):
    id: str
    agent: AgentName
    deps: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    # Une tâche optionnelle en échec n'arrête pas le run (ex. une source).
    optional: bool = False


class TaskGraph(BaseModel):
    tasks: list[Task]

    @model_validator(mode="after")
    def check_dag(self) -> "TaskGraph":
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de tâches dupliqués")
        known = set(ids)
        for task in self.tasks:
            if missing := set(task.deps) - known:
                raise ValueError(f"{task.id} dépend de tâches inconnues : {sorted(missing)}")
        self.topological_order()  # lève si cycle
        return self

    def by_id(self, task_id: str) -> Task:
        return next(task for task in self.tasks if task.id == task_id)

    def topological_order(self) -> list[str]:
        remaining = {task.id: set(task.deps) for task in self.tasks}
        order: list[str] = []
        while remaining:
            ready = sorted(tid for tid, deps in remaining.items() if not deps)
            if not ready:
                raise ValueError(f"cycle détecté entre : {sorted(remaining)}")
            for tid in ready:
                order.append(tid)
                del remaining[tid]
            for deps in remaining.values():
                deps.difference_update(ready)
        return order

    def ready(self, status: dict[str, str]) -> list[Task]:
        """Tâches en attente dont toutes les dépendances sont terminées."""
        finished = {"done", "skipped"} | {"failed"}
        return [
            task
            for task in self.tasks
            if status.get(task.id, "pending") == "pending"
            and all(
                status.get(dep) in finished
                and (status.get(dep) != "failed" or self.by_id(dep).optional)
                for dep in task.deps
            )
        ]

    def blocking_failure(self, status: dict[str, str]) -> Task | None:
        return next(
            (t for t in self.tasks if status.get(t.id) == "failed" and not t.optional),
            None,
        )

    def complete(self, status: dict[str, str]) -> bool:
        return all(status.get(task.id) in {"done", "skipped", "failed"} for task in self.tasks)

    def to_mermaid(self, status: dict[str, str] | None = None) -> str:
        status = status or {}
        lines = ["flowchart LR"]
        for task in self.tasks:
            label = f"{task.id}<br/><i>{task.agent}</i>"
            lines.append(f'    {task.id.replace(":", "_")}["{label}"]:::{status.get(task.id, "pending")}')
            for dep in task.deps:
                lines.append(f"    {dep.replace(':', '_')} --> {task.id.replace(':', '_')}")
        lines += [
            "    classDef pending fill:#eee,stroke:#999",
            "    classDef done fill:#d4f4dd,stroke:#2e7d32",
            "    classDef failed fill:#fde2e1,stroke:#c62828",
            "    classDef skipped fill:#f5f5f5,stroke:#bbb,stroke-dasharray:3",
        ]
        return "\n".join(lines)


COLLECTORS = ("rss", "arxiv", "github_releases", "github_mcp")


def default_plan(collect: bool = True, collectors: tuple[str, ...] = COLLECTORS) -> TaskGraph:
    collect_ids = [f"collect:{name}" for name in collectors] if collect else []
    tasks = [
        Task(id=task_id, agent="collector", params={"source": task_id.split(":", 1)[1]}, optional=True)
        for task_id in collect_ids
    ]
    tasks += [
        Task(id="prefilter", agent="prefilter", deps=collect_ids),
        Task(id="research", agent="research", deps=["prefilter"]),
        Task(id="review", agent="review", deps=["research"]),
        Task(id="editorial", agent="editorial", deps=["review"]),
    ]
    return TaskGraph(tasks=tasks)
