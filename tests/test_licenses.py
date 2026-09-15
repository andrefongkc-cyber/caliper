"""No GPL or AGPL dependencies, ever (ADR 0006).

Checks every distribution installed in the current environment. The core CI job covers
the dev tools, the macOS job adds Qt, and the `licenses` workflow installs every extra
whenever uv.lock changes.

LGPL is allowed. A dual-licensed package passes if any alternative is allowed, e.g.
PySide6's "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only".
"""

import email.message
import importlib.metadata as metadata
import re
from typing import cast

import pytest

# Matches GPL and AGPL but not LGPL, in SPDX ids ("GPL-3.0-only") and classifier text.
COPYLEFT = re.compile(r"\bA?GPL|GNU (Affero )?General Public License", re.IGNORECASE)

# Packages whose metadata trips the check but were reviewed and are allowed.
# Map distribution name -> reason. Adding an entry needs an ADR 0006 review in the PR.
REVIEWED: dict[str, str] = {}


def is_forbidden(alternatives: list[str]) -> bool:
    """Forbidden only if every alternative is copyleft (GPL/AGPL)."""
    return bool(alternatives) and all(COPYLEFT.search(a) for a in alternatives)


def declared_alternatives(meta: metadata.PackageMetadata) -> list[str]:
    """The license options a distribution declares, most reliable source first."""
    if expression := meta.get("License-Expression"):
        return split_expression(expression)
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::") and c != "License :: OSI Approved"
    ]
    if classifiers:
        return classifiers
    # Older packages put an SPDX expression (PySide6 does) or a whole license text in the
    # free-text field. LGPL's text mentions the GPL, so only trust it when it's short.
    text = meta.get("License") or ""
    return split_expression(text) if 0 < len(text) <= 100 else []


def split_expression(expression: str) -> list[str]:
    return [part.strip("() ") for part in re.split(r"\s+OR\s+", expression.strip())]


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


def metadata_with(**headers: str) -> metadata.PackageMetadata:
    message = email.message.Message()
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    return cast(metadata.PackageMetadata, message)


@pytest.mark.parametrize(
    ("headers", "forbidden"),
    [
        # PySide6 and shiboken6 declare their dual license in the free-text field.
        ({"License": "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"}, False),
        ({"License_Expression": "MIT OR GPL-3.0-only"}, False),
        ({"License_Expression": "GPL-3.0-or-later"}, True),
        ({"Classifier": "License :: OSI Approved :: GNU General Public License v3 (GPLv3)"}, True),
        ({"License": "GNU LESSER GENERAL PUBLIC LICENSE Version 3 ... " + "x" * 200}, False),
    ],
)
def test_metadata_sources(headers: dict[str, str], forbidden: bool) -> None:
    assert is_forbidden(declared_alternatives(metadata_with(**headers))) is forbidden


def test_installed_distributions_have_no_copyleft_licenses() -> None:
    offenders = {
        dist.metadata["Name"]: alternatives
        for dist in metadata.distributions()
        if dist.metadata["Name"] not in REVIEWED
        and is_forbidden(alternatives := declared_alternatives(dist.metadata))
    }
    assert not offenders, f"GPL/AGPL dependencies are banned (ADR 0006): {offenders}"
