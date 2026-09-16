"""Загрузка скиллов: `skills/<name>/skill.yaml` по схеме skill_manifest и файлы промптов.

Промпт — Markdown с заголовком между `---`: id, version, skill, model_role. Версия из заголовка
должна совпадать с манифестом, иначе загрузка падает: версия промпта входит в ключ кэша
и в результат генерации, расхождение сделало бы кэш и метрики недостоверными.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from functools import cache

import yaml

from presentation_designer.contracts import SkillManifest
from presentation_designer.llm.types import (
    JsonDict,
    Message,
    ReasoningMode,
    Request,
    ResponseFormat,
)
from presentation_designer.shared.settings import ROOT

SKILLS_DIR = ROOT / "skills"


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    path: pathlib.Path
    header: JsonDict
    body: str

    @property
    def ref(self) -> tuple[str, str]:
        return (self.id, self.version)


@dataclass(frozen=True)
class Skill:
    manifest: SkillManifest
    prompts: dict[str, Prompt]
    dir: pathlib.Path

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def ref(self) -> tuple[str, str]:
        return (self.manifest.name, self.manifest.version)

    @property
    def model_role(self) -> str:
        return self.manifest.model_role or "llm"

    def prompt(self, prompt_id: str) -> Prompt:
        try:
            return self.prompts[prompt_id]
        except KeyError as e:
            raise SkillError(f"у скилла {self.name} нет промпта {prompt_id}") from e

    def request(
        self,
        prompt_id: str,
        user_text: str,
        *,
        schema: JsonDict | None = None,
        stage: str | None = None,
        **overrides: object,
    ) -> Request:
        """Запрос по параметрам манифеста: системный промпт из файла, формат и рассуждение
        из skill.yaml; явные аргументы переопределяют манифест."""
        prompt = self.prompt(prompt_id)
        m = self.manifest
        params = m.params or {}
        reasoning: ReasoningMode | None = m.reasoning.mode if m.reasoning else None
        max_out = m.reasoning.max_output_tokens if m.reasoning else None
        response_format: ResponseFormat = m.response_format or "text"
        req = Request(
            role=self.model_role,
            messages=[Message("system", prompt.body), Message("user", user_text)],
            response_format=response_format,
            schema=schema,
            schema_name=m.output_schema or prompt_id.replace(".", "_"),
            reasoning=reasoning,
            max_output_tokens=max_out,
            temperature=params.get("temperature"),
            seed=params.get("seed"),
            prompt=prompt.ref,
            skill=self.ref,
            stage=stage or m.stage,
        )
        for key, value in overrides.items():
            if not hasattr(req, key):
                raise SkillError(f"неизвестный параметр запроса {key}")
            setattr(req, key, value)
        return req


def parse_prompt_file(path: pathlib.Path) -> tuple[JsonDict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SkillError(f"{path}: у промпта нет заголовка ---")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillError(f"{path}: заголовок промпта не закрыт")
    header = yaml.safe_load(parts[1]) or {}
    if not isinstance(header, dict):
        raise SkillError(f"{path}: заголовок промпта должен быть объектом")
    return header, parts[2].strip("\n")


def load_skill(name: str, skills_dir: pathlib.Path | None = None) -> Skill:
    base = (skills_dir or SKILLS_DIR) / name
    manifest_path = base / "skill.yaml"
    if not manifest_path.exists():
        raise SkillError(f"скилл {name} не найден: {manifest_path}")
    manifest = SkillManifest.model_validate(yaml.safe_load(manifest_path.read_text()))
    if manifest.name != name:
        raise SkillError(f"{manifest_path}: name={manifest.name}, а каталог {name}")
    prompts: dict[str, Prompt] = {}
    for entry in manifest.prompts:
        path = base / entry.path
        if not path.exists():
            raise SkillError(f"{manifest_path}: нет файла промпта {path}")
        header, body = parse_prompt_file(path)
        version = str(header.get("version", ""))
        if version != entry.version:
            raise SkillError(
                f"{path}: версия в заголовке {version!r} не совпадает "
                f"с манифестом {entry.version!r}"
            )
        if header.get("id", entry.id) != entry.id:
            raise SkillError(f"{path}: id в заголовке {header.get('id')!r} не {entry.id!r}")
        prompts[entry.id] = Prompt(entry.id, entry.version, path, header, body)
    return Skill(manifest, prompts, base)


def list_skills(skills_dir: pathlib.Path | None = None) -> list[str]:
    base = skills_dir or SKILLS_DIR
    return sorted(p.parent.name for p in base.glob("*/skill.yaml"))


@cache
def get_skill(name: str) -> Skill:
    return load_skill(name)


def version_refs(skills_dir: pathlib.Path | None = None) -> tuple[list[JsonDict], list[JsonDict]]:
    """Ссылки на версии всех скиллов и промптов для GenerationResult.versions."""
    skills: list[JsonDict] = []
    prompts: list[JsonDict] = []
    for name in list_skills(skills_dir):
        skill = load_skill(name, skills_dir)
        skills.append({"name": skill.name, "version": skill.version})
        prompts.extend({"name": p.id, "version": p.version} for p in skill.prompts.values())
    return skills, prompts
