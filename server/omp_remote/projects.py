from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Mapping


_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    name: str
    owner_users: frozenset[str]
    source_path: Path

    def public(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name}


class ProjectRegistry:
    def __init__(self, projects: tuple[Project, ...]):
        self._projects = {project.id: project for project in projects}

    @classmethod
    def from_environment(
        cls, allowed_users: tuple[str, ...], environ: Mapping[str, str] | None = None
    ) -> "ProjectRegistry":
        env = os.environ if environ is None else environ
        raw = env.get("OMP_REMOTE_PROJECTS_JSON", "[]")
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("OMP_REMOTE_PROJECTS_JSON must be valid JSON") from exc
        if not isinstance(values, list):
            raise ValueError("OMP_REMOTE_PROJECTS_JSON must be a JSON array")
        allowlist = frozenset(allowed_users)
        projects: list[Project] = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("project entries must be objects")
            project_id = value.get("id")
            name = value.get("name")
            owners = value.get("owner_users")
            source_path = value.get("source_path")
            if (
                not isinstance(project_id, str)
                or not _PROJECT_ID.fullmatch(project_id)
                or not isinstance(name, str)
                or not name.strip()
                or not isinstance(owners, list)
                or not isinstance(source_path, str)
                or not Path(source_path).is_absolute()
            ):
                raise ValueError("invalid project definition")
            owner_users = frozenset(owners)
            if not owner_users or not owner_users <= allowlist:
                raise ValueError("project owner is not allowlisted")
            projects.append(Project(project_id, name.strip(), owner_users, Path(source_path)))
        if len({project.id for project in projects}) != len(projects):
            raise ValueError("project IDs must be unique")
        return cls(tuple(projects))

    def list_for(self, owner_user: str) -> list[dict[str, str]]:
        return [project.public() for project in self._projects.values() if owner_user in project.owner_users]

    def get_for(self, project_id: str, owner_user: str) -> Project | None:
        project = self._projects.get(project_id)
        return project if project is not None and owner_user in project.owner_users else None
