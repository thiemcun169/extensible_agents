"""Tests for Skill loader and activation."""
import os, sys, pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "lib"))

from skill_loader import load_skill, should_activate, Skill


class TestSkillLoader:
    def test_load_kpi_skill(self, project_root):
        skill = load_skill(os.path.join(project_root, "skills", "kpi-report-skill"))
        assert skill.name == "kpi-report"
        assert len(skill.triggers) > 0
        assert skill.workflow
        assert "kpi_format_rules.md" in skill.references

    def test_triggers(self, project_root):
        skill = load_skill(os.path.join(project_root, "skills", "kpi-report-skill"))
        assert should_activate(skill, "KPI report for March")
        assert should_activate(skill, "Compare revenue vs last month")
        assert not should_activate(skill, "What is the weather?")

    def test_system_prompt(self, project_root):
        skill = load_skill(os.path.join(project_root, "skills", "kpi-report-skill"))
        prompt = skill.to_system_prompt()
        assert "kpi-report" in prompt
        assert "Workflow" in prompt

    def test_missing_skill(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_skill(str(tmp_path / "nonexistent"))

    def test_programmatic_skill(self):
        skill = Skill(name="test", description="Test", triggers=["test","demo"],
                      workflow="Step A", output_format="Text", examples="")
        assert should_activate(skill, "run the test")
        assert not should_activate(skill, "hello")
