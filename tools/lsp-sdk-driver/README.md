# Pinned production-SDK integration driver

This local-only JSON-lines process is an adapter for the real `fiber-lsp-sdk`
and exported `fiber-lsp-sdk-agent` library at product commit
`8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8`. It makes actual HTTP JSON-RPC calls;
it does not replace the LSP, channel actor, chain verifier, or SDK with a mock.
The product checkout stays unchanged. Attribution for the small adapted fixture
policies is preserved in `NOTICE`.

## Build and start

The fixed product checkout must be the sibling `fiber-lsp` directory of this
integration-test repository. Install the toolchain from that checkout's
`rust-toolchain.toml`; then run `tools/lsp-sdk-driver/build.sh` from any directory.
The script checks the product revision and rejects modified tracked product source.
`Cargo.lock` pins dependencies separately for this adapter.

```
fiber-lsp-integration-sdk-driver \
  --rpc http://127.0.0.1:8227 \
  --store /path/to/isolated/test-signer \
  --operator-token "$TEST_OPERATOR_TOKEN" \
  --ckb-rpc http://127.0.0.1:8114 \
  --contracts-dir /path/to/fiber-lsp/tests/deploy/contracts
```

Send one JSON object per stdin line. Every response is exactly one stdout line:
`{"id":1,"result":...,"observations":{"rpc_calls":[],"captured":[]}}` or
`{"id":1,"error":"...","observations":...}`. No background polling, retries,
or signature confirmation occurs between commands. Python owns bounded waits,
process cleanup and restart. All test wallets and tokens must be isolated devnet
fixtures. Do not publish signer stores or tokens.

## Commands

- `{"id":1,"op":"identity"}` creates/opens the wallet identity without RPC.
- `attenuate` accepts a valid `token` and Datalog `code`, such as
  `tenant("another"); node("local");`. It appends a signed attenuation block with
  Biscuit's public API and returns `token`; no authority signing key is read.
  The real server remains responsible for authority/routing scope validation.
- `nonce` obtains a challenge for that root identity.
- `registration_proof` accepts `nonce` containing the full challenge result and
  returns SDK-signed registration params. Altering the nonce/LSP domain before
  this operation permits genuine wrong-domain/old-challenge signatures.
- `register` submits optional `params`, or obtains a fresh challenge and signs
  it when params are absent. SDK verifies the returned tenant identity before
  saving the token.
- `initialize` uses production Agent registration and pending opening material.
  The returned status contains `tenant_token`, `root_signer_pubkey`,
  `channel_open_signer_material`, and `bound_channel_ids`.
- `status` reads the last successfully written Agent status. It is a cached local
  observation and does not activate the tenant. A successful `open` or `poll`
  refreshes it. A captured/failed poll may leave status unchanged.
- `open` takes `params`, the full `OpenChannelWithExternalFundingParams` originally
  approved by the wallet. Agent invokes the SDK opening entrypoint and checks
  funding inputs independently at the CKB endpoint. No implicit opening or
  commitment authorization is inferred from a signing response. Optional
  `response_patch:[{"pointer":"/channel_id","value":"0x..."}]` replaces existing
  JSON-pointer fields in the genuine returned proposal before SDK verification.
  `observations.opening_responses` contains both `original` and `sdk_input`.
  The altered response is never written to the server. A failed verification
  leaves the original wallet opening intent available for a genuine retry.
- `authorize` takes `authorization`. Existing Agent authorization types are
  `Invoice`, `ReleasePreimage`, `Close` and `Watchtower`, unchanged from its README.
  Added `Outbound` takes `channel_id` and full SDK `PaymentAuthorization` in
  `terms` with `inbound:false`. A `preimage` field is rejected.
  Added `Cancel` takes `channel_id` and `payment_hash` for explicit cancellation.
- `outbound_fulfillment` takes `channel_id` and `payment_hash`. It queries the
  payer's own `get_payment` using this wallet's token; only a preimage in that
  response is passed to SDK `authorize_fulfillment`. The response contains
  `authorized`, `source:"payer.get_payment"`, and the observed `payment`.
  No caller-supplied preimage is accepted. If the LSP has not exposed a preimage,
  it returns `authorized:false` and does not attempt to invent one.
- `poll` services real channel statuses using production SDK Manual policy and
  the exported Agent's `poll_watchtower_verified` for watchtower confirmation,
  submission and accepted receipts. It explicitly confirms SDK-verified requests.
  It returns `ok`, optional `error`, `rpc_calls`, and `captured`. `ok:false` is a
  real SDK/network error, not a successful assertion. With `capture:true`, the
  first valid submit is retained locally and the transport stops before sending;
  SDK state has already recorded the signature. This permits exact replay and
  receipt mutation tests without generating fake cryptographic material.
- `signing_status` takes `channel_id`, with optional `watchtower:true`. It reads the
  real server signing status using the wallet token. Channel signing status is
  a cold-safe server operation at the pinned revision.
- `prepare` takes `channel_id`, optional `policy:"Manual"|"Auto"` (Manual default)
  and optional raw `status` (the inner object of a real signing-status result).
  Without status it fetches the real status. A supplied status is the explicit
  local response-mutation seam for testing mandatory SDK validation. No forged
  status is sent to the server. Returns `Idle`, `Denied`, `NeedConfirmation` with
  `prepared_id` and SDK review, or `ReadyToSubmit` with `submission`.
  Auto may actually sign a verified allowed request; it never submits.
