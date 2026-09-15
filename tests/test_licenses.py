"""No GPL or AGPL dependencies, ever (ADR 0006).

Checks every distribution installed in the current environment. The core CI job covers
the dev tools, the macOS job adds Qt, and the `licenses` workflow installs every extra
whenever uv.lock changes.

LGPL is allowed. A dual-licensed package passes if any alternative is allowed, e.g.
PySide6's "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only".
"""

import importlib.metadata as metadata
import re

import pytest

# Matches GPL and AGPL but not LGPL, in SPDX ids ("GPL-3.0-only") and classifier text.
COPYLEFT = re.compile(r"\bA?GPL|GNU (Affero )?General Public License", re.IGNORECASE)

# Packages whose metadata trips the check but were reviewed and are allowed.
# Map distribution name -> reason. Adding an entry needs an ADR 0006 review in the PR.
REVIEWED: dict[str, str] = {}


def is_forbidden(alternatives: list[str]) -> bool:
    """Forbidden only if every alternative is copyleft (GPL/AGPL)."""
    return bool(alternatives) and all(COPYLEFT.search(a) for a in alternatives)


def declared_alternatives(dist: metadata.Distribution) -> list[str]:
    """The license options a distribution declares, most reliable source first."""
    meta = dist.metadata
    if expression := meta.get("License-Expression"):
        return [part.strip("() ") for part in re.split(r"\s+OR\s+", expression)]
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::") and c != "License :: OSI Approved"
    ]
    if classifiers:
        return classifiers
    # The free-text field sometimes holds a whole license text (and LGPL's text mentions
    # the GPL), so only trust it when it's short.
    text = meta.get("License") or ""
    return [text] if 0 < len(text) <= 100 else []


@pytest.mark.parametrize(
    ("alternatives", "forbidden"),
    [
        (["MIT"], False),
        (["LGPL-2.1-or-later"], False),
        (["LGPL-3.0-only", "GPL-2.0-only", "GPL-3.0-only"], False),
        (["GNU Lesser General Public License v3 (LGPLv3)"], False),
        (["GPL-3.0-or-later"], True),
        (["AGPL-3.0-only"], True),
        (["GNU General Public License v3 (GPLv3)"], True),
        (["GPLv2"], True),
        ([], False),
    ],
)
def test_classification(alternatives: list[str], forbidden: bool) -> None:
    assert is_forbidden(alternatives) is forbidden


def test_installed_distributions_have_no_copyleft_licenses() -> None:
    offenders = {
        dist.metadata["Name"]: alternatives
        for dist in metadata.distributions()
        if dist.metadata["Name"] not in REVIEWED
        and is_forbidden(alternatives := declared_alternatives(dist))
    }
    assert not offenders, f"GPL/AGPL dependencies are banned (ADR 0006): {offenders}"
