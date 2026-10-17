"""The Terraform baseline mirrors limits.py; keep the copies in sync."""

import re
from pathlib import Path

from loadtest import limits

ROOT = Path(__file__).resolve().parents[3]
LOCAL_KIND = ROOT / "infra" / "envs" / "local-kind" / "main.tf"
MODULE_VARS = ROOT / "infra" / "modules" / "k8s-platform-baseline" / "variables.tf"


def _allowed_prefixes() -> list[str]:
    block = re.search(r"allowed_image_prefixes\s*=\s*\[(.*?)\]", LOCAL_KIND.read_text(),
                      re.DOTALL)
    assert block, "allowed_image_prefixes not found in local-kind main.tf"
    return re.findall(r'"([^"]+)"', block.group(1))


def test_local_kind_image_prefixes_match_limits():
    expected = [f"{r}/" for r in limits.ALLOWED_REGISTRIES] + list(limits.PLATFORM_IMAGES.values())
    assert sorted(_allowed_prefixes()) == sorted(expected)


def test_module_managed_label_default_matches_limits():
    m = re.search(r'variable "managed_label" \{[^}]*?default\s*=\s*"([^"]+)"', MODULE_VARS.read_text())
    assert m and m.group(1) == limits.MANAGED_LABEL
