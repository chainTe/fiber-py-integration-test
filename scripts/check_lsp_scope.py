#!/usr/bin/env python3
"""Validate all LSP cases while reporting unchanged, proven baseline orphans.

The normal mapping/design commands still return failure for global orphan IDs.
This scoped gate does not hide them or relax checks for new or edited files.
"""

import hashlib
import json
from pathlib import Path

from _lsp_check_loader import load_checker


def main():
    root = Path(__file__).resolve().parents[1]
    review = ["reviews/lsp/hosted-lsp.md"]
    mapping_module = load_checker("check_test_map")
    design_module = load_checker("check_test_design")
    mapping = mapping_module.build_report(root, review)
    design = design_module.check_design(root, review)
    baseline = json.loads(
        (root / "reports/hosted-lsp/baseline-mapping-issues.json").read_text()
    )
    baseline_ids = set()
    errors = []
    for name, evidence in baseline["files"].items():
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if actual != evidence["sha256"]:
            errors.append(f"Baseline source changed; orphan allowance invalid: {name}")
        baseline_ids.update(evidence["orphan_ids"])
    actual_orphans = set(mapping["orphan_mappings"])
    if actual_orphans != baseline_ids:
        errors.append(
            f"Global orphan set differs from proven baseline: {sorted(actual_orphans)}"
        )
    for key in (
        "duplicate_review_ids",
        "missing_automation_markers",
        "automation_marker_mismatches",
        "unautomated",
    ):
        if mapping[key]:
            errors.append(f"{key}: {mapping[key]}")
    # Exempt only the exact already-proved global orphan diagnostic. All other
    # upstream design checks remain fatal, including new/dangling Case or Spec IDs.
    baseline_error = f"orphan_mappings: {mapping['orphan_mappings']}"
    errors.extend(error for error in design["errors"] if error != baseline_error)
    report = {
        "scope": "hosted-lsp",
        "base_commit": baseline["base_commit"],
        "baseline_orphans_still_visible": sorted(actual_orphans),
        "mapping_count": mapping["automated_case_count"],
        "review_case_count": mapping["review_case_count"],
        "semantic_review_and_execution": "separate evidence; mapping is not a pass",
        "errors": errors,
    }
    print(json.dumps(report, indent=2))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
