# Payment runtime verification

Product: `8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8`, unchanged.
The results below concern `test_cases/fiber/devnet/lsp/test_payments.py` only.
The desired external-signer preimage-liveness regression is in the signing file.

## First full payment-file execution

Executed 2026-10-12, using the repository `.venv` and real isolated CKB/FNN
processes. From the integration repository:

```sh
export LSP_PRODUCT_DIR="$PWD/../fiber-lsp"
export LSP_FNN_BIN="$PWD/../runtime/target/debug/fnn"
export LSP_SDK_DRIVER_BIN="$PWD/../runtime/adapter-target/debug/fiber-lsp-integration-sdk-driver"
export LSP_CKB_BIN="$PWD/../runtime/ckb_v0.202.0_x86_64-unknown-linux-gnu-portable/ckb"
export LSP_CKB_CLI_BIN="$PWD/../runtime/ckb_v0.202.0_x86_64-unknown-linux-gnu-portable/ckb-cli"
.venv/bin/python -m pytest -o addopts= -vv -s --tb=short \
  --junitxml=reports/hosted-lsp/payments-attempt1-junit.xml \
  test_cases/fiber/devnet/lsp/test_payments.py
```

Result: **14 passed, 5 failed in 1152.04 seconds; exit 1**. Other real-process suites ran concurrently. This is not a final frozen-suite pass.

The test module loaded for this attempt has SHA256
`f1c75de84b49807d1e5d229029b904e9487b4dafbab0158f08a77778a409819c`.
Its exact source, FNN/CKB logs and provenance were preserved outside the repository
under `../runtime/test-runs/payments-attempt1-evidence/`. Wallet/key/token stores
were not copied into publishable reports. The source was subsequently corrected;
traceback line text rendered at session end can reflect those later edits. Use
the saved attempt source for original line numbers.

Local raw artifacts, excluded from publication:

- `payments-attempt1.log`: SHA256 `4a51f5d00d079f8331aaa8efc2431bfe6e21f3f4d180e86cd5c96ee67e9d6f04`
- `payments-attempt1-junit.xml`: SHA256 `40346a2f52cd87e6cc3ae463c486ddf5867bdde4327ba43bb677c59f2c2aac74`

### Passed selectors

- `test_hosted_invoice_identity_hint_and_scoped_lookup`
- `test_inbound_mpp_rejects_before_invoice_write`
- `test_online_inbound_settles_both_ends_once`
- `test_offline_deferred_delivery_resumes_without_resending`
- `test_deferred_and_final_delivery_survive_host_restart`
- `test_dispatched_hold_payment_outlives_buffer_deadline`
- `test_second_payer_cannot_replace_first_execution[True]`
- `test_second_payer_cannot_replace_first_execution[False]`
- `test_payment_between_tenants_on_distinct_lsps`
- `test_failed_outbound_payment_does_not_poison_next_payment`
- `test_deferred_deadline_uses_the_actual_earliest_bound[invoice]`
- `test_deferred_deadline_uses_the_actual_earliest_bound[tlc_safety]`
- `test_runtime_busy_guard_without_a_hosted_delivery`
- `test_three_rpc_identities_keep_their_own_outgoing_sessions`

### Failed selectors and attribution

1. `test_offline_buffer_timeout_does_not_revive_payment`: the delivery and payer
   reached Failed, the receiver remained unpaid with unchanged balance, then
   `Timed out after 30s waiting for expired TLC cleanup; last=False` at original
   line 289. Source inspection found that `rpc/channel.rs:565-585` exposes
   `all_tlcs()` as `pending_tlcs`, including failed history retained until a later
   commitment (`fiber/channel.rs:8312-8334`). The corrected oracle accepts only
   exact `RemoveAckConfirmed` history for this hash and simultaneously requires
   zero offered/received reservations on payer, Public T and tenant. Unknown or
   nonterminal states remain failures. The focused corrected execution below passed.
2. `test_tenant_outbound_ordinary_payment_and_payment_isolation`: payment/balance
   completion had passed, but operator `lsp_get_payment` returned code -32999,
   `Unauthorized: Biscuit authorization timed out`. It is a failed execution,
   not a permission-denial pass. No authorization retry was introduced.
3. `test_outbound_mpp_really_uses_two_downstream_channels`: setup fee budget was
   too small for the capacity-forced amount. `send_payment` rejected it with
   `recommend_minimal_fee=3000000, maximal_fee=30000000 current_fee=2000000`.
   The corrected test chooses the standard 0.5% ceiling before sending, retains
   exact debit/credit checks and checks the actual fee against that explicit
   ceiling. It also waits for receiver Received before settling. Two-channel
   shard-count and exact total-amount assertions remain intact.
4. `test_rejected_missing_expiry_invoice_leaves_no_partial_write`: the first
   `new_invoice` failed, but the following `get_invoice` did not raise (original
   line 603). This is runtime evidence of the strict desired atomicity invariant
   failing. Source writes the tenant invoice before finite-expiry registration
   validation (`rpc/invoice.rs:196,409`, `lsp/invoice.rs:193-195`). No xfail/skip,
   product patch or acceptance of residual state was added. The focused execution
   below required the precise finite-expiry error before observing the residue.
