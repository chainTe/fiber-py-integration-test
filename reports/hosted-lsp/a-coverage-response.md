# Responses to independent coverage review

The initial B snapshot was superseded by the final source review in
[b-coverage-review.md](b-coverage-review.md). These are historical implementation
responses, not a declaration that tests ran or passed. The final review verified
the corrected frozen files; actual execution is recorded separately.

- F01: Corrected invoice wire enum to `sha256` in every external and baseline RPC payload.
- F02: Baseline settle helper supplies the real SHA256 payment_hash and preimage; cross-tenant settlement now uses a well-formed, live owner invoice.
- F03: External invoice final expiry is explicitly two days, independently authorized SDK minimum one day, maximum eight days. This avoids depending on random privacy slack.
- F04: Deadline setup uses a 20-hour payer budget, a 24-hour operator buffer, a 48-hour invoice for the safety variant and explicit one-minute forwarding/final deltas. It independently derives the exact cutoff from the actual upstream TLC and proves the selected term is strictly smallest. The short invoice variant waits to failure; long safety variant does not claim elapsed-expiry coverage.
- F05: External outbound uses a preselected positive 5,000-shannon trampoline budget, and the payer authorizes invoice amount plus that exact budget before payment exists. Receiver-only preimage is never passed to payer.
- F06: Tests submit genuine signed funding before waiting for the opening signature checkpoint, following product signer_isolation setup. No synthetic pending-state insertion.
- F08: Added independently derived RootSigner tenant ID, exact eviction before/after balances, parsed invoice equality and explicit expiry, and exact sender debit = invoice amount + reported payment fee.
- F09: Forbidden parse/cancel calls now use a real valid Public T invoice, whose Open status is checked afterward; rejection cannot be satisfied merely by invalid invoice text or nonexistent hash.
- F10: Added genuine owner signature submission through intruder credentials with unchanged owner pending request. Populated Public T invoice and A/B/public outgoing sessions; tenant lists stay isolated, while Public T's legitimate forwarding sessions are distinguished by their own fees. Added wrong-LSP tenant/invoice/delivery/payment negative checks.
- F11: Added an outgoing hosted payment to an ordinary hold receiver with no LSP delivery record to exercise runtime busy protection independently. Duplicate payer test now covers both Deferred ordinary invoice and already InFlight hold invoice, preserving the first execution through completion.
- F12: Raw funding mutations require the precise raw-data-mismatch rejection, preserve original live inputs/frozen proposal, and then complete with the valid original.
- F13: MPP oracle filters receiver-bound channels, requires two distinct downstream IDs and exact shard total, excluding the tenant upstream channel.
- F14: Synchronous insufficient-route handling catches only RpcError and checks route/balance/capacity attribution; transport/programming exceptions fail the test.
- F15: Watchtower fixture derives exact wallet entitlement and fixed commitment fee independently from approved opening values and the protocol transaction template, then checks actual confirmed commitment capacity and final payout. Sponsor conservation and fee limits remain additional checks.

The first review's finding numbering has no F07; it is preserved without inventing
an extra finding. Runtime-driven corrections and final evidence are recorded
separately.
