import os
from pathlib import Path

def fix_annotations():
    base_dir = Path("webui")
    for py_file in base_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if "from __future__ import annotations" not in content:
            new_content = "from __future__ import annotations\n" + content
            py_file.write_text(new_content, encoding="utf-8")
            print(f"Fixed {py_file}")

if __name__ == "__main__":
    fix_annotations()
