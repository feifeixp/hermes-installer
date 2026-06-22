"""SKILL.md 结构校验：frontmatter + 关键工具引用 + 一致性约束。"""
from pathlib import Path
import re

SKILL = Path(__file__).parent.parent / "skills_seed" / "worldhub-worldbuilder" / "SKILL.md"


def test_skill_exists_with_frontmatter():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---")
    fm = text.split("---", 2)[1]
    assert re.search(r"^name:\s*worldhub-worldbuilder\s*$", fm, re.M)
    assert re.search(r"^description:\s*\S", fm, re.M)


def test_skill_references_tools_and_rules():
    text = SKILL.read_text(encoding="utf-8")
    for tool in ["list_worlds", "get_world", "check_consistency", "submit_world_changes"]:
        assert tool in text, f"SKILL.md 未引用工具 {tool}"
    # 一致性硬约束必须出现
    assert "冲突" in text and "复用" in text and "ent_id" in text
