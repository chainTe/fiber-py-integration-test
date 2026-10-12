"""Real SDK Watchtower requests, independent CKB evidence, and confirmed payouts."""

from copy import deepcopy
import json
import secrets

import pytest
from coincurve import PrivateKey

from framework.lsp_devnet import RpcError, wait_for
from framework.lsp_signer import ExternalChannel, number, transaction_size_in_block

MAX_FEE = 10_000
RAW_TX_FIELDS = (
    "version",
    "cell_deps",
    "header_deps",
    "inputs",
    "outputs",
    "outputs_data",
)


def same_raw_transaction(left, right):
    return all(left[field] == right[field] for field in RAW_TX_FIELDS)


def outpoint(tx_hash, index=0):
    return {"tx_hash": tx_hash, "index": hex(index)}


def key(point):
    return point["tx_hash"], number(point["index"])


def transaction(env, tx_hash):
    result = env.ckb.rpc("get_transaction", [tx_hash])
    assert result and result["tx_status"]["status"] == "committed", result
    return result["transaction"]


def assert_live(env, point):
    result = env.ckb.rpc("get_live_cell", [point, True])
    assert result["status"] == "live", (point, result)
    return result["cell"]["output"]


def authorize_watchtower(channel):
    # Explicit test-wallet consent to the public node's sponsor/change script.
    # This is not inferred from a later signing proposal.
    destination = channel.opening["opening_context"]["remote_shutdown_script"]
    result = channel.signer.authorize(
        {
            "type": "Watchtower",
            "channel_id": channel.channel_id,
            "destination": destination,
            "max_fee": MAX_FEE,
        }
    )
    assert result["authorized"]
    return destination


def watchtower_status(channel):
    return channel.signer.signing_status(channel.channel_id, watchtower=True)


def pending_watchtower(channel):
    def read():
        status = watchtower_status(channel)
        return status if status["type"] == "SignatureRequired" else False

    return wait_for(read, timeout=45, description="actual external Watchtower request")


class ConfirmedHistory:
    """Follow actual confirmed inputs, never interpret an unknown tx as spent."""

    def __init__(self, env, commitment_hash):
        self.env = env
        confirmed = env.ckb.rpc("get_transaction", [commitment_hash])
        assert confirmed["tx_status"]["status"] == "committed", confirmed
        self.commitment = confirmed["transaction"]
        self.cursor = number(confirmed["tx_status"]["block_number"])
        self.spends = {}
        self.commitment_points = {key(outpoint(commitment_hash))}
        self.lock = self.commitment["outputs"][0]["lock"]

    def is_commitment(self, output):
        lock = output["lock"]
        return (
            lock["code_hash"] == self.lock["code_hash"]
            and lock["hash_type"] == self.lock["hash_type"]
            and lock["args"][:74] == self.lock["args"][:74]
        )

    def refresh(self):
        tip = number(self.env.ckb.rpc("get_tip_block_number"))
        for height in range(self.cursor, tip + 1):
            block = self.env.ckb.rpc("get_block_by_number", [hex(height)])
            for tx in block["transactions"]:
                parents = [key(item["previous_output"]) for item in tx["inputs"]]
                for parent in parents:
                    self.spends[parent] = tx
                if any(parent in self.commitment_points for parent in parents):
                    for index, output in enumerate(tx["outputs"]):
                        if self.is_commitment(output):
                            self.commitment_points.add((tx["hash"], index))
        self.cursor = tip + 1
        return self

    def finished(self):
        self.refresh()
        return all(point in self.spends for point in self.commitment_points)

    def spent(self, point):
        self.refresh()
        return self.spends.get(key(point))


