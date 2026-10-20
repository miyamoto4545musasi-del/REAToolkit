"""Project templates.

Each file is rendered by :func:`reatk.package.templates.render`, which only
substitutes ``@@TOKEN@@`` placeholders.  Keeping the templates out of the
Python source means Kotlin, Swift and C braces stay readable.
"""

from __future__ import annotations

import re
from pathlib import Path

TOKEN = re.compile(r"@@([A-Z0-9_]+)@@")
ROOT = Path(__file__).parent


class MissingToken(KeyError):
    """Raised when a template asks for a value the caller did not provide."""


def render(name: str, **values: str) -> str:
    """Render the template at *name* (for example ``android/EmuCore.kt``)."""
    text = (ROOT / name).read_text("utf-8")

    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise MissingToken(f"{name} needs a value for {key}")
        return values[key]

    return TOKEN.sub(substitute, text)


def tokens(name: str) -> set[str]:
    """Placeholder names used by a template, for tests that check coverage."""
    return set(TOKEN.findall((ROOT / name).read_text("utf-8")))
