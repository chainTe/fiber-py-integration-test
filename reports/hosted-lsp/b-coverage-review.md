# Hosted LSP independent B coverage review: final source revision

## Result

**51/51 cases have current semantic coverage; no known implementation gap remains in this snapshot. Final local execution: 69 passed, 3 failed, 0 skipped/errors. Qualified gate: `needs_decision`.** There are 50 test functions, 60 case-to-function bindings and 72 collected concrete selectors. Counts differ because of shared bindings and parametrization, not additional cases. The JSON gives every case's real setup, trigger, observation, located assertions, exact selectors and limitations.

Reviewer: `independent-coverage-review-final`; `isolation_unverified`. This is an independent native source review without host/transport attestation. It is not G2 acceptance, merge approval or a claim that all tests passed. No integration test or fetched script was executed by this reviewer. The review did not consume the design review's conclusions.

Product remains at unchanged `8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8`; tracked product status was clean. Test base: `4be658da51967421be9d58543fe0d5c2888e324b`; review framework: `6768441f75ced58132d32f0d0914638b403b4edd`. The task-specific G1 procedural waiver is preserved without claiming that the user individually approved the 51 rows.

Frozen input: `input-index-final.json`, fingerprint `b5e62d74770939ad5a75644e58508160ca1ad4992326444579221a9b4c3de724`. All 25 file hashes and the manifest fingerprint were independently recomputed. This revision supersedes the stale first pass and the pre-LSP-007 snapshot `dd7938b55030a582175d730d9f496bd0886c555215e1fb25e1182b74cd8944a2`. Any executable, dependency, fixture, semantic-design or product change invalidates affected evidence; unrelated execution-report additions do not silently change source coverage.

## Finding dispositions

All findings below were reported while fixes were still possible, then checked in the final source rather than credited from implementation summaries.

| Finding | First-pass defect | Final verified disposition |
| --- | --- | --- |
| F01 | External invoice used invalid `Sha256` wire enum. | Uses actual `sha256` JSON enum. |
| F02 | Settlement omitted required `payment_hash`, allowing malformed cross-tenant negatives and broken happy paths. | Explicit SHA256 test preimage deterministically derives the required hash; both valid and intruder requests have real API shape. |
| F03 | SDK 24-hour minimum was not ensured by debug invoice expiry. | Explicit two-day final delta, one-day SDK minimum and seven-day route budget establish feasible lifetime. |
| F04 | LSP-025 ten-minute route limit was below mandatory four-hour privacy reserve. | Feasible 20-hour route limit/24-hour buffer/8-second or 48-hour invoice; exact actual incoming-expiry min oracle and strict winner. |
| F05 | LSP-033 zero trampoline fee was rejected before the intended liveness path. | Positive independently approved 5000-unit budget and exact total debit; no receiver-secret bypass. |
| F06 | Signature tests awaited pending before submitting funding. | Genuine funded pending request is established first; idle pending opening remains distinct from active TLC/delivery busy state. |
| F08 | Missing derived ID, balance preservation, decoded invoice equality/expiry and fee reconciliation. | Independent canonical ID; exact eviction balance snapshot; full decoded equality; exact payer debit = amount + returned fee. |
| F09 | Forbidden parse/cancel used invalid inputs and broad exception acceptance. | Actual public invoice/hash, `RpcError`, preserved invoice/count/channel state; auth timeout fails outright. |
| F10 | Valid cross-tenant signature, populated public/A/B payment namespaces and wrong-LSP negatives were absent. | Valid owner signature is rejected for intruder without changing pending; all three have actual payments; cross-service namespaces reject. |
| F11 | Delivery guard masked independent runtime busy guard; only offline duplicate was tested. | Held outbound ordinary payment proves no hosted delivery and exact runtime-busy rejection; duplicate test covers offline ordinary and online hold. |
| F12 | Raw funding mutations invalidated signatures, leaving rejection cause ambiguous. | Requires exact raw-data-mismatch rejection, retained live inputs/frozen opening, then legitimate recovery. |
| F13 | MPP pending count could include upstream plus one downstream. | Receiver-pubkey filter, two distinct downstream IDs and sum of actual shard amounts equals invoice amount. |
| F14 | Capacity test accepted arbitrary exceptions. | Restricted genuine RPC route/capacity failure or real Failed session; no transport exception credited. |
| F15 | Settlement conservation did not prove exact tenant entitlement. | Independent wallet-approved opening amount and commitment fee, exact prior peer claim/final deficit, sponsor allocation, matched confirmed raw transaction and exact payout. |
| Final LSP-007 | Rejected `enabled=False` write could mutate the owner while channel state stayed unchanged. | Both owners start enabled and retain exact ID/outpoint/state/enabled/balances/reservations/TLCs/routing terms/shutdown hash and one channel each. |