5. `test_pending_delivery_quotas_are_isolated_and_released`: A2 reached the LSP
   and was rejected for Alice's per-tenant quota. Reusing the same ordinary payer
   for B1 then failed locally with `PathFind error: no path found` (original
   line 715), so B's quota admission was never exercised. The quota rejection
   maps to TemporaryNodeFailure (`fiber/network.rs:5218-5223`), which marks the
   last outer-route node failed (`fiber/history.rs:312-343`). Corrected setup
   gives A2/C1 independent ordinary payers while the positive payer owns A1/B1
   and the later successful slot-reuse payment. Each rejected probe must have
   the corresponding new service-side quota log, plus terminal payer failure
   and no durable delivery. Route failure alone cannot satisfy the test.

## Additional test corrections

- Cross-LSP setup names both trampoline hops. Explicit hops replace, rather than
  append to, the invoice hint (`fiber/payment.rs:401-408`); the product's own
  cross-LSP test names both (`lsp/tests/integration.rs:2470`). This correction
  preceded attempt 1, in which the cross-LSP test passed.
- All negative assertions in this file now require `RpcError` with the
  appropriate rejection category: unsupported MPP, missing invoice/payment,
  unregistered tenant, unfinished delivery, busy runtime, or finite expiry.
  Transport failures and authorizer timeouts cannot satisfy them.
- These payment corrections change no product code. Shared fixture changes
  and runtime build details are reported separately.

## Focused corrected execution

Other real-process suites were paused for this serial run. This reduces
known test-suite contention; it does not establish a dedicated CPU or prove the
cause of an authorizer timeout. No test file was changed during execution.

Using the same five explicit `LSP_*` environment variables above:

```sh
.venv/bin/python -m pytest -o addopts= -vv -s --tb=short \
  --basetemp=../runtime/test-runs/payments-focused-v2 \
  --junitxml=reports/hosted-lsp/payments-focused-v2-junit.xml \
  test_cases/fiber/devnet/lsp/test_payments.py::test_offline_buffer_timeout_does_not_revive_payment \
  test_cases/fiber/devnet/lsp/test_payments.py::test_tenant_outbound_ordinary_payment_and_payment_isolation \
  test_cases/fiber/devnet/lsp/test_payments.py::test_outbound_mpp_really_uses_two_downstream_channels \
  test_cases/fiber/devnet/lsp/test_payments.py::test_rejected_missing_expiry_invoice_leaves_no_partial_write \
  test_cases/fiber/devnet/lsp/test_payments.py::test_pending_delivery_quotas_are_isolated_and_released \
  test_cases/fiber/devnet/lsp/test_payments.py::test_dispatched_hold_payment_outlives_buffer_deadline
```

Result: **4 passed, 2 failed in 383.35 seconds; exit 1**.

- PASS: expired delivery cleanup and fresh payment (86.810 s). The status-aware
  oracle required every retained entry to have the exact final removal state
  and zero offered/received reservations. The original empty-list failure was
  an oracle error, not evidence of an active-funds leak.
- PASS: ordinary outbound payment and operator/tenant isolation (59.892 s).
- PASS: real two-downstream-channel MPP, exact shard total and final balances
  (51.323 s).
- FAIL: missing-expiry creation atomicity (49.509 s). The exact finite-expiry
  rejection was observed, then `test_payments.py:644` failed at
  `with pytest.raises(RpcError, match="invoice not found")` with
  `DID NOT RAISE <class 'framework.lsp_devnet.RpcError'>`. The stored invoice
  residue remains a confirmed violation of this desired invariant. The
  subsequent corrected same-hash retry was not reached, so its outcome is not
  claimed as executed.
- FAIL: quota test (54.183 s) aborted before payer/probe creation. The explicit
  guard reported `Unexpected Biscuit authorization runtime timeout:
  lsp_list_tenants`, HTTP 200, JSON-RPC code -32999, with message
  `Unauthorized: Biscuit authorization timed out`. The Public T log confirms
  authorizer `run_limit: Timeout` at 16:32:52.713325 UTC. No admission probe ran
  in this attempt. Corrected quota admission and slot reuse were not reached.
- PASS: dispatched hold survives buffer deadline and cross-tenant settlement
  rejection is correctly attributed (81.119 s).

Frozen file SHA256:

- `test_payments.py`: `6914899d9797d93c5e2ce82b10ecb2cc141f8379accacd392d0fa703c799b937`
- `framework/lsp_devnet.py`: `878500cc1b2e45ef1bd67d7ed3351cc74c87e1e9f798b2f4d2f92b1cb7258ee4`
- LSP `conftest.py`: `a8530b4a50cc4b340a80c01846e960543abb3b84900701115a4823b458cc2b48`

Local raw artifacts:

- `payments-focused-v2.log`: SHA256 `082fd1c06eed94ff9f9169fcf195da14c123bc30588471865537edd9f44bad02`
- `payments-focused-v2-junit.xml`: SHA256 `4da2005d2f67318f93cff17ff536b726f48b0b5f34952a5af943fba5744b7573`
- Dedicated raw process state remains outside the repository in the stated
  basetemp. Exact source snapshots are in the sibling
  `payments-focused-v2-sources/` directory.

