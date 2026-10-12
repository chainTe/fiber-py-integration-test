# Watchtower stale-request starvation

**The final serial run observed a product liveness failure after safe rejection of a hostile fee.** The original result remains **69 passed, 3 failed**. One later, unchanged focused execution passed; it does not replace the failed observation.

## Observed failure

`test_sdk_rejects_hostile_watchtower_transaction_before_signing[fee]` passed its malicious-fee rejection, no-signature/submission and unchanged-wallet-state assertions. Legitimate recovery then exceeded its unchanged 90-second bound.

The persisted request queue and canonical chain establish why:

1. The original commitment `0x27f98f49…ffd7c0` was confirmed at block **37**.
2. The SDK accepted Settlement request `0x5ab15491…fb62f7`, corresponding to transaction `0x31da34e1…439025`. The host's competing settlement `0xd9daf010…ec2aa6` won; CKB rejected the SDK-signed competitor with `PoolRejectedRBF` (fee 785; replacement required at least 1962).
3. The winning host transaction consumed the original commitment at block **115**, creating another commitment output at index 0.
4. At test end, the external signer store contained two pending requests in ascending request-ID order:
   - **First:** `0xf608d9a4…565866`, still requesting a signature over the already-spent original commitment.
   - **Second:** `0xfe265a1f…0f3b17`, referencing the host's descendant commitment. Both this input and its sponsor input remained unspent through canonical tip **388**. The remaining commitment held **49,999,999,543 Shannon**.
5. The SDK correctly refused the first request with `independent chain verification: input is not live`. Each tolerated refusal required an independently confirmed spender, unchanged signer snapshot, zero submission RPCs, and zero captured signatures. Watchtower periodic scans continued about once per second through the timeout.

This is an observed stale-request queue starvation mechanism, not evidence of an accepted malicious fee or lost funds. The blocked remainder stayed on chain. The test did not discard the stale request, select a hidden successor, resubmit an unsafe signature, or extend its timeout.

## Source mechanism

At unchanged product commit `8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8`:

- [`first_pending()`](https://github.com/chenyukang/fiber/blob/8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8/crates/fiber-types/src/watchtower.rs#L160-L161) returns the lexicographically first entry of a `BTreeMap`.
- [`get_watchtower_signing_status`](https://github.com/chenyukang/fiber/blob/8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8/crates/fiber-lib/src/rpc/watchtower.rs#L379-L400) exposes that single request through its conversion at lines 476–499.
- The [actor's request insertion](https://github.com/chenyukang/fiber/blob/8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8/crates/fiber-lib/src/watchtower/actor.rs#L2151-L2155) retains previous pending requests; this path does not prune requests whose inputs were consumed. Signature submission removes only the submitted request.

The persisted ordering therefore prevents the live successor from reaching the ordinary SDK poll path. The queue contents and ordering, rather than only a source-level possibility, were observed in this failure.

## Evidence and comparison

[Secret-free structured evidence](watchtower-liveness-evidence.json) contains full request IDs, raw public signing transactions, accepted receipt metadata, exact confirmed transactions, canonical block hashes, pending order and input-spend observations. The FNN database was opened read-only using the pinned schema. Canonical blocks 0–388 were exported offline from a database copy; no node was restarted for extraction. A separate reviewer recomputed all 389 header hashes and parent continuity, parsed the saved proposals, recomputed their raw transaction hashes, checked the original spend and both unspent successor inputs, and verified the source and RBF-log mechanism. Reviewer isolation is not host-attested.

The one unchanged comparison ran the exact fee selector with the same 90-second bound: **1 passed in 36.13 seconds**, exit 0. Its [pytest output](runtime-evidence/watchtower-fee-comparison.log) and [JUnit](runtime-evidence/watchtower-fee-comparison.xml) are separate from the [original final run](runtime-evidence/final-junit.xml). No product, adapter, test or timeout was changed. The differing result is consistent with a settlement race and request-ID ordering; it does not establish stable repeatability.

Frozen test SHA-256: `508160ded7fd63fc7129b28a63a6184104a9c99391162ebb4c2ba2238bd24033`.
