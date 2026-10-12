# Pinned devnet runtime validation

## Build and provenance

- Product source and Cargo.lock: unchanged `chenyukang/fiber@8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8`.
- Rust/Cargo 1.95.0; Python 3.12.14; official Nervos CKB 0.202.0 (`d0a6c95`), bundled ckb-cli 1.15.0 (`8c892a5`).
- CKB [official portable release](https://github.com/nervosnetwork/ckb/releases/download/v0.202.0/ckb_v0.202.0_x86_64-unknown-linux-gnu-portable.tar.gz) archive SHA256: `022b13461d081f872ec2665510861d88ecc3f5c11d0aae5a75d646251add23ec`. This is the observed downloaded asset hash, now checked by CI; it is not an independently verified vendor signature.
- Initial build exited 101 because this environment's libclang installation lacked builtin `stdbool.h`. Official Debian `libclang-common-19-dev` 19.1.7-3+b1 headers were unpacked into the isolated workspace, with a process-local `BINDGEN_EXTRA_CLANG_ARGS` include path. The repaired locked debug build passed, exit 0 (70m51s including a wait for previously started OpenSSL compilation). CI installs complete `clang` and `libclang-dev` packages.
- The first real fixture startup exposed excessively slow debug KDF work, before RPC became available. The unchanged `scrypt` and `salsa20` dependencies were optimized. That targeted locked rebuild passed, exit 0 in 9m38s; native RocksDB stayed cached.
- A later exclusive payment run still encountered the unchanged 10 ms Biscuit authorization deadline. A second targeted rebuild added only `biscuit-auth` optimization, passed exit 0 in 10m44s, and kept the exact deadline, authentication policy and native cache. This final FNN is used for final verification; earlier smoke outcomes below used the KDF-only binary.

Portable build entry point: `bash scripts/build_lsp.sh`, after installing the [documented prerequisites](../../test_cases/fiber/devnet/lsp/README.md). Its product command is:

```sh
cargo build --locked --manifest-path ../fiber-lsp/Cargo.toml \
  -p fiber-bin -p fiber-lsp-sdk-agent \
  --config 'profile.dev.package.scrypt.opt-level=3' \
  --config 'profile.dev.package.salsa20.opt-level=3' \
  --config 'profile.dev.package.biscuit-auth.opt-level=3'
```

Debug assertions and dev RPCs remain enabled. No KDF cost, protocol behavior, source file, database or genesis snapshot is patched. A focused public-input probe retained recommended `log_n=17`, `r=8`, `p=1` and produced identical derived bytes with original and optimized dependencies. Timings were 57.011s original versus 9.793s optimized while compilation was active; the optimized repeat took 2.688s after compilation. These timings are environment-dependent and are not integration results.

A [source-based authorization probe](runtime-evidence/auth_probe.rs), linked to the optimized dependency, passed 100 fresh normal authorizations with the unchanged 10 ms limit (maximum measured authorize call: 28 microseconds). Authority tenant lookup, unrelated/write-only permission denial, failed attenuation, appended-privilege denial, wrong-root signature rejection and malformed-token rejection also passed. Run-limit errors were fatal rather than accepted as denials. These are **helper-only** checks, not real FNN integration outcomes. [Exact local command](runtime-evidence/auth-probe-command.txt) and [result](runtime-evidence/auth-probe-optimized.log) are retained; the artifact fingerprint in that command is build-specific.

The KDF [probe source](runtime-evidence/kdf_probe.rs), [initial comparison](runtime-evidence/kdf-probe-comparison.log) and [post-build repeat](runtime-evidence/kdf-probe-optimized-after-build.log) use public synthetic input only. No live credentials or wallet secrets are present.

## Final executables for integration

| Binary | SHA256 |
| --- | --- |
| FNN | `9ad5294cc001cef68fea4e09276fe40057c623a2acb014e144ada5f5c66498fe` |
| Product SDK agent | `b14a89495117607983e081ce9ec6432e33104cd5867b7cced8e553f7162582cd` |
| CKB | `d85d74034231a4f68ea57e5b3fbe99e66736e08e108d7afeb32c0503d527e0b6` |
| ckb-cli | `1f359467de3f208808530f47d03c09983c911ab041a3ae31210aef6aacca00aa` |

`fnn --version` returned `fnn Fiber v0.10.0-rc1 (8db6a11 2026-10-09)`; FNN and SDK-agent `--help` passed. The integration driver has its own [validation evidence](adapter-validation.md).

## Real fixture smoke

On 2026-10-12, the final fixture smoke command was:

```sh
python -m pytest -o addopts= -vv --tb=short \
  test_cases/fiber/devnet/lsp/test_payments.py::test_online_inbound_settles_both_ends_once
```

Result: **1 passed, 40.70s, exit 0**. This exercised actual CKB/FNN processes, RootSigner registration, both-end ChannelReady, payer Success, receiver Paid, hosted delivery success, preimage/fee/endpoint balances and TLC drainage. Fixture-only corrections before this result were operator-authenticated registration, waiting for canonical block verification after mining, and bounded handling of the pre-mutation peer Init readiness race. The complete final test run is recorded separately; this smoke is not a full-suite claim.

## Canonical mining helper validation

A real CKB-only probe after the initial watchtower batch passed in 10.659 seconds
(exit 0). It submitted 300 exact templates, checked each canonical block's hash,
height and parent, and observed five stale tx-pool templates. The fixture waited
for each stale template to match the canonical parent before submitting it once;
it did not create extra sibling blocks or retry a rejected transaction. Genuine
`generate_epochs(2)` advanced canonical maturity from epoch 27.6 to 29.6; the
previous block was still below the target. Final height was 320.

[Probe summary](runtime-evidence/mining-template-summary.json). This is helper
validation, not mapped LSP coverage. The original uncles and residual authorization
timeout are retained as historical failed attempts; the final serial suite determines
the corrected fixture's integration result. Optimization did not remove every
observed 10 ms authorization timeout, and no timeout was accepted as a permission denial.