def confirm_commitment(channel, tx_hash=None):
    env = channel.env
    if tx_hash is None:
        channel.node.rpc(
            "shutdown_channel",
            {
                "channel_id": channel.channel_id,
                "force": True,
            },
        )

        def closing_hash():
            env.mine(1)
            return (channel.channel(channel.node, include_closed=True) or {}).get(
                "shutdown_transaction_hash"
            )

        # The latest commitment field is a pre-broadcast template without chain
        # dependencies. The shutdown field is the actual confirmed transaction.
        tx_hash = wait_for(
            closing_hash,
            timeout=45,
            description="Public T confirmed force-close transaction hash",
        )

    def committed():
        tx = env.ckb.rpc("get_transaction", [tx_hash])
        if tx and tx["tx_status"]["status"] == "committed":
            return tx
        env.mine(2)
        return False

    wait_for(committed, timeout=45, description="force-close commitment confirmed")
    tx = transaction(env, tx_hash)
    assert any(
        item["previous_output"] == outpoint(channel.funding_tx_hash)
        for item in tx["inputs"]
    )
    assert tx["outputs"], "confirmed commitment must actually create a cell"
    history = ConfirmedHistory(env, tx_hash)
    # Record independently approved wallet terms before consulting any spend
    # proposal. Settlement fixtures have no off-chain balance changes.
    history.local_entitlement = number(channel.params["funding_amount"])
    history.remote_entitlement = number(
        channel.opening["opening_context"]["remote_amount"]
    )
    fee_template = deepcopy(tx)
    fee_template["cell_deps"] = [{}, {}]  # plain CKB commitment + secp dependencies
    fee_template["header_deps"] = []
    fee_template["outputs"][0]["lock"]["args"] = "0x" + "00" * 58
    fee_template["witnesses"] = ["0x" + "00" * 112]
    history.commitment_fee = (
        number(channel.params["commitment_fee_rate"])
        * transaction_size_in_block(fee_template)
        // 1000
    )
    funding_capacity = number(
        channel.opening["unsigned_funding_tx"]["outputs"][0]["capacity"]
    )
    assert funding_capacity == history.local_entitlement + history.remote_entitlement
    assert (
        number(tx["outputs"][0]["capacity"]) + history.commitment_fee
        == funding_capacity
    )
    return history


def mature_watchtower(channel, history):
    # First prove the commitment exists on chain and is live before expiry.
    assert_live(channel.env, outpoint(history.commitment["hash"]))
    channel.env.mine_epochs(7)
    channel.env.wait_indexer()
    status = pending_watchtower(channel)
    assert_live(
        channel.env, status["content"]["transaction"]["inputs"][0]["previous_output"]
    )
    return status


def capture_submission(channel):
    result = channel.signer.poll(capture=True, allow_error=True)
    assert not result["ok"], "capture must stop before transmitting the signature"
    captured = result["captured"]
    assert len(captured) == 1, captured
    submission = captured[0]
    assert submission["method"] == "submit_watchtower_signature", submission
    assert "submit_watchtower_signature" not in result["rpc_calls"]
    return submission


