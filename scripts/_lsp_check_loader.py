"""Load the pinned ai-test-agent with this repository's existing test layout.

The upstream generic scanner recognizes tests/case/targets/benchmarks, whereas
this repository deliberately retains test_cases/. Extend only directory discovery;
all duplicate/orphan/checkbox/Spec validation is the original upstream logic.
"""

import importlib
import os
from pathlib import Path
import subprocess
import sys

FRAMEWORK_SHA = "6768441f75ced58132d32f0d0914638b403b4edd"


def load_checker(name):
    root = Path(__file__).resolve().parents[1]
    framework = Path(os.environ.get("AI_TEST_AGENT_DIR", root.parent / "ai-test-agent"))
    actual = subprocess.check_output(
        ["git", "-C", str(framework), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != FRAMEWORK_SHA:
        raise RuntimeError(f"Expected ai-test-agent {FRAMEWORK_SHA}, found {actual}")
    subprocess.run(
        ["git", "-C", str(framework), "diff", "--quiet", "HEAD", "--", "scripts"],
        check=True,
    )
    sys.path.insert(0, str(framework / "scripts"))
    mapping = importlib.import_module("check_test_map")
    mapping.CODE_DIR_NAMES.add("test_cases")
    mapping.PROJECT_ROOT = root
    if name == "check_test_map":
        return mapping
    return importlib.import_module(name)
