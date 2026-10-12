# Production-SDK adapter validation

Final-source evidence rerun: 2026-10-12, 16:15–16:17 UTC.

## Result and scope

All seven recorded checks exited **0**. The Rust adapter's **eight unit tests passed**, with **zero failed and zero ignored**. The JSONL smoke check passed **seven commands across two actual process launches**.

These results validate adapter compilation, linting, selected local SDK/store behavior, and the subprocess protocol. **They do not establish any LSP case as passed and are not real-LSP integration execution.** No FNN or CKB server was started by these checks. Tests against real nodes are reported separately.

The adapter imports the production SDK and exported test Agent from product commit `8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8`. Its build guard verified the commit and absence of tracked product modifications. Dependency inspection shows only SDK `default` and `json` features; `test-apis` is not enabled.

## Reproduction

The logs below preserve the exact commands used in the prepared local workspace. That workspace sourced `../runtime/env.sh`, a local toolchain-path setup file, which is not distributed in this PR. For a fresh checkout, install the prerequisites in the [suite README](../../test_cases/fiber/devnet/lsp/README.md), then run the portable equivalent below from the integration-test repository root. Its sibling `fiber-lsp` must be at the fixed product commit. Rust 1.95.0 and its rustfmt/clippy components are required. The recorded offline commands require the existing Cargo dependency cache.

```bash
export CARGO_BUILD_JOBS=2
export CARGO_TARGET_DIR="$(pwd)/../adapter-target"
cd tools/lsp-sdk-driver

cargo +1.95.0 fmt --manifest-path Cargo.toml -- --check
cargo +1.95.0 clippy --locked --offline --all-targets --no-deps --manifest-path Cargo.toml -- -D warnings
cargo +1.95.0 test --locked --offline --manifest-path Cargo.toml
bash build.sh --offline
python3 check_jsonl.py --binary "$CARGO_TARGET_DIR/debug/fiber-lsp-integration-sdk-driver"
../../.venv/bin/python -m black --check check_jsonl.py
cargo +1.95.0 tree --locked --offline --manifest-path Cargo.toml -e features -i fiber-lsp-sdk
```

`build.sh` explicitly selects Rust 1.95.0 and checks the pinned product revision and clean tracked source before `cargo build --locked`.

## Durable evidence

Each log includes its command, UTC start time, output, and exit code. Only the common absolute workspace prefix was replaced with `<workspace>`; test results were not altered. No generated wallet keys, bearer tokens, or private machine paths are included.

| Check | Exit | Log |
| --- | ---: | --- |
| Rust formatting | 0 | [fmt.log](adapter-validation/fmt.log) |
| Clippy, all targets, warnings denied | 0 | [clippy.log](adapter-validation/clippy.log) |
| Eight adapter unit tests | 0 | [unit.log](adapter-validation/unit.log) |
| Pinned, locked executable build | 0 | [build.log](adapter-validation/build.log) |
| JSONL subprocess restart smoke | 0 | [jsonl.log](adapter-validation/jsonl.log) |
| Python smoke-script formatting | 0 | [python-format.log](adapter-validation/python-format.log) |
| Production SDK feature selection | 0 | [sdk-features.log](adapter-validation/sdk-features.log) |

Machine-readable command/results record: [summary.json](adapter-validation/summary.json).

The eight unit checks cover:

1. A caller-supplied payer preimage is refused before RPC or wallet creation.
2. SDK registration proof generation performs no RPC and leaves the local snapshot unchanged.
3. Root identity and pending opening material survive reopening the complete local store.
4. A valid Biscuit attenuation block preserves the original authority block and cryptographically verifies; the adapter receives no issuer signing key.
5. Valid legacy and full-hash commitment descendants preserve their format; the adapter's unsupported 58-byte/feature-zero form is rejected directly for both parent and child.
6. Malformed lengths, unknown or changed features, mixed formats, and unsupported commitment versions fail closed.
7. Changed commitment prefix, contract identity/hash type, or output type fail closed.
8. Matching a signed commitment template requires exact independently known funding dependencies and unchanged transaction content.

The separate [JSONL smoke script](../../tools/lsp-sdk-driver/check_jsonl.py) checks one response per command, response IDs, unchanged identity and store fingerprints across actual process restarts, refusal of an injected payer preimage, and absence of recorded RPC calls. Temporary wallets are deleted and their contents are never printed.

## Executable and limits

Executable relative to the repository root:

`../runtime/adapter-target/debug/fiber-lsp-integration-sdk-driver`

SHA256:

`ccc1e1504bb36f11a0a5f1595a9882b05389cbe066550d72a9ae92e880fccdf1`

Control contract and supported operations: [adapter README](../../tools/lsp-sdk-driver/README.md). The chain adapter supports plain CKB fixtures. Prepared approvals are intentionally process-local and must be freshly prepared after a signer restart; the complete persistent wallet store is retained. No snapshot rollback or receiver-to-payer secret injection is supplied.


## Observed upstream test-Agent compatibility limitation

A separate real-chain execution of the watchtower tests observed the unchanged exported Agent rejecting a valid full-hash descendant with `not a commitment descendant`. The initial commitment was `0xbd76503511d0b9bf7d95e844760e3543824547286a123e1745b638f4712f0d11`, committed at block `0x1b`; the descendant was `0x0f803bacaaf93b07ff5c0af72028af41d080b78acfd8a719c60a5b2dcbd70316`, committed at block `0x69`. Both had 58-byte commitment arguments. The run had already obtained a genuine first Settlement receipt. Full local diagnostic chain evidence remains in the ignored `local-runtime-evidence/watchtower-attempt2/lineage-chain-evidence.json`; it is not an adapter-unit-test result or a claim that the full LSP scenario passed.

At the pinned product revision, `tests/fiber-lsp-sdk-agent/src/chain.rs` lines 171–181 require exactly 57 bytes in descendant ancestry. The adapter now supplies `src/chain.rs` at the public `ChainVerifier` boundary, preserving DevChain's live-cell, maturity and median-time verification. Its lineage traversal checks actual canonical committed transaction hashes, exact input/output zero, trusted local commitment-contract identity, preserved type/prefix/features, supported versions, known funding dependencies, and a 128-step bound. Watchtower signing still uses the exported Agent's `poll_watchtower_verified`, including its genuine accepted-submission receipt persistence, and the unchanged production SDK's verified prepare/confirm checks. Channel signing remains production SDK Manual-policy handling.

The local provider supports the fixture forms emitted by the pinned node builder: 57-byte legacy or 58-byte full-hash feature one. Although the pinned SDK parser also accepts 58 bytes with feature zero, this provider rejects that form as outside its supported fixture scope. That limitation does not establish an SDK or bundled-contract rejection. The contract binaries' checked-in source-revision note is stale, so newer external contract source is not treated as proof of bundled-binary semantics.

The upstream Agent's default lineage limitation is preserved as observed evidence; the adapter does not modify or claim to fix it. Actual successful chain execution after introducing this independent provider must be reported separately by the integration runner.