The latest file passes Python compilation and Black checking (exit 0 each).
Product checkout remains clean at the pinned commit. After this focused run,
LSP-034 gained a secret-free failure diagnostic: an unexpectedly returned
invoice reports its status, payment hash and expiry attribute (or its absence),
then fails. The invariant and fixed-product success path are unchanged. This
diagnostic-only snapshot has SHA256
`045dc2f1fb5ec9a5a155689566429b6b9bb3187879cb4abdf9901b7767b2640b`.
The final frozen-suite run is a separate result.
Neither the earlier 14 passes nor these four focused passes establish a pass of
all 19 selectors on the latest snapshot.

## Final-runtime quota verification

These two serial attempts used the same explicit environment variables above,
unchanged product source, and final optimized FNN. The build optimizes only
`scrypt`, `salsa20`, and `biscuit-auth` dependencies in the dev profile; it does
not change cryptographic parameters, the 10 ms authorization deadline or add
retries. Build details are in [runtime validation](runtime-validation.md).

Exact selector commands (each was run separately):

```sh
.venv/bin/python -m pytest -o addopts= -vv -s --tb=short \
  --basetemp=../runtime/test-runs/payments-quota-v3 \
  --junitxml=reports/hosted-lsp/payments-quota-v3-junit.xml \
  test_cases/fiber/devnet/lsp/test_payments.py::test_pending_delivery_quotas_are_isolated_and_released
.venv/bin/python -m pytest -o addopts= -vv -s --tb=short \
  --basetemp=../runtime/test-runs/payments-quota-v4 \
  --junitxml=reports/hosted-lsp/payments-quota-v4-junit.xml \
  test_cases/fiber/devnet/lsp/test_payments.py::test_pending_delivery_quotas_are_isolated_and_released
```

- **v3: 1 failed in 93.00 seconds; exit 1.** Both quota rejections were
  attributed to their exact new service-side reasons. Alice's delivery completed
  and Carol's fresh slot-reuse payment succeeded. The intermediate global drain
  assertion then failed because Bob's intentionally deferred TLC remained on
  the same payer. The payer log shows this retained hash in `Outbound(Committed)`
  with no removal; this is a test-oracle conflict. No authorization timeout was
  observed. Source SHA256 was the diagnostic-only `045dc2f...` snapshot above.
- **v4: 1 passed in 71.14 seconds; exit 0.** The corrected intermediate oracle
  requires exactly Bob's known hash to remain, no fresh or unrelated hash, and
  no receiver TLC. Bob's delivery must still be deferred after Carol completes.
  After Bob is resumed and succeeds, all positive endpoints must drain fully.
  Exact fee/debit/credit checks remain. Independent rejected payers still prove
  both per-tenant and global quota rejection, unchanged original deliveries,
  no rejected durable records, and reuse of Alice's released slot.

Source and binary SHA256 for v4:

- `test_payments.py`: `91029390b94c5fbfea753395ef342b70cd8309f9119923c06055d23baf33332b`
- `framework/lsp_devnet.py`: `afa7009fd70025bcd5ef17da5ed2ad432c14cd3a0deb723c5d4bd86558bca009`
- LSP `conftest.py`: `a8530b4a50cc4b340a80c01846e960543abb3b84900701115a4823b458cc2b48`
- FNN: `9ad5294cc001cef68fea4e09276fe40057c623a2acb014e144ada5f5c66498fe`
- SDK driver: `ccc1e1504bb36f11a0a5f1595a9882b05389cbe066550d72a9ae92e880fccdf1`
- CKB: `d85d74034231a4f68ea57e5b3fbe99e66736e08e108d7afeb32c0503d527e0b6`
- CKB CLI: `1f359467de3f208808530f47d03c09983c911ab041a3ae31210aef6aacca00aa`

Local raw artifact SHA256:

- v3 log: `3d467f1099a5402e5406ba3fe65f8bd7514dc4f3c1f91d193524c51008f433e0`
- v3 JUnit: `12b7297be2471814630b6c65731e01b2c0308a0e8238f4c8d2bc946bb2f3ee84`
- v4 log: `820f83156a005baba3bac0054b5eca370c49c2bb98f40ad15ebc3af02aaf616c`
- v4 JUnit: `d318f76b9cdbafa8cf36e6dbaa5901eb45e772933c6934be28d5e234a00ddafa`

For each run, stdout/stderr were saved through `tee`; the pytest pipeline exit
was saved using `${PIPESTATUS[0]}`. Matching `.exit` and `-provenance.txt` files
sit beside the ignored log/JUnit files. Dedicated basetemps and adjacent
`-sources/` snapshots preserve raw evidence outside the repository. Private
wallet stores, signing keys and bearer tokens must not be published.

Payment test edits stopped after v4. The final frozen full-suite execution must
establish all latest selectors together, including the strict LSP-034 regression;
this single quota pass does not replace that result.