Initial coverage was 24 covered, 24 partial, 3 uncertain, 0 missing. Those were source-review findings, not execution outcomes. The final 51 covered entries mean that effective tests now exercise and assert each selected invariant. Strict LSP-033/034 assertions exposed the selected liveness and atomicity failures in the final run; their static coverage remains covered.

## High-risk boundaries rechecked

- **Independent chain provider:** `chain.rs` authenticates checked-in contract bytes against dev-chain genesis via unchanged `DevChain`, verifies canonical committed transaction/hash/version, follows actual input-zero/output-zero ancestry, and preserves contract code hash/hash type, type script, lock prefix and supported features. Root matching requires exact funding dependencies even when matching the signing hash that omits dependencies. Recovery source must belong to the selected channel's persisted SDK records. Live-cell, maturity and median-time checks remain delegated to exported production checks; production verified prepare/confirm remains in the signing path. No asserted ancestry, fake spent cell or unsigned shortcut is accepted.
- **Exact format scope:** supported layouts are 57-byte legacy and 58-byte full-hash feature=1, as emitted by the pinned builder. The pinned SDK parser also accepts 58-byte feature=0; refusing that shape is only a local provider limitation. Bundled binary provenance does not establish equivalence with a newer external contract source. Ancestry beyond 128 steps fails closed. Integration observations cover real 58+1; direct controls cover unsupported/malformed bounds.
- **Watchtower oracle:** actual accepted receipt is linked to the exact confirmed raw transaction, not merely a proposal or positive balance. Claim equals independently authorized tenant entitlement (or held TLC amount), with explicitly computed commitment fee and sponsor/settlement-fee allocation. Raced-but-unmined receipts do not count. A stale-request refusal is tolerated only with independently confirmed spent input, identical wallet snapshot and no submit RPC, submission result or captured signature.
- **Revocation:** a genuine `CompleteReceivedRevokeAndAck` is first rejected when peer partial evidence is removed. Complete local SDK records are then required before old peer commitment broadcast and confirmed exact penalty payout. Latest local recoverability ordering matches production `release_preimage` and cannot select an older matching payment.
- **Quotas:** separate real probes prevent route penalties from intercepting admission tests. Exact per-tenant/global rejection reason is observed in new log bytes; durable existing deliveries remain unchanged. During reuse only B1 may remain at the payer, and B remains Deferred; final global endpoint drainage is mandatory.
- **Mining:** each generated block uses a template matching canonical parent/next height, submits once, then proves returned block canonical. Stale templates are waited out before submission. Epoch maturity uses canonical fractional epoch progression, not the RPC return alone. No blind extra-block/error retry changes semantics.
- **Negative-test integrity:** exact Biscuit authorization timeout is a hard pytest failure in both ordinary and signer transports; it cannot satisfy expected rejection. Genuine legal operations follow rejection controls where the scenario permits recovery. The only opening setup retry is the specific pre-mutation peer-Init race; tested invalid opening calls themselves are direct.
- **No secret bypass:** receiver authorizes and releases only its own preimage; outbound intent has no secret and payer fulfillment comes only from its own `get_payment`. The bounded liveness oracle cannot count receiver Paid / payer Inflight as success.

## Final execution evidence and qualified gate

