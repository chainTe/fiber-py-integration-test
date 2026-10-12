#!/usr/bin/env python3
"""Run pinned ai-test-agent design validation on existing test_cases/."""

from _lsp_check_loader import load_checker

if __name__ == "__main__":
    raise SystemExit(load_checker("check_test_design").main())
