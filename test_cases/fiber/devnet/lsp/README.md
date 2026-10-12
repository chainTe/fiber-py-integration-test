# Hosted LSP process integration

This suite targets exactly `chenyukang/fiber@8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8`
(`lsp-support`). It starts real CKB, FNN and independent production-SDK processes.
It never uses the old packaged chain snapshot and never connects to testnet or
mainnet. Every pytest test owns a fresh genesis, data directory, loopback ports
and process handles; teardown stops only those handles.

## Scope and review

The complete sourced behavior design is [reviews/lsp/hosted-lsp.md](../../../../reviews/lsp/hosted-lsp.md).
It distinguishes the internal-signer routing baseline from external-SDK signing,
and records explicit limits. `TEST-MAP` comments identify corresponding behaviors;
a checked review box means a mapping exists, **not that the test passed**.
Independent design findings and responses, source-review limits and actual
execution are indexed in [the evidence reports](../../../../reports/hosted-lsp/README.md).

Important oracles include:

- RootSigner registration, one-time nonce replacement/consumption, credential
  refresh, restart and authority-only Biscuit tenant routing
- Tenant lifecycle/capacity, private external funding and exact opening retries
- Both payer and receiver completion, actual TLC clearance and channel balances
- Cold tenant delivery with non-activating operator observations, deadlines,
  per-tenant/global quotas and durable restart
- Actual two-channel outgoing MPP, distinct hosted incoming-MPP rejection
- Production SDK intent/mandatory validation, signer persistence, signature replay
- Cooperative/force closure, checked Watchtower settlement/TLC keys and revocation

The desired external-signer liveness regression tied to
[upstream issue 1695](https://github.com/nervosnetwork/fiber/issues/1695)
never gives the receiver wallet's secret to the payer. `receiver Paid` with
`payer Inflight` is a failure. A failed hosted invoice creation leaving a stored
invoice is also a real atomicity failure. These are not masked with `xfail`,
skips, direct database writes or product patches.

## Prerequisites

- Linux, Python 3.12+, Rust **1.95.0**, C/C++ toolchain, libclang, CMake, OpenSSL
- Official CKB **0.202.0** and its bundled ckb-cli (CLI version **1.15.0**)
- The fixed product source as a sibling directory named `fiber-lsp`
- Sufficient space/time for a debug Rust/RocksDB build; no Docker, Bitcoin or LND

```sh
# From the parent of this integration repository:
git clone https://github.com/chenyukang/fiber.git fiber-lsp
git -C fiber-lsp checkout 8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8
cd fiber-py-integration-test
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lsp.txt
export CARGO_TARGET_DIR="$(pwd)/../target"
bash scripts/build_lsp.sh
export LSP_PRODUCT_DIR="$(pwd)/../fiber-lsp"
export LSP_FNN_BIN="$CARGO_TARGET_DIR/debug/fnn"
export LSP_SDK_DRIVER_BIN="$CARGO_TARGET_DIR/debug/fiber-lsp-integration-sdk-driver"
export LSP_CKB_BIN=/absolute/path/to/ckb
export LSP_CKB_CLI_BIN=/absolute/path/to/ckb-cli
.venv/bin/python -m pytest -o addopts= --collect-only test_cases/fiber/devnet/lsp
.venv/bin/python -m pytest -o addopts= -vv --tb=short \
  --junitxml=reports/hosted-lsp/junit.xml test_cases/fiber/devnet/lsp
```

`tools/lsp-sdk-driver/build.sh` verifies the exact product revision and rejects
modified SDK sources. Its JSONL API is documented in that directory. The driver
uses real HTTP RPC and production SDK/chain validation. Local mutation seams
alter a copied response **before** SDK validation; they do not replace the
server or independently approved wallet terms.

Run the final suite serially and avoid a concurrent native build on the same worker. The pinned product has a 10 ms Biscuit authorizer budget; parallel debugging produced explicit authorization timeouts. Such a timeout is a hard test failure, including inside negative-rejection assertions, and is never retried or accepted as the intended denial.

Missing runtimes are fixture errors rather than silent skips. Collection alone
is not integration execution. Do not reuse a wallet store concurrently across
signer processes. Never upload its root key, SDK store or bearer-token status.

## Review and mapping checks

Use the same pinned ai-test-agent that produced the review:

```sh
git clone https://github.com/nervosnetwork/ai-test-agent.git ../ai-test-agent
git -C ../ai-test-agent checkout 6768441f75ced58132d32f0d0914638b403b4edd
python3 scripts/check_test_design.py --root . \
  --review reviews/lsp/hosted-lsp.md --json
python3 scripts/check_test_map.py --root . \
  --review reviews/lsp/hosted-lsp.md --require-complete --json
```

The dedicated `Hosted LSP integration` workflow builds fixed sources and runs
this suite. It uploads JUnit and pytest output only, never devnet wallet stores.
The existing broad Fiber Makefile list is unchanged; `make lsp_test` is the
explicit new target so older FNN builds are not accidentally used for LSP cases.

The build entry point optimizes only the unchanged `scrypt`, `salsa20` and `biscuit-auth` dependency packages, preserving the product dev profile, debug assertions, dev RPCs, recommended KDF parameters and exact 10 ms authorization budget. See [runtime evidence](../../../../reports/hosted-lsp/runtime-validation.md) for the cold-build repair and measured startup issue.

## Timing boundaries

Delivery tests observe an actual Deferred execution before asserting a deadline.
The TLC-safety variant checks the exact computed cutoff, then safely resumes;
it does not claim to have waited through a multi-hour budget. Randomly restarting
an observed pending transition is not proof of an exact crash between a durable
write and its continuation. Plain-CKB SDK chain flows do not imply UDT settlement
coverage. See the review's pending/unanalysed branches for the remaining limits.

## Existing repository mapping defects

At immutable integration base `4be658da51967421be9d58543fe0d5c2888e324b`,
PMP-01 through PMP-05 and SETTLE-01 already have TEST-MAP comments without
corresponding reviews. Global checker commands above continue reporting them
and exit nonzero. `python scripts/check_lsp_scope.py` is the dedicated CI gate:
it verifies those exact unchanged source SHA256 values, reports the baseline
orphans, and fails on any new orphan, missing mapping, checkbox, duplicate or
Spec/tree defect. This does not claim the whole repository has clean mapping.
The pinned generic framework also needs the thin loader's `test_cases/` discovery
adaptation; no semantic or acceptance rule is weakened.