**`needs_decision`**: the complete local serial run is red and no host-attested reviewer isolation or controlled per-case acceptance receipt exists. Source coverage and runtime outcomes are separate. The reviewer read execution artifacts but did not run integration tests.

`final-serial.log`, `final-serial.xml` and `final-serial.exit` independently agree on **69 passed, 3 failed, 0 skipped/errors, exit 1**, in **1995.76 seconds** (XML suite duration 1995.750). All 72 JUnit test IDs exactly match `final-collection.log` selectors, with no missing or extra instance. All frozen 25 source hashes, canonical manifest fingerprint and unchanged pinned product were reverified after the run. Final source fingerprint stays `b5e62d74770939ad5a75644e58508160ca1ad4992326444579221a9b4c3de724`.

Local case-level observation: 48 cases have only passing bound selectors; LSP-033 and LSP-034 each have a failed selector; LSP-051 has three passed selectors and one failed selector. This is a direct local JUnit-to-reviewed-binding comparison, not automated per-case acceptance. No failure is skipped, xfailed or converted to a passing observation.

| Case/selector | Actual result and evidence | Attribution limit |
| --- | --- | --- |
| LSP-033 `test_external_hosted_payer_obtains_fulfillment_only_from_own_rpc` | Receiver invoice **Paid**, owner secret released; payer **Inflight**, no RPC preimage and no local fulfillment authorization. Payer remains `SignatureRequired` at `CompleteReceivedCommitment`; receiver has no signing request. SDK reports TLC removal without a locally authorized resolution. Independently approved debit was 1,005,000 for a 1,000,000 invoice. | Reproduced bounded liveness target failure, consistent with the preimage/commitment dependency. No unsupported claim about every route or runtime is made. |
| LSP-034 `test_rejected_missing_expiry_invoice_leaves_no_partial_write` | Actual `new_invoice` rejects specifically for missing finite expiry, but the same hash is then observable as **Open with no expiry_time**. Strict absence assertion fails. | Reproduced rejected-write residue / atomicity failure. Same-hash corrected creation is after the failed assertion and was not reached in this run. |
| LSP-051 `test_sdk_rejects_hostile_watchtower_transaction_before_signing[fee]` | All intended hostile-proposal rejection, no captured/submitted signature, unchanged wallet snapshot and unchanged genuine-request checks were reached successfully. Its later legitimate `settle()` recovery timed out at 90 seconds. Payout, sponsor and fresh-live-input variants passed. | Persisted state and canonical-chain evidence establish stale-request starvation in this run. SDK refuses the spent first request correctly, while the live successor remains hidden by first-only selection. Successful negative subchecks do not make the full fee selector pass. |

### Independent fee-recovery attribution

The final fee failure is an **observed watchtower liveness failure caused by stale-first request selection**, supported by persisted state and chain history rather than a timeout alone:

1. The saved `BTreeMap` contains two pending requests in order: `0xf608d9a4…` then `0xfe265a1f…`. The first spends original commitment `0x27f98f49…:0`, which canonical host settlement `0xd9daf010…` already spent at block **115**.
2. The second spends that host transaction's commitment successor `0xd9daf010…:0`. Both this successor and its sponsor remain unspent through canonical tip **388**. Thus a real live recovery request exists behind the stale one.
3. The one accepted SDK receipt `0x5ab15491…` corresponds to competing transaction `0x31da34e1…`, not the confirmed host spend. Original FNN log records its RBF rejection: fee 785 versus minimum replacement fee 1962. An accepted signature did not establish an actual payout.
4. `fiber-types/src/watchtower.rs:160-161` returns the first ordered pending request; `fiber-lib/src/rpc/watchtower.rs:484` exposes only that request. Actor insertion at `watchtower/actor.rs:2151-2155` does not prune spent requests, and signature submission removes only the submitted request ID. The safe SDK repeatedly refuses the stale source and cannot reach the live second request through that API.