- `confirm` takes `prepared_id` from this process. It calls the SDK's confirmation
  path with a fresh local clock, revalidating mandatory approval constraints;
  returns `submission` without sending it. Prepared approvals are intentionally
  volatile. Restart the complete store, then prepare the real pending request
  again rather than serializing SDK-private approval internals.
- `watchtower_authorization` takes `channel_id` and returns the genuine pending
  `status` plus exact `authorization`. It uses previously persisted wallet
  Watchtower destination/fee-cap intent, SDK recovery history, independently
  confirmed commitment ancestry, live CKB inputs and local contract dependencies.
- `watchtower_prepare` takes `channel_id`, that fixed `authorization`, and optional
  raw `status` (otherwise reads the real server). It calls production verified
  on-chain preparation and returns `NeedConfirmation` with `prepared_id`.
  Keep authorization unchanged when mutating destination/output/fee in status.
  `confirm` also handles this pending request and rechecks actual live-chain
  inputs and maturity; spending the real input between these commands must
  therefore be rejected by the SDK. These commands never submit.
- `submit` takes `submission:{"method":"submit_channel_signature"|
  "submit_watchtower_signature","params":...}`. Params may be copied or mutated
  by the Python test. It returns the exact real RPC result. This is the explicit
  negative-test seam, and does not alter local SDK state.
- `snapshot` returns lengths and BLAKE2b fingerprints for every authoritative
  persistent wallet file: root key, SDK snapshot, session directory, watchtower
  approvals, and accepted watchtower receipts. It never returns private bytes.
  Cached status is excluded because it is not authoritative signer state.
- `recovery_status` takes `channel_id` and returns SDK-ordered `records` containing
  `reference`, `complete_signature` (boolean), `revoked` (boolean), and
  `payment_hashes`. It reads public SDK recovery/revocation records locally,
  without polling or changing state. This is a readiness oracle; final preimage
  release still requires the SDK's full live-funding and recovery-window checks.
- `verify_commitment_lineage` takes `channel_id`, `source` (a complete SDK
  `CommitmentReference`) and `outpoint` (JSON CKB outpoint). It first requires the
  exact source in that channel's local SDK recovery records, then independently
  verifies committed ancestry. Returns `verified:true`; never signs or submits.
- `shutdown` returns an acknowledgment and exits successfully.

All commands also return observed RPC method names, actual fetched watchtower
statuses as `watchtower_statuses:[{channel_id,status}]`, and successful real signature
`submission_results` entries `{method,result}` (including Applied/AlreadyApplied). A refused `ReleasePreimage`
can therefore assert neither `settle_invoice` nor `create_preimage` was attempted.
On success those methods execute only after SDK recovery checks. Watchtower
preimage release must happen while the funding outpoint remains live, before the
force-close commitment is confirmed; the production SDK deliberately refuses
release after it is spent.

## Persistence and limitations

Each command reopens exactly the same full on-disk wallet store. Only one SDK
store instance is alive at once, preventing independent in-memory snapshots from
overwriting one another. Keep all files together on process restart; copying
only `root.key` is not signer recovery. Do not run two drivers against one store.

### Independent chain-provider compatibility

The unchanged upstream test Agent's default `DevChain::lineage` hardcodes
57-byte commitment args. A real devnet run reached a committed 58-byte full-hash
commitment and descendant, accepted the first Settlement signature, then failed
on the descendant with `not a commitment descendant`. This is an observed test
Agent compatibility limitation, not evidence that the production SDK rejects
valid 58-byte commitments.

`src/chain.rs` supplies the SDK's public `ChainVerifier` boundary. It reuses
DevChain's unchanged live-cell, maturity and median-time checks and genesis/local
contract-binary verification. Only ancestry traversal is adapted: every step
uses canonical committed transactions with verified hashes, exact input zero and
output zero, trusted commitment script identity, invariant type/prefix/features,
supported 57/58-byte layouts parsed by the pinned production feature parser,
valid commitment versions, exact known funding dependencies for the signed
source template, and a maximum of 128 steps. Unknown features or changed layouts
fail closed. This provider supports exactly 57-byte legacy and 58-byte feature-one
full-hash layouts, the forms emitted by the pinned node builder
(`crates/fiber-lib/src/fiber/channel.rs`, commitment-lock args construction).
The pinned SDK feature parser also accepts 58 bytes with feature zero; this
adapter rejects that shape as outside its supported fixture formats. This is
not a claim that the production SDK or bundled contract rejects it. The real
fixture exercises 58-byte feature one. Newer external contract source and the
stale source revision documented beside the bundled binaries are not treated
as proof of bundled-binary behavior.

The production SDK still independently validates spend content,
destination, fee, witnesses, recovery evidence and all on-chain signing policy.

The local provider is used for watchtower authorization, verified polling,
prepare/confirm, and the read-only lineage command. Exported Agent preimage
release and opening behavior are retained. No product or upstream Agent source
is modified, and the upstream default lineage incompatibility is not claimed fixed.

The Agent and chain provider support plain CKB fixtures. This adapter does not claim
UDT settlement, automatic outbound fee discovery, snapshot restore/rollback,
arbitrary watchtower authorization, or private SDK field access. Payment terms
and exact channel amounts (including explicitly approved route fees) must be
supplied independently by the wallet test before `send_payment`. No application
operation may pass receiver-only secrets to the payer.
