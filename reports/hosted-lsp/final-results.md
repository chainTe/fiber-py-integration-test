# Hosted LSP final local results

**69 passed, 3 failed, 0 skipped, 0 errors; 72 selectors; exit 1; 1995.76 seconds.**
This is a complete serial run of the frozen suite, not an all-green result.

| Area | Passed | Failed |
| --- | ---: | ---: |
| Registration, authentication and tenant isolation | 14 | 0 |
| Payments, delivery, quotas and deadlines | 18 | 1 |
| External funding, SDK signing and recovery | 28 | 1 |
| Close and Watchtower chain execution | 9 | 1 |

The 51 reviewed behavior cases map through 60 bindings to 50 functions and 72
concrete selectors. Counts reflect shared bindings and parametrization, not 72
independent case IDs. [Source review](b-coverage-review.md) and actual execution
are separate evidence.

## Exact target and reproduction

- Product: unchanged `chenyukang/fiber@8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8` (`lsp-support`).
- Integration base: `4be658da51967421be9d58543fe0d5c2888e324b`.
- ai-test-agent: `6768441f75ced58132d32f0d0914638b403b4edd`.
- Frozen source fingerprint: `b5e62d74770939ad5a75644e58508160ca1ad4992326444579221a9b4c3de724`; all 25 source/build/design hashes matched after execution.
- Shell UTC provenance: 2026-10-12 17:10:39 to 17:44:03. Pytest's duration excludes shell setup. JUnit's naive timestamp is runner-local time, not the UTC provenance.
- Real CKB 0.202.0, FNN and production-SDK processes; fresh isolated devnet for each selector. No concurrent build or other node suite during this run.

After the documented [build and environment setup](../../test_cases/fiber/devnet/lsp/README.md), from the integration repository:

```sh
.venv/bin/python -m pytest -o addopts= -vv --tb=short \
  --basetemp=../runtime/test-runs/final-serial-v2 \
  --junitxml=reports/hosted-lsp/final-serial.xml \
  test_cases/fiber/devnet/lsp
```

The environment supplied `LSP_FNN_BIN`, `LSP_SDK_DRIVER_BIN`, `LSP_PRODUCT_DIR`,
`LSP_CKB_BIN` and `LSP_CKB_CLI_BIN`. Exact binary hashes are in
[runtime validation](runtime-validation.md) and [adapter validation](adapter-validation.md).
[Full final pytest output](runtime-evidence/final-pytest.log),
[original final JUnit](runtime-evidence/final-junit.xml) and
[machine-readable summary with artifact hashes](final-results.json) are retained.
Both complete output files were inspected before publication; they contain no
wallet stores, private keys, bearer tokens or payment preimages.

## Failed invariants and attribution

### LSP-033: external payer cannot obtain the fulfillment needed to finish

`test_external_hosted_payer_obtains_fulfillment_only_from_own_rpc` failed its
90-second protocol-completion bound. Direct paired observations show:

- Receiver invoice **Paid**, receiver preimage release completed.
- Payer **Inflight**, payer RPC preimage absent, fulfillment not authorized from its RPC.
- Payer requires `CompleteReceivedCommitment`; receiver requires no signature.
- Production SDK rejects TLC removal without a locally authorized resolution.

The [complete secret-free diagnosis](runtime-evidence/final-lsp-033.json) was
saved before pytest could truncate the assertion. This reproduces the paired
state in [upstream issue 1695](https://github.com/nervosnetwork/fiber/issues/1695).
The pinned source exposes preimages only from successful attempts
(`fiber-lib/src/fiber/payment.rs:635–643,1937–1948`), while SDK commitment removal
requires fulfillment authorization (`fiber-lsp-sdk/src/commitment.rs:284–299,504–523`).
No receiver preimage was passed to the payer and no product code was modified.

### LSP-034: rejected invoice creation leaves a stored invoice

`test_rejected_missing_expiry_invoice_leaves_no_partial_write` first received the
intended finite-expiry rejection, then read the same invoice as **Open**, with
`expiry_time=absent`. The strict no-partial-write assertion failed. The same
hash's successful retry is intentionally not claimed because execution stops at
the residue. Source writes the invoice before hosted finite-expiry registration
validation (`fiber-lib/src/rpc/invoice.rs:196,409`; `fiber-lib/src/lsp/invoice.rs:193–195`).

### LSP-051 fee variant: rejected mutation, then settlement recovery stalled

`test_sdk_rejects_hostile_watchtower_transaction_before_signing[fee]` passed its
malicious-request rejection, no-signature/submission and unchanged-wallet-state
assertions. It then timed out after 90 seconds waiting for legitimate accepted
signatures and confirmed settlement. The guarded stale-input path independently
observed a confirmed spend; the SDK refused the stale input without changing its
store or sending a signature. Offline canonical-chain and read-only persisted-state inspection found two
pending IDs: the lexicographically first request spends an already spent input,
while the second spends an unspent confirmed descendant. RPC exposes only
`first_pending()` and leaves the stale request ahead of the live successor;
actor scans continued through canonical height 388. This establishes the observed
queue starvation and its source path, not deterministic occurrence in every run.

One separate unchanged fee-selector comparison **passed in 36.13s, exit 0**.
The occurrence depends on timing/request ordering. It does not replace the
primary 69/3 result or erase the original failure. See the
[Watchtower diagnosis](watchtower-liveness.md) and
[chain/request evidence](watchtower-liveness-evidence.json). No funds-loss claim
is made. The original failure is not counted as successful recovery or as an
accepted malicious fee.

## Other verification

- Whole-repository Black: 385 files unchanged; exit 0.
- Changed Python syntax compilation, whitespace checks and 72-selector collection: exit 0.
- Production-SDK adapter: 8 Rust unit tests, rustfmt, Clippy with `-D warnings`, locked build and seven JSONL commands across two launches: all exit 0. These are separate from integration counts.
- All 51 selected cases mapped; scoped design/mapping gate exits 0.
- Original global mapping/design commands still exit 1 for the six byte-proven unchanged baseline orphan IDs, PMP-01–05 and SETTLE-01. They are visible in the reports and are not fixed or hidden by this change.
- Product tracked source and Cargo.lock remained clean. No skip, xfail, expected-failure conversion, secret bypass or timeout extension was introduced.

## Acceptance and limits

The evidence disposition is **needs_decision**, not product acceptance. The suite
contains real failed invariants, reviewer isolation is not host-attested, and
native pytest results do not create a controlled-framework per-case acceptance
receipt. Opening this test PR is not a merge or release approval.

Earlier failures and repairs remain in the [evidence index](README.md). In
particular, the fixed CKB template race and observed 10 ms authorization-timeout
instability are not erased by later passes. This final batch had no authorization-
timeout failure, but one run does not establish stable repeatability.

Claims are limited to the selected plain-CKB devnet behaviors. UDT settlement,
production/testnet, exact internal durable-write crash windows, elapsed multi-hour
TLC-safety deadlines, exhaustive fuzzing and unrelated product changes remain
outside this result. The local strict chain provider's upstream test-Agent
compatibility adaptation is documented separately; it is not a Fiber product fix.