The reviewer independently recomputed all **389 exported block-header hashes**, verified parent continuity from genesis through 388, parsed and hashed the saved Molecule proposals, checked their actual inputs, and matched exact canonical transactions/spends against the saved request records. Original FNN log lines 235/244 corroborate host broadcast and competing RBF rejection. Evidence: `local-runtime-evidence/final-watchtower-fee/chain-and-request-evidence.json` (SHA-256 `cef82595d8ef482450dd7b0ca43233c8983fbb72658918742a24f0e0aa4ceb96`), its `signer-requests-public.jsonl` and `watchtower-stale-request.json`; public structured counterpart `watchtower-liveness-evidence.json`.

The separately inspected unchanged comparison `local-runtime-evidence/watchtower-fee-final-comparison.log`/XML **passed 1/1 in 36.13s**. The original final run remains **69 passed / 3 failed**. Occurrence is competing-settlement timing/request-hash-order dependent, with mixed outcomes across fresh runs; it is not a deterministic failure claim. No funds-loss claim is made, and accepting a stale signature or dropping the live-cell check is not a valid workaround.

LSP-033's sanitized `runtime-evidence/final-lsp-033.json` was compared byte-for-byte to the original run diagnostic, SHA-256 `2601f39e09ccaa856b61c73743d47599721081217596924c727f74edc520d89e`. It contains no secret preimage. LSP-034's exact residue is present in the raw log and JUnit failure, rather than inferred from source.

Historical evidence remains distinct: quota v4 passed 1/1 in 71.14s; strengthened namespace passed 1/1 in 29.57s; focused watchtower 043/045/046 passed 3/3 in 92.84s. Earlier `watchtower-full1.log` had 2 failures, 7 passes and 1 setup error. Later focused successes neither erase these outcomes nor establish stable repeatability. The earlier interrupted final attempt completed no selector and earns no execution credit.

Plain CKB, finite scenarios and observable pending-state restart boundaries only. UDT on-chain external signing, precise internal crash windows, long-duration safety-deadline expiry, unrelated product diff and production load remain outside the reviewed claims. No all-green, stable-repeatability, CI-pass, product-acceptance or merge claim is made. Isolation remains `isolation_unverified` independently of both code coverage and execution outcomes.

## Final reviewed SHA-256

