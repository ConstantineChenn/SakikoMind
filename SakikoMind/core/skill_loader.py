"""
SakikoMind Skill 加载器。

Skill 是一段可热加载的业务能力说明，用来补充 Agent 的 system prompt。
它适合放置企业话术、处理流程、合规边界、排障 SOP 等需要运营侧快速调整的规则。
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """单个 Skill 的标准化表示，屏蔽 Markdown/JSON 等不同文件格式差异。"""
    name: str
    description: str
    content: str
    path: str
    keywords: List[str] = field(default_factory=list)
    agents: List[str] = field(default_factory=list)
    version: str = "v1.0"
    owner: str = ""
    updated_at: str = ""
    escalation_conditions: List[str] = field(default_factory=list)
    escalation_keywords: List[str] = field(default_factory=list)
    prohibited_commitments: List[str] = field(default_factory=list)
    enabled: bool = True

    def matches(self, message: str, agent_type: Optional[str] = None) -> bool:
        """
        判断当前请求是否应该注入这个 Skill。

        - agents 为空：适用于所有 Agent；否则只匹配指定 Agent。
        - keywords 为空：作为全局 Skill 注入；否则只有命中关键词才注入。
        """
        if not self.enabled:
            return False

        if self.agents and agent_type and agent_type.lower() not in self.agents:
            return False

        if not self.keywords:
            return True

        lowered = (message or "").lower()
        return any(keyword.lower() in lowered for keyword in self.keywords)

    def to_prompt_block(self, max_chars: int = 3200) -> str:
        """格式化为可直接拼入 system prompt 的文本块，并限制单个 Skill 长度。"""
        body = self.content.strip()
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n..."
        description = f"\n说明: {self.description}" if self.description else ""
        return f"### {self.name}{description}\n{body}"

    def to_summary(self) -> Dict[str, Any]:
        """返回 API 可序列化摘要，避免把完整长文本默认暴露给健康检查。"""
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "keywords": self.keywords,
            "agents": self.agents,
            "version": self.version,
            "owner": self.owner,
            "updated_at": self.updated_at,
            "escalation_conditions": self.escalation_conditions,
            "escalation_keywords": self.escalation_keywords,
            "prohibited_commitments": self.prohibited_commitments,
            "enabled": self.enabled,
            "content_chars": len(self.content),
        }

    def to_usage(self, matched_keywords: List[str]) -> Dict[str, Any]:
        """返回单次请求实际命中的 Skill 摘要。"""
        return {
            "name": self.name,
            "version": self.version,
            "owner": self.owner,
            "matched_keywords": matched_keywords,
        }

    def escalation_matches(self, message: str, agent_type: Optional[str] = None) -> List[str]:
        """返回命中且要求人工升级的关键词，供接口稳定标记高风险请求。"""
        if not self.matches(message, agent_type):
            return []
        keywords = self.escalation_keywords or self.escalation_conditions
        lowered = (message or "").lower()
        return [keyword for keyword in keywords if keyword.lower() in lowered]


class SkillManager:
    """
    从目录中发现、解析并管理 Skills。

    支持两种常用结构：
      1. skills/refund/SKILL.md
      2. skills/refund.json / skills/refund.md / skills/refund.txt
    """

    SUPPORTED_SUFFIXES = {".md", ".txt", ".json"}

    def __init__(self, root_dir: str, max_prompt_chars: int = 5000):
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.max_prompt_chars = max_prompt_chars
        self._skills: List[Skill] = []
        self._errors: List[str] = []

    @property
    def skills(self) -> List[Skill]:
        return list(self._skills)

    @property
    def errors(self) -> List[str]:
        return list(self._errors)

    def load(self) -> List[Skill]:
        """重新扫描目录并加载 Skills；单个文件失败不会影响其他 Skill 生效。"""
        loaded: List[Skill] = []
        errors: List[str] = []

        if not self.root_dir.exists():
            logger.info(f"Skill 目录不存在，跳过加载: {self.root_dir}")
            self._skills = []
            self._errors = []
            return []

        for path in self._discover_files(self.root_dir):
            try:
                skill = self._load_file(path)
                if skill is not None:
                    loaded.append(skill)
            except Exception as ex:
                msg = f"{path}: {ex}"
                errors.append(msg)
                logger.warning(f"Skill 加载失败: {msg}")

        self._skills = loaded
        self._errors = errors
        self._log_loaded_skills()
        return self.skills

    def reload(self) -> List[Skill]:
        """运行时热加载入口，供 API 调用。"""
        return self.load()

    def prompt_for(self, message: str, agent_type: Optional[str] = None) -> str:
        """
        为当前用户请求构建 Skill prompt。

        只注入匹配的 Skill，并按总长度截断，避免挤占主对话上下文。
        """
        blocks: List[str] = []
        matched = self.matching_skills(message, agent_type)
        remaining = self.max_prompt_chars

        for skill, _ in matched:
            block = skill.to_prompt_block()
            if len(block) > remaining:
                block = block[:remaining].rstrip() + "\n..."
            blocks.append(block)
            remaining -= len(block)
            if remaining <= 0:
                break

        if not blocks:
            logger.debug(
                "Skills 未命中: agent=%s message=%r",
                agent_type or "all",
                (message or "")[:80],
            )
            return ""

        detail = "; ".join(
            f"{skill.name}(keywords={', '.join(keywords) if keywords else 'all'})"
            for skill, keywords in matched
        )
        logger.info(
            "Skills 已注入: agent=%s matched=%s message=%r",
            agent_type or "all",
            detail,
            (message or "")[:80],
        )

        return (
            "以下是当前请求可用的 SakikoMind Skills。"
            "请优先遵循这些业务规则；如果与系统角色冲突，以系统角色和安全边界为准。\n\n"
            + "\n\n".join(blocks)
        )

    def matching_skills(self, message: str, agent_type: Optional[str] = None) -> List[tuple[Skill, List[str]]]:
        """返回某次请求会注入的 Skills 及命中的关键词。"""
        lowered_message = (message or "").lower()
        matched: List[tuple[Skill, List[str]]] = []
        for skill in self._skills:
            if not skill.matches(message, agent_type):
                continue
            matched_keywords = [
                keyword for keyword in skill.keywords
                if keyword.lower() in lowered_message
            ]
            matched.append((skill, matched_keywords))
        return matched

    def usage_for(self, message: str, agent_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """返回某次请求实际注入的 Skill 使用记录。"""
        return [
            skill.to_usage(matched_keywords)
            for skill, matched_keywords in self.matching_skills(message, agent_type)
        ]

    def requires_escalation(self, message: str, agent_type: Optional[str] = None) -> bool:
        """根据已加载 Skill 的明确升级关键词，给高风险请求稳定设置升级标记。"""
        return any(
            skill.escalation_matches(message, agent_type)
            for skill in self._skills
        )

    def summary(self) -> Dict[str, Any]:
        """返回 Skill 管理器状态，用于 /skills 接口和排障。"""
        return {
            "root_dir": str(self.root_dir),
            "count": len(self._skills),
            "skills": [skill.to_summary() for skill in self._skills],
            "errors": self.errors,
        }

    def _log_loaded_skills(self) -> None:
        """在控制台输出醒目的 Skill 加载结果，方便启动和热加载时确认生效状态。"""
        lines = [
            "",
            "================ SakikoMind Skills Loaded ================",
            f"目录: {self.root_dir}",
            f"数量: {len(self._skills)}",
        ]

        if self._skills:
            for index, skill in enumerate(self._skills, start=1):
                agents = ", ".join(skill.agents) if skill.agents else "all"
                keywords = ", ".join(skill.keywords[:8]) if skill.keywords else "all"
                if len(skill.keywords) > 8:
                    keywords += ", ..."
                lines.extend([
                    f"{index}. {skill.name}",
                    f"   agents: {agents}",
                    f"   keywords: {keywords}",
                    f"   path: {skill.path}",
                ])
        else:
            lines.append("未加载任何 Skill。")

        if self._errors:
            lines.append("解析错误:")
            lines.extend(f"  - {error}" for error in self._errors)

        lines.append("========================================================")
        logger.info("\n".join(lines))

    def _discover_files(self, root_dir: Path) -> Iterable[Path]:
        """发现可加载文件，优先读取目录规范文件 SKILL.md。"""
        skill_md_files = sorted(root_dir.rglob("SKILL.md"))
        yielded = {path.resolve() for path in skill_md_files}
        for path in skill_md_files:
            yield path

        for path in sorted(root_dir.rglob("*")):
            resolved = path.resolve()
            if resolved in yielded or not path.is_file():
                continue
            if path.name.startswith(".") or path.name.upper() == "README.MD":
                continue
            if path.suffix.lower() in self.SUPPORTED_SUFFIXES:
                yield path

    def _load_file(self, path: Path) -> Optional[Skill]:
        if path.suffix.lower() == ".json":
            return self._load_json(path)
        return self._load_text(path)

    def _load_json(self, path: Path) -> Optional[Skill]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("JSON Skill 必须是对象格式")

        content = str(raw.get("content") or raw.get("instructions") or "").strip()
        if not content:
            raise ValueError("缺少 content 或 instructions")

        return Skill(
            name=str(raw.get("name") or path.stem),
            description=str(raw.get("description") or ""),
            content=content,
            path=str(path),
            keywords=self._as_list(raw.get("keywords")),
            agents=[item.lower() for item in self._as_list(raw.get("agents"))],
            version=str(raw.get("version") or "v1.0"),
            owner=str(raw.get("owner") or ""),
            updated_at=str(raw.get("updated_at") or ""),
            escalation_conditions=self._as_list(raw.get("escalation_conditions")),
            escalation_keywords=self._as_list(raw.get("escalation_keywords")),
            prohibited_commitments=self._as_list(raw.get("prohibited_commitments")),
            enabled=self._as_bool(raw.get("enabled"), default=True),
        )

    def _load_text(self, path: Path) -> Optional[Skill]:
        raw = path.read_text(encoding="utf-8")
        meta, body = self._split_front_matter(raw)
        body = body.strip()
        if not body:
            return None

        default_name = path.parent.name if path.name == "SKILL.md" else path.stem
        name = str(meta.get("name") or self._first_heading(body) or default_name)

        # 如果首行标题只是 Skill 名称，注入 prompt 时去掉它，减少重复噪音。
        body = self._strip_first_heading(body, name)

        return Skill(
            name=name,
            description=str(meta.get("description") or ""),
            content=body,
            path=str(path),
            keywords=self._as_list(meta.get("keywords")),
            agents=[item.lower() for item in self._as_list(meta.get("agents"))],
            version=str(meta.get("version") or "v1.0"),
            owner=str(meta.get("owner") or ""),
            updated_at=str(meta.get("updated_at") or ""),
            escalation_conditions=self._as_list(meta.get("escalation_conditions")),
            escalation_keywords=self._as_list(meta.get("escalation_keywords")),
            prohibited_commitments=self._as_list(meta.get("prohibited_commitments")),
            enabled=self._as_bool(meta.get("enabled"), default=True),
        )

    def _split_front_matter(self, raw: str) -> tuple[Dict[str, Any], str]:
        """
        解析 Markdown 顶部的简单 front matter。

        这里刻意不用 PyYAML，避免为一个轻量配置格式新增运行时依赖。
        """
        text = raw.lstrip()
        if not text.startswith("---"):
            return {}, raw

        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, raw

        meta: Dict[str, Any] = {}
        end_idx: Optional[int] = None
        for idx, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_idx = idx
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip("\"'")

        if end_idx is None:
            return {}, raw
        return meta, "\n".join(lines[end_idx + 1:])

    @staticmethod
    def _first_heading(body: str) -> Optional[str]:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or None
        return None

    @staticmethod
    def _strip_first_heading(body: str, name: str) -> str:
        lines = body.splitlines()
        if not lines:
            return body
        first = lines[0].strip()
        if first.startswith("#") and first.lstrip("#").strip() == name:
            return "\n".join(lines[1:]).strip()
        return body

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(",") if item.strip()]

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}