def assert_payout(env, history, request, destination, expected_tlc_amount=None):
    proposed = request["content"]["transaction"]
    first = proposed["inputs"][0]["previous_output"]
    spend = history.spent(first)
    assert spend, f"No confirmed spend for accepted request {request['request_id']}"
    # Witnesses become signed; every field covered by the signature stays exact.
    for field in RAW_TX_FIELDS:
        assert spend[field] == proposed[field], (field, spend, proposed)
    source = transaction(env, first["tx_hash"])["outputs"][number(first["index"])]
    total_input = number(source["capacity"])
    sponsor_capacity = 0
    for item in spend["inputs"][1:]:
        point = item["previous_output"]
        sponsor = transaction(env, point["tx_hash"])["outputs"][number(point["index"])]
        assert sponsor["lock"] == destination and sponsor.get("type") is None
        sponsor_capacity += number(sponsor["capacity"])
    total_input += sponsor_capacity
    payout_capacity = remainder_capacity = total_output = 0
    for output, data in zip(spend["outputs"], spend["outputs_data"]):
        capacity = number(output["capacity"])
        total_output += capacity
        assert output.get("type") is None and data == "0x"
        if history.is_commitment(output):
            remainder_capacity += capacity
        else:
            assert output["lock"] == destination, "unapproved Watchtower payout"
            payout_capacity += capacity
    fee = total_input - total_output
    assert 0 <= fee <= MAX_FEE
    claim = payout_capacity - sponsor_capacity + fee
    assert claim > 0
    assert claim == number(source["capacity"]) - remainder_capacity
    if expected_tlc_amount is not None:
        assert (
            claim == expected_tlc_amount
        ), "TLC claim must pay the held invoice amount"
    else:
        # Without TLCs or payments, the tenant is owed its approved opening
        # amount. The final claimant alone bears the commitment-fee deficit;
        # settlement fees are paid by the explicitly approved sponsor inputs.
        original = outpoint(history.commitment["hash"])
        expected_claim = history.local_entitlement
        if first != original:
            prior = transaction(env, first["tx_hash"])
            assert prior["inputs"][0]["previous_output"] == original
            assert (
                number(history.commitment["outputs"][0]["capacity"])
                - number(source["capacity"])
            ) == history.remote_entitlement
            expected_claim -= history.commitment_fee
            assert remainder_capacity == 0
        assert (
            claim == expected_claim
        ), "settlement differs from wallet-approved tenant entitlement"
        assert payout_capacity == sponsor_capacity + expected_claim - fee
    assert env.ckb.rpc("get_live_cell", [first, False])["status"] != "live"
    return spend


def settle(
    channel,
    history,
    destination,
    requests=None,
    require_purpose="Settlement",
    tlc_amount=None,
    cold=False,
):
    requests = dict(requests or {})

    def step():
        status = watchtower_status(channel)
        if status["type"] == "SignatureRequired":
            requests[status["request_id"]] = deepcopy(status)
        before = channel.signer.snapshot()
        polled = channel.signer.poll(allow_error=True)
        for observed in polled["watchtower_statuses"]:
            actual = observed["status"]
            if (
                observed["channel_id"] == channel.channel_id
                and actual["type"] == "SignatureRequired"
            ):
                requests[actual["request_id"]] = deepcopy(actual)
        if not polled["ok"]:
            assert "input is not live" in polled.get("error", ""), polled.get("error")
            actual = polled["watchtower_statuses"][-1]["status"]
            spent = [
                item["previous_output"]
                for item in actual["content"]["transaction"]["inputs"]
                if history.spent(item["previous_output"])
            ]
            assert (
                spent
            ), "a stale-input refusal must have independent confirmed-spend evidence"
            assert channel.signer.snapshot() == before
            assert not polled["submission_results"]
            assert "submit_watchtower_signature" not in polled["rpc_calls"]
            assert not polled["captured"]
            (channel.env.path / "watchtower-stale-request.json").write_text(
                json.dumps(
                    {
                        "request_id": actual["request_id"],
                        "spent_inputs": spent,
                        "sdk_error": polled["error"],
                    },
                    indent=2,
                )
            )
        channel.env.mine(2)
        if cold:
            state = channel.tenant.status()  # Operator read: must not activate tenant.
            assert state["runtime_status"] == "cold"
            assert state["private_channel_id"] == channel.channel_id
        receipts = channel.signer.status()["watchtower_submissions"].get(
            channel.channel_id, []
        )
        return (
            receipts
            if history.finished()
            and any(item["key_purpose"]["type"] == require_purpose for item in receipts)
            else False
        )

    receipts = wait_for(
        step,
        timeout=90,
        description="SDK-accepted Watchtower signatures and confirmed settlement",
    )
    required = [
        item for item in receipts if item["key_purpose"]["type"] == require_purpose
    ]
    assert required
    # A receipt alone is insufficient; link at least one accepted signature to
    # the actual transaction and its exact payout, fee and funding lineage.
    matched = []
    for receipt in required:
        request = requests.get(receipt["request_id"])
        if request is None:
            continue
        proposed = request["content"]["transaction"]
        actual = history.spent(proposed["inputs"][0]["previous_output"])
        # Two valid claims can race for the original commitment. An accepted
        # but unmined signature is not evidence of payout; require its exact raw
        # transaction to appear on chain, and keep driving the successor claim.
        if actual and same_raw_transaction(actual, proposed):
            matched.append(receipt)
    assert matched, (receipts, list(requests))
    for item in matched:
        assert_payout(
            channel.env,
            history,
            requests[item["request_id"]],
            destination,
            tlc_amount if require_purpose == "Tlc" else None,
        )
    assert all(point in history.spends for point in history.commitment_points)
    return receipts


