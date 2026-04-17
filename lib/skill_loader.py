"""
Skill loader — reads SKILL.md and reference files, builds a system prompt
supplement that teaches an LLM agent a specific reusable workflow.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str]
    workflow: str
    output_format: str
    examples: str
    references: dict[str, str] = field(default_factory=dict)  # filename -> content

    def to_system_prompt(self) -> str:
        """Convert this skill into a system-prompt block for an LLM."""
        sections = [
            f"## Active Skill: {self.name}",
            f"**When to use:** {self.description}",
            f"**Trigger keywords:** {', '.join(self.triggers)}",
            "",
            "### Workflow",
            self.workflow,
            "",
            "### Output Format",
            self.output_format,
        ]
        if self.examples:
            sections += ["", "### Examples", self.examples]
        if self.references:
            sections += ["", "### Reference Material"]
            for fname, content in self.references.items():
                sections += [f"#### {fname}", content]
        return "\n".join(sections)


def load_skill(skill_dir: str) -> Skill:
    """Load a Skill from a directory containing SKILL.md + references/."""
    skill_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.exists(skill_path):
        raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")

    with open(skill_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse YAML-like frontmatter
    meta: dict[str, str] = {}
    body = content
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).strip().split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                meta[key.strip()] = val.strip()
        body = fm_match.group(2)

    # Parse sections from body
    sections: dict[str, str] = {}
    current = ""
    for line in body.split("\n"):
        hdr = re.match(r"^##\s+(.+)", line)
        if hdr:
            current = hdr.group(1).strip().lower()
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"

    # Clean section values
    for k in sections:
        sections[k] = sections[k].strip()

    # Parse triggers
    triggers_raw = meta.get("triggers", sections.get("triggers", ""))
    triggers = [t.strip().strip('"').strip("'")
                for t in triggers_raw.split(",") if t.strip()]

    # Load reference files
    refs: dict[str, str] = {}
    ref_dir = os.path.join(skill_dir, "references")
    if os.path.isdir(ref_dir):
        for fname in sorted(os.listdir(ref_dir)):
            fpath = os.path.join(ref_dir, fname)
            if os.path.isfile(fpath):
                with open(fpath, "r", encoding="utf-8") as f:
                    refs[fname] = f.read()

    return Skill(
        name=meta.get("name", os.path.basename(skill_dir)),
        description=meta.get("description", sections.get("description", "")),
        triggers=triggers,
        workflow=sections.get("workflow", ""),
        output_format=sections.get("output format", sections.get("output", "")),
        examples=sections.get("examples", ""),
        references=refs,
    )


def should_activate(skill: Skill, user_message: str) -> bool:
    """Check if user message triggers this skill."""
    msg_lower = user_message.lower()
    return any(t.lower() in msg_lower for t in skill.triggers)
