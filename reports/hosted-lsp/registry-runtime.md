# Registry and authority runtime debugging

These are initial real-process debugging runs, **not the final frozen-suite result**.
The exact binary/source pins are in [runtime-validation.md](runtime-validation.md).

## Commands and observed results

With documented `LSP_*` binary paths, from the repository root:

```sh
python -m pytest -o addopts= -x -vv --tb=short \
  --junitxml=reports/hosted-lsp/registry-first.xml \
  test_cases/fiber/devnet/lsp/test_registry.py
```

Result: **6 passed, 1 failed, exit 1**, 189.13s. Stop-on-first-failure prevented the
remaining seven tests from running in this invocation. The failed refresh test
had an operator `lsp_ensure_tenant` response:

```text
code: -32999
message: Unauthorized: Biscuit authorization timed out
```

The product source sets a 10 ms authorizer limit in
`crates/fiber-lib/src/rpc/biscuit.rs`. Several independent test modules were
running at once. Scheduling/debug contention is a possible explanation, not a
confirmed root cause, and the error is retained as a failed run.

The remaining group, including that same refresh test, then ran with a unique
persistent temporary directory:

```sh
python -m pytest -o addopts= -vv --tb=short \
  --basetemp=../runtime/test-runs/registry-remaining-20261012-1600 \
  --junitxml=reports/hosted-lsp/registry-remaining.xml \
  test_cases/fiber/devnet/lsp/test_registry.py \
  -k 'not authenticated_registration_and_tenant_activation and not registry_latest_nonce_single_use and not bad_registry_proof_preserves_live_nonce'
```

Result: **8 passed, 6 deselected, exit 0**, 231.65s. All **14 distinct registry
selectors** therefore have an observed passing execution across the two runs;
that does not turn the initial timeout into a pass or establish a clean full run.

## Test hardening after the observation

- Negative registry tests now require `RpcError`, so programming and transport
  exceptions cannot satisfy intended rejections.
- The exact authorization-timeout response is a fail-fast pytest failure in the
  shared RPC and SDK wrappers. It bypasses broad expected-exception contexts and
  polling, without retrying. Ordinary authorization denials remain normal RPC
  errors.
- A helper-only check verified that distinction, plus Black and Python compile
  checks. It is not integration coverage.
- Final verification must restart pytest to load these changes and execute the
  entire suite serially. Its result is reported separately.

Raw local logs and JUnit are retained outside the committed report set. Older
first-run process logs were pruned by pytest's default temporary-directory
retention; later runs use dedicated temporary directories. No wallet stores,
private keys or bearer tokens are published.

## Final namespace-write oracle revision

Independent semantic review identified that checking only channel state after a
rejected cross-tenant `update_channel(enabled=False)` could miss a mutated enabled
flag. Both owners now have an enabled-true precondition and exact preservation
checks for funding identity, enabled/state, balances/reservations, pending TLCs,
routing fee/expiry terms and shutdown hash. The focused real-process selector
`test_tenant_and_public_namespace_isolation` passed in **29.57s, exit 0** on final
binaries and fixture. Command: `.venv/bin/python -m pytest -o addopts= -vv --tb=short
--basetemp=../runtime/test-runs/registry-namespace-final
--junitxml=reports/hosted-lsp/registry-namespace-final.xml
test_cases/fiber/devnet/lsp/test_registry.py::test_tenant_and_public_namespace_isolation`.
An initial full run was interrupted before any selector completed to include
this correction; it is not counted as a passing or completed run.