# TEST-MAP: LSP-043
def test_force_close_waits_for_real_settlement_signature(lsp_env):
    with ExternalChannel(lsp_env) as channel:
        channel.create()
        destination = authorize_watchtower(channel)
        history = confirm_commitment(channel)
        request = mature_watchtower(channel, history)
        assert request["content"]["key_purpose"]["type"] == "Settlement"
        assert (
            channel.signer.status()["watchtower_submissions"].get(
                channel.channel_id, []
            )
            == []
        )
        settle(channel, history, destination, {request["request_id"]: request})


# TEST-MAP: LSP-044
def test_watchtower_rejects_bad_signatures_and_exact_replay_is_idempotent(lsp_env):
    with ExternalChannel(lsp_env) as channel:
        channel.create()
        destination = authorize_watchtower(channel)
        history = confirm_commitment(channel)
        request = mature_watchtower(channel, history)
        valid = capture_submission(channel)
        assert valid["params"]["request_id"] == request["request_id"]
        original_store = channel.signer.snapshot()
        wrong_key = PrivateKey().sign_recoverable(secrets.token_bytes(32), hasher=None)
        variants = [
            {"request_id": "0x" + secrets.token_hex(32)},
            {"signature": "0x" + "00" * 64},
            {"signature": "0x" + wrong_key.hex()},
        ]
        for change in variants:
            malformed = deepcopy(valid["params"])
            malformed.update(change)
            with pytest.raises(RpcError):
                channel.tenant.rpc("submit_watchtower_signature", malformed)
            assert watchtower_status(channel) == request
            assert channel.signer.snapshot() == original_store
        applied = channel.signer.poll()
        assert {
            "method": "submit_watchtower_signature",
            "result": {"type": "Applied"},
        } in applied["submission_results"]
        receipts = channel.signer.status()["watchtower_submissions"][channel.channel_id]
        assert any(item["request_id"] == request["request_id"] for item in receipts)
        assert channel.tenant.rpc("submit_watchtower_signature", valid["params"]) == {
            "type": "AlreadyApplied"
        }
        changed = deepcopy(valid["params"])
        signature = bytearray.fromhex(changed["signature"][2:])
        signature[0] ^= 1
        changed["signature"] = "0x" + signature.hex()
        with pytest.raises(RpcError):
            channel.tenant.rpc("submit_watchtower_signature", changed)
        # Receipt recording is exercised through genuine Agent polling. Its
        # cached signature must survive restarting the complete wallet store.
        channel.signer.restart()
        lsp_env.public.restart()
        settle(channel, history, destination, {request["request_id"]: request})


