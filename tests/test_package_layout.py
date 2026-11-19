"""Package code imports its modules as a package, never by editing sys.path."""
from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "family_bot"


def test_package_does_not_edit_sys_path() -> None:
    offenders = [str(p.relative_to(PACKAGE)) for p in PACKAGE.rglob("*.py")
                 if "sys.path" in p.read_text(encoding="utf-8")]
    assert offenders == []