- `.github/workflows/hosted-lsp.yml`: `5f7c4582d37c0af04b5a60e07b03b697f9b2ee77c5cffb7c248d5f9232aa0fe4`
- `Makefile`: `c162139a99e6eb91d7289112cd13499bc56f8d61d0a8bd9704eed77e955c1889`
- `framework/lsp_devnet.py`: `afa7009fd70025bcd5ef17da5ed2ad432c14cd3a0deb723c5d4bd86558bca009`
- `framework/lsp_signer.py`: `c02d42d3fbb920b61da3b03bb7c2700fc0beefd51c24595ade77843b8a194e54`
- `requirements-lsp.txt`: `29a14a75d708fad171947e61b8d29bda007adb67bbe2abc4565e926fad55631e`
- `reviews/lsp/hosted-lsp.md`: `606162aafc76ae1a6f303bba0c102687ad19b17db3524bfff598697dcad1b831`
- `scripts/_lsp_check_loader.py`: `9d8580ff731854d9012f1a6294afeb40dabc95fd52066a445933bae6cea8d967`
- `scripts/build_lsp.sh`: `bd13ba9b22b7a5b24d32855a255a0ddea7fee27aadcf4b455ce3b4cd909558c0`
- `scripts/check_lsp_scope.py`: `d4abdc59ace2489319e986e34632abe7cc97507d1b86c82373b41ced71fa9fb0`
- `scripts/check_test_design.py`: `3bb3dd168ca8b42e637c66c7290902256513584efa78d2320b090252ab90ad1e`
- `scripts/check_test_map.py`: `7837dafaf529453b25689dbb5291edcea91fd2f69a8bb955c6ea8d709d205f10`
- `test_cases/fiber/devnet/lsp/conftest.py`: `a8530b4a50cc4b340a80c01846e960543abb3b84900701115a4823b458cc2b48`
- `test_cases/fiber/devnet/lsp/test_payments.py`: `91029390b94c5fbfea753395ef342b70cd8309f9119923c06055d23baf33332b`
- `test_cases/fiber/devnet/lsp/test_registry.py`: `a70d8c057f471c7ff2f1cf17f894100ee9956bc93b9029409f6f4fedeac592cf`
- `test_cases/fiber/devnet/lsp/test_signing.py`: `28b2f60c75de43310971ffbbc1ba68a1471f245b363c2fffc626208cbde31a00`
- `test_cases/fiber/devnet/lsp/test_watchtower.py`: `508160ded7fd63fc7129b28a63a6184104a9c99391162ebb4c2ba2238bd24033`
- `tools/lsp-sdk-driver/Cargo.lock`: `3422f324168b8a747af0c0a6d64d6da2b1059ab1f1409de037177e057755141b`
- `tools/lsp-sdk-driver/Cargo.toml`: `5516ee5aa04b1ae51bba0f7c850c1a2858ca3ed70cb7a27483f5a2f8696012fb`
- `tools/lsp-sdk-driver/build.sh`: `315a9208ed8a74e807bad88bd8f7209d61100653421c89ec2e329caeaedadc22`
- `tools/lsp-sdk-driver/check_jsonl.py`: `0538e6f3cd071291415b1bbf57fc074be01542c5bb26e1bdfa25f640bee97121`
- `tools/lsp-sdk-driver/rust-toolchain.toml`: `24ef3b9d3edbd850aa386cb0a98e10450b0030991a4537cb359f54d49dbbb33a`
- `tools/lsp-sdk-driver/src/chain.rs`: `241e245139b37ba73548ff1790f2a15cd8716d58aa9901ffa9c31bab913a92a9`
- `tools/lsp-sdk-driver/src/main.rs`: `6ce8105211bfd7f9d8ce381c32d26b917d9dae9d2b9ab9bf586359b4b2d95980`
- `tools/lsp-sdk-driver/src/transport.rs`: `6830583a9962582d3bfaabea798e66020a8a5e8f82b2b5104e40612f7c806385`
- `tools/lsp-sdk-driver/src/watchtower.rs`: `8c473ae7cebde6a1c2b72b312d04f28d7eda4775744872c93866bc63e4c831ff`

## Preserved initial snapshot hashes (stale)

First-pass fingerprint: `678ab97c516ce81fb2126675d5128e4241eeb502b8185360b14771e98e6a4e2a`. These hashes identify the original reported defects; they do not bind final assertions.

- `reviews/lsp/hosted-lsp.md`: `29d683a6a799d5718241dd359eb7857061d1e7a928246b9388953fc539bff2c7`
- `test_cases/fiber/devnet/lsp/test_registry.py`: `7213fcdd4ebd146d7e4fd03e2df292a813466f9e053d306d85c9d1c37296f139`
- `test_cases/fiber/devnet/lsp/test_payments.py`: `f7f34b52b797b6ea319c44b4af2ed85a092b351051b1cff187391bc77fa9ef93`
- `test_cases/fiber/devnet/lsp/test_signing.py`: `7a7f62e8a02750d30765a897f5f08144fb85ecf7b4611a73604870845ce65a45`
- `test_cases/fiber/devnet/lsp/test_watchtower.py`: `18c773e83d98c97434ad15b8ed88205d7dc12ad46fae32134a7ee8eec7347814`
- `framework/lsp_devnet.py`: `1b2eac1c7f60aaf0f2f46e337aecf64b4e660e08f4239de94a07681e71925bd2`
- `framework/lsp_signer.py`: `d4f396ad1d23a3f4c5f84de2d678b796b6bd4e68f241492a7d8098a2422a6b5d`
- `tools/lsp-sdk-driver/src/main.rs`: `c7efa38d9a2fd9719da4fed6acf624b3bd7b993ac2982d2dc782849170d1aa4d`

Schema v2 has no separate validity, execution or per-file-hash fields. Those dimensions are therefore explicit in each case's limitations and in this companion report. Literal assertion/symbol matching and schema validation were checked with read-only AST inspection and local JSON-schema validation; they do not replace semantic review.