# TEST-MAP: LSP-045
def test_watchtower_namespace_and_secret_key_registration(lsp_env):
    with ExternalChannel(lsp_env) as owner:
        owner.create()
        destination = authorize_watchtower(owner)
        outsider = lsp_env.register()
        history = confirm_commitment(owner)
        request = mature_watchtower(owner, history)
        valid = capture_submission(owner)
        before = owner.signer.snapshot()
        with pytest.raises(RpcError):
            outsider.rpc(
                "get_watchtower_signing_status", {"channel_id": owner.channel_id}
            )
        with pytest.raises(RpcError):
            outsider.rpc("submit_watchtower_signature", valid["params"])
        assert watchtower_status(owner) == request
        assert owner.signer.snapshot() == before

        secret = PrivateKey()
        public_key = secret.public_key.format().hex()
        registration = {
            "channel_id": "0x" + secrets.token_hex(32),
            "funding_udt_type_script": None,
            "local_settlement_key": secret.secret.hex(),
            "local_settlement_key_pubkey": public_key,
            "remote_settlement_key": PrivateKey().public_key.format().hex(),
            "local_funding_pubkey": PrivateKey().public_key.format().hex(),
            "remote_funding_pubkey": PrivateKey().public_key.format().hex(),
            "settlement_data": {
                "local_amount": "0x0",
                "remote_amount": "0x0",
                "tlcs": [],
            },
        }
        with pytest.raises(RpcError):
            outsider.rpc("create_watch_channel", registration)
        with pytest.raises(RpcError):
            outsider.rpc(
                "get_watchtower_signing_status",
                {"channel_id": registration["channel_id"]},
            )
        registration.pop("local_settlement_key")
        outsider.rpc("create_watch_channel", registration)
        assert outsider.rpc(
            "get_watchtower_signing_status",
            {
                "channel_id": registration["channel_id"],
            },
        )["status"] == {"type": "NoSignatureRequired"}
        with pytest.raises(RpcError):
            owner.tenant.rpc(
                "get_watchtower_signing_status",
                {"channel_id": registration["channel_id"]},
            )
        assert watchtower_status(owner) == request
        settle(owner, history, destination, {request["request_id"]: request})


# TEST-MAP: LSP-046
def test_cold_tenant_keeps_watchtower_protection(lsp_env):
    with ExternalChannel(lsp_env) as channel:
        channel.create()
        destination = authorize_watchtower(channel)
        channel.tenant.evict()
        wait_for(
            lambda: channel.tenant.status()["runtime_status"] == "cold",
            description="idle tenant evicted",
        )
        assert channel.tenant.status()["private_channel_id"] == channel.channel_id
        history = confirm_commitment(channel)
        request = mature_watchtower(channel, history)
        assert channel.tenant.status()["runtime_status"] == "cold"
        settle(
            channel, history, destination, {request["request_id"]: request}, cold=True
        )


def receive_hold(channel, verify_missing_revocation=False):
    env = channel.env
    env.open_channel(env.payer, channel.node, public=True)
    invoice = channel.hold_invoice()
    env.payer.rpc(
        "send_payment",
        {
            "invoice": invoice["invoice"],
            "max_fee_amount": hex(10_000),
            "tlc_expiry_limit": hex(7 * 86_400_000),
        },
    )
    rejected_missing_evidence = False

    def received():
        nonlocal rejected_missing_evidence
        status = channel.signer.signing_status(channel.channel_id)
        if (
            verify_missing_revocation
            and not rejected_missing_evidence
            and status.get("transition") == "CompleteReceivedRevokeAndAck"
        ):
            assert status["session_evidence"]["peer_partial_signature"]
            incomplete = deepcopy(status)
            incomplete["session_evidence"]["peer_partial_signature"] = None
            before = channel.signer.snapshot()
            rejected = channel.signer.raw(
                "prepare", channel_id=channel.channel_id, status=incomplete
            )
            assert "error" in rejected, "revocation must require the peer's evidence"
            assert channel.signer.snapshot() == before
            assert (
                "submit_channel_signature" not in rejected["observations"]["rpc_calls"]
            )
            assert channel.signer.signing_status(channel.channel_id) == status
            rejected_missing_evidence = True
        channel.signer.poll()
        env.mine(2)
        invoice_status = channel.tenant.rpc(
            "get_invoice",
            {
                "payment_hash": invoice["payment_hash"],
            },
        )["status"]
        assert invoice_status != "Paid", "hold invoice must not settle off chain"
        return invoice_status == "Received" and (
            not verify_missing_revocation or rejected_missing_evidence
        )

    wait_for(
        received,
        timeout=90,
        description="real held TLC with SDK-verified commitment/revocation",
    )

    channel.drive(
        lambda: channel.recoverable(invoice["payment_hash"]),
        "SDK latest recoverable commitment protects held invoice",
    )
    return invoice


# TEST-MAP: LSP-047
def test_hold_preimage_releases_before_close_and_uses_tlc_key(lsp_env):
    with ExternalChannel(lsp_env) as channel:
        channel.create()
        destination = authorize_watchtower(channel)
        invoice = receive_hold(channel)
        assert_live(lsp_env, outpoint(channel.funding_tx_hash))
        released = channel.release(invoice, target="Watchtower")
        assert released["authorized"]
        assert "create_preimage" in channel.signer.last["observations"]["rpc_calls"]
        assert "settle_invoice" not in channel.signer.last["observations"]["rpc_calls"]
        assert (
            channel.tenant.rpc(
                "get_invoice", {"payment_hash": invoice["payment_hash"]}
            )["status"]
            == "Received"
        )
        assert_live(lsp_env, outpoint(channel.funding_tx_hash))
        history = confirm_commitment(channel)
        request = mature_watchtower(channel, history)
        settle(
            channel,
            history,
            destination,
            {request["request_id"]: request},
            require_purpose="Tlc",
            tlc_amount=invoice["amount"],
        )


# TEST-MAP: LSP-048
def test_revoked_peer_commitment_is_confirmed_then_penalized(lsp_env):
    with ExternalChannel(lsp_env) as channel:
        channel.create()
        invoice = receive_hold(channel, verify_missing_revocation=True)
        assert channel.release(invoice, target="Invoice")["authorized"]
        channel.drive(
            lambda: lsp_env.payer.rpc(
                "get_payment",
                {
                    "payment_hash": invoice["payment_hash"],
                },
            )["status"]
            == "Success",
            "incoming payment settled through real SDK",
        )

        def evidence():
            records = channel.signer.status()["revoked_commitments"].get(
                channel.channel_id, []
            )
            return next(
                (
                    record
                    for record in records
                    if record["for_remote"] and number(record["version"]) == 1
                ),
                None,
            )

        revoked = channel.drive(
            evidence, "complete SDK-verified peer revocation evidence"
        )
        result = channel.node.rpc(
            "submit_commitment_transaction",
            {
                "channel_id": channel.channel_id,
                "commitment_number": "0x1",
            },
        )
        history = confirm_commitment(channel, result["tx_hash"])
        original = outpoint(result["tx_hash"])
        # SDK reference hashes describe the signed template; broadcasting adds
        # the independently known funding-contract dependencies.
        verified = channel.signer.command(
            "verify_commitment_lineage",
            channel_id=channel.channel_id,
            source=revoked,
            outpoint=original,
        )
        assert verified["verified"]
        assert (
            int.from_bytes(
                bytes.fromhex(history.commitment["outputs"][0]["lock"]["args"][2:])[
                    28:36
                ],
                "big",
            )
            == 1
        )

        # No external signing is permitted here: punishment must use the complete
        # aggregate revocation evidence obtained from the real prior protocol.
        def punished():
            lsp_env.mine(2)
            return history.spent(original)

        spend = wait_for(
            punished, timeout=60, description="confirmed old-commitment punishment"
        )
        assert spend["inputs"][0]["previous_output"] == original
        payout = spend["outputs"][0]
        assert payout["lock"] == channel.funding_script
        assert payout["type"] is None and spend["outputs_data"][0] == "0x"
        assert number(payout["capacity"]) == number(
            history.commitment["outputs"][0]["capacity"]
        )
        assert lsp_env.ckb.rpc("get_live_cell", [original, False])["status"] != "live"
        input_capacity = sum(
            number(
                transaction(lsp_env, item["previous_output"]["tx_hash"])["outputs"][
                    number(item["previous_output"]["index"])
                ]["capacity"]
            )
            for item in spend["inputs"]
        )
        output_capacity = sum(number(output["capacity"]) for output in spend["outputs"])
        assert 0 <= input_capacity - output_capacity <= MAX_FEE
        sponsor_lock = channel.opening["opening_context"]["remote_shutdown_script"]
        assert all(output["lock"] == sponsor_lock for output in spend["outputs"][1:])
        assert history.finished()


# TEST-MAP: LSP-051
@pytest.mark.parametrize("mutation", ["payout", "fee", "sponsor"])
def test_sdk_rejects_hostile_watchtower_transaction_before_signing(lsp_env, mutation):
    with ExternalChannel(lsp_env) as channel:
        channel.create()
        destination = authorize_watchtower(channel)
        history = confirm_commitment(channel)
        request = mature_watchtower(channel, history)
        approved = channel.signer.command(
            "watchtower_authorization", channel_id=channel.channel_id, status=request
        )
        changed = deepcopy(request)
        tx = changed["content"]["transaction"]
        destination_index = next(
            i for i, output in enumerate(tx["outputs"]) if output["lock"] == destination
        )
        if mutation == "payout":
            tx["outputs"][destination_index]["lock"] = lsp_env.new_wallet()[
                "lock_script"
            ]
        elif mutation == "fee":
            output = tx["outputs"][destination_index]
            output["capacity"] = hex(number(output["capacity"]) - MAX_FEE - 1)
        else:
            # Actual live sponsor cell from a different independently funded key.
            wallet = lsp_env.new_wallet()
            lsp_env.wait_indexer()
            cells = lsp_env.ckb.rpc(
                "get_cells",
                [
                    {
                        "script": wallet["lock_script"],
                        "script_type": "lock",
                        "script_search_mode": "exact",
                    },
                    "asc",
                    "0x1",
                ],
            )
            assert cells["objects"]
            tx["inputs"].append(
                {"since": "0x0", "previous_output": cells["objects"][0]["out_point"]}
            )
            tx["witnesses"].append("0x")
        before = channel.signer.snapshot()
        rejected = channel.signer.raw(
            "watchtower_prepare",
            channel_id=channel.channel_id,
            status=changed,
            authorization=approved["authorization"],
        )
        assert "error" in rejected, rejected
        assert not rejected["observations"]["captured"]
        assert (
            "submit_watchtower_signature" not in rejected["observations"]["rpc_calls"]
        )
        assert channel.signer.snapshot() == before
        assert watchtower_status(channel) == request
        settle(channel, history, destination, {request["request_id"]: request})


# TEST-MAP: LSP-051
def test_sdk_confirm_rechecks_actual_live_input_after_prepare(lsp_env):
    with ExternalChannel(lsp_env) as channel:
        channel.create()
        authorize_watchtower(channel)
        history = confirm_commitment(channel)
        request = mature_watchtower(channel, history)
        # Capture first so the subsequent prepare sees the final SDK revision.
        # Raw submission below cannot stale it by mutating local signer state.
        valid = capture_submission(channel)
        approved = channel.signer.command(
            "watchtower_authorization", channel_id=channel.channel_id, status=request
        )
        prepared = channel.signer.command(
            "watchtower_prepare",
            channel_id=channel.channel_id,
            status=request,
            authorization=approved["authorization"],
        )
        assert prepared["outcome"] == "NeedConfirmation"
        before = channel.signer.snapshot()
        assert channel.tenant.rpc("submit_watchtower_signature", valid["params"]) == {
            "type": "Applied"
        }
        first = request["content"]["transaction"]["inputs"][0]["previous_output"]

        def consumed():
            lsp_env.mine(2)
            return history.spent(first)

        wait_for(
            consumed,
            timeout=60,
            description="prepared input spent by legitimate signed transaction",
        )
        assert channel.signer.snapshot() == before
        assert lsp_env.ckb.rpc("get_live_cell", [first, False])["status"] != "live"
        rejected = channel.signer.raw("confirm", prepared_id=prepared["prepared_id"])
        assert "error" in rejected, rejected
        assert (
            "live" in rejected["error"].lower()
        ), "must reject fresh chain evidence, not SDK revision"
        assert channel.signer.snapshot() == before
        assert (
            "submit_watchtower_signature" not in rejected["observations"]["rpc_calls"]
        )
        assert not rejected["observations"]["captured"]
