"""Pinned production SDK against real hosted LSP, chain and ordinary peers."""

from copy import deepcopy
import hashlib
import json
import secrets
import time

import pytest

from framework.lsp_devnet import CKB, RpcError, wait_for
from framework.lsp_signer import (
    ExternalChannel,
    SdkError,
    number,
    payment_terms,
    transaction_size_in_block,
)


def prepared_submission(channel, status=None, policy="Manual"):
    args = {"channel_id": channel.channel_id, "policy": policy}
    if status is not None:
        args["status"] = status
    prepared = channel.signer.command("prepare", **args)
    if prepared["outcome"] == "NeedConfirmation":
        return channel.signer.command("confirm", prepared_id=prepared["prepared_id"])[
            "submission"
        ]
    assert prepared["outcome"] == "ReadyToSubmit", prepared
    return prepared["submission"]


def all_channels(client):
    return client.rpc("list_channels", {})["channels"]


def balances(client):
    return {c["channel_id"]: number(c["local_balance"]) for c in all_channels(client)}


def pending_tlcs(client):
    return [tlc for c in all_channels(client) for tlc in c["pending_tlcs"]]


def assert_funding_inputs_live(channel):
    for tx_input in channel.opening["unsigned_funding_tx"]["inputs"]:
        assert (
            channel.env.ckb.rpc("get_live_cell", [tx_input["previous_output"], False])[
                "status"
            ]
            == "live"
        )


def start_inbound(channel, amount=1_000_000):
    env = channel.env
    env.open_channel(env.payer, channel.node, public=True)
    invoice = channel.hold_invoice(amount)
    before = (balances(env.payer), balances(channel.tenant))
    env.payer.rpc(
        "send_payment",
        {
            "invoice": invoice["invoice"],
            "max_fee_amount": hex(2_000_000),
            "tlc_expiry_limit": hex(7 * 86_400_000),
        },
    )
    return invoice, before


def invoice_status(channel, invoice):
    return channel.tenant.rpc("get_invoice", {"payment_hash": invoice["payment_hash"]})[
        "status"
    ]


def receive_hold(channel, invoice):
    channel.drive(
        lambda: invoice_status(channel, invoice) == "Received"
        and channel.recoverable(invoice["payment_hash"])
        and channel.signer.signing_status(channel.channel_id)["type"]
        == "NoSignatureRequired",
        "hold invoice irrevocably received",
        mine=False,
    )


def complete_inbound(channel, invoice, before):
    env = channel.env

    def settled():
        payment = env.payer.rpc(
            "get_payment", {"payment_hash": invoice["payment_hash"]}
        )
        return (
            payment["status"] == "Success"
            and invoice_status(channel, invoice) == "Paid"
            and not pending_tlcs(env.payer)
            and not pending_tlcs(channel.tenant)
        )

    channel.drive(
        settled, "payer Success, receiver Paid and both TLC sets drained", mine=False
    )
    payment = env.payer.rpc("get_payment", {"payment_hash": invoice["payment_hash"]})
    assert payment["payment_preimage"] == invoice["preimage"]
    assert env.wait_delivery(
        invoice["payment_hash"], "succeeded", public_node=channel.node
    )
    debit = sum(before[0].values()) - sum(balances(env.payer).values())
    credit = sum(balances(channel.tenant).values()) - sum(before[1].values())
    assert credit == invoice["amount"]
    assert debit == invoice["amount"] + number(payment["fee"])
    assert 0 <= number(payment["fee"]) <= 2_000_000
    assert (
        channel.signer.signing_status(channel.channel_id)["type"]
        == "NoSignatureRequired"
    )


# TEST-MAP: LSP-013
# TEST-MAP: LSP-015
# TEST-MAP: LSP-016
def test_external_opening_exact_retry_and_single_channel(lsp_devnet):
    with ExternalChannel(lsp_devnet) as channel:
        original = channel.open()
        assert_funding_inputs_live(channel)
        assert channel.tenant.rpc("open_tenant_channel", channel.params) == original
        channel.signer.restart()  # Caller connection is gone; complete signer store remains.
        assert channel.signer.command("open", params=channel.params) == original
        for field, value in (
            ("funding_amount", hex(501 * CKB)),
            ("shutdown_script", lsp_devnet.new_wallet()["lock_script"]),
            ("funding_lock_script", lsp_devnet.new_wallet()["lock_script"]),
        ):
            changed = deepcopy(channel.params)
            changed[field] = value
            with pytest.raises(RpcError):
                channel.tenant.rpc("open_tenant_channel", changed)
            assert channel.tenant.rpc("open_tenant_channel", channel.params) == original
            assert_funding_inputs_live(channel)
        channel.fund()
        channel.ready()
        with pytest.raises(RpcError):
            channel.tenant.rpc("open_tenant_channel", channel.params)
        assert len(all_channels(channel.tenant)) == 1
        assert channel.tenant.status()["private_channel_id"] == original["channel_id"]


# TEST-MAP: LSP-014
@pytest.mark.parametrize(
    "invalid", ["peer", "public", "absent_public", "missing_signer", "operator"]
)
def test_external_opening_scope_rejects_then_valid_request_works(lsp_devnet, invalid):
    with ExternalChannel(lsp_devnet) as channel:
        params = deepcopy(channel.params)
        client = channel.tenant
        if invalid == "peer":
            params["pubkey"] = lsp_devnet.payer.pubkey
        elif invalid == "public":
            params["public"] = True
        elif invalid == "absent_public":
            params.pop("public")
        elif invalid == "missing_signer":
            params.pop("external_channel_signer")
        else:
            client = lsp_devnet.public
        before = all_channels(lsp_devnet.public)
        with pytest.raises(RpcError):
            client.rpc("open_tenant_channel", params)
        assert all_channels(channel.tenant) == []
        assert all_channels(lsp_devnet.public) == before
        channel.create()


# TEST-MAP: LSP-017
def test_external_opening_survives_host_restart(lsp_devnet):
    with ExternalChannel(lsp_devnet) as channel:
        opening = channel.open()
        identity = channel.tenant.tenant_id
        lsp_devnet.restart(lsp_devnet.public)
        assert channel.signer.command("open", params=channel.params) == opening
        assert channel.tenant.tenant_id == identity
        assert_funding_inputs_live(channel)
        channel.fund()
        channel.ready()


# TEST-MAP: LSP-018
@pytest.mark.parametrize(
    "mutation", ["outputs", "amount", "lock", "input", "cell_deps"]
)
def test_tampered_signed_funding_rejected_without_poisoning_opening(
    lsp_devnet, mutation
):
    with ExternalChannel(lsp_devnet) as channel:
        channel.open()
        signed = channel.signed_funding()
        changed = deepcopy(signed)
        if mutation == "outputs":
            changed["outputs"].append(deepcopy(changed["outputs"][0]))
            changed["outputs_data"].append("0x")
        elif mutation == "amount":
            changed["outputs"][0]["capacity"] = hex(
                number(changed["outputs"][0]["capacity"]) + 1
            )
        elif mutation == "lock":
            changed["outputs"][0]["lock"] = lsp_devnet.new_wallet()["lock_script"]
        elif mutation == "input":
            changed["inputs"][0]["previous_output"]["index"] = "0xffff"
        else:
            changed["cell_deps"] = []
        with pytest.raises(RpcError, match="raw data mismatch"):
            channel.fund(changed)
        lsp_devnet.mine(2)
        assert_funding_inputs_live(channel)
        assert (
            channel.tenant.rpc("open_tenant_channel", channel.params) == channel.opening
        )
        channel.fund(signed)
        channel.ready()


# TEST-MAP: LSP-007
# TEST-MAP: LSP-035
# TEST-MAP: LSP-036
def test_invalid_channel_signatures_preserve_request_and_receipt_is_exact(lsp_devnet):
    with ExternalChannel(lsp_devnet) as channel:
        channel.open()
        channel.fund()
        original = channel.pending()
        good = prepared_submission(channel, original)
        assert good["params"]["next_material"]
        intruder = lsp_devnet.register()
        with pytest.raises(RpcError):
            intruder.rpc("submit_channel_signature", good["params"])
        assert channel.signer.signing_status(channel.channel_id) == original
        variants = []
        for field, value in (
            ("request_id", "0x" + "ee" * 32),
            ("partial_signature", "0x" + "01" * 32),
            ("next_material", None),
        ):
            bad = deepcopy(good)
            bad["params"][field] = value
            variants.append(bad)
        for field in (
            "next_commitment_point",
            "next_commitment_nonce",
            "next_revocation_nonce",
        ):
            bad = deepcopy(good)
            bad["params"]["next_material"][field] = None
            variants.append(bad)
        bad = deepcopy(good)
        bad["params"]["next_material"][
            "next_commitment_point"
        ] = lsp_devnet.public.pubkey
        variants.append(bad)
        bad = deepcopy(good)
        bad["params"]["next_material"]["next_revocation_nonce"] = bad["params"][
            "next_material"
        ]["next_commitment_nonce"]
        variants.append(bad)
        for bad in variants:
            with pytest.raises(SdkError):
                channel.signer.command("submit", submission=bad)
            assert channel.signer.signing_status(channel.channel_id) == original
        assert channel.signer.command("submit", submission=good)["type"] == "Applied"
        assert (
            channel.signer.command("submit", submission=good)["type"]
            == "AlreadyApplied"
        )
        for change in ("partial_signature", "next_material"):
            bad = deepcopy(good)
            if change == "partial_signature":
                bad["params"][change] = "0x" + "01" * 32
            else:
                bad["params"][change][
                    "next_commitment_point"
                ] = lsp_devnet.public.pubkey
            with pytest.raises(SdkError):
                channel.signer.command("submit", submission=bad)
        channel.ready()


# TEST-MAP: LSP-037
def test_cold_pending_opening_query_stays_cold_and_submission_resumes(lsp_devnet):
    with ExternalChannel(lsp_devnet) as channel:
        channel.open()
        channel.fund()
        pending = channel.pending()
        channel.tenant.evict()
        assert channel.tenant.status()["runtime_status"] == "cold"
        assert channel.signer.signing_status(channel.channel_id) == pending
        assert channel.tenant.status()["runtime_status"] == "cold"
        submission = prepared_submission(channel, pending)
        assert channel.tenant.status()["runtime_status"] == "cold"
        assert (
            channel.signer.command("submit", submission=submission)["type"] == "Applied"
        )
        wait_for(
            lambda: channel.tenant.status()["runtime_status"] == "active",
            description="submit reactivates tenant",
        )
        channel.ready()


# TEST-MAP: LSP-038
@pytest.mark.parametrize("restart", ["node", "signer"])
@pytest.mark.parametrize("phase", ["commitment", "revocation"])
def test_inbound_pending_signature_survives_separate_restart(
    lsp_devnet, restart, phase
):
    with ExternalChannel(lsp_devnet) as channel:
        channel.create()
        invoice, before = start_inbound(channel)
        transitions = (
            {"SendCommitmentSigned", "CompleteReceivedCommitment"}
            if phase == "commitment"
            else {"SendRevokeAndAck", "CompleteReceivedRevokeAndAck"}
        )

        def pending_phase():
            status = channel.signer.signing_status(channel.channel_id)
            if (
                status["type"] == "SignatureRequired"
                and status["transition"] in transitions
            ):
                return status
            channel.signer.poll()
            return False

        pending = wait_for(
            pending_phase,
            timeout=45,
            description=f"real {phase} request before {restart} restart",
        )
        identity = channel.signer.status()
        snapshot = channel.signer.snapshot()
        if restart == "node":
            lsp_devnet.restart(channel.node)
        else:
            channel.signer.restart()
            assert channel.signer.snapshot() == snapshot
        restored = channel.signer.status()
        for field in ("tenant_id", "root_signer_pubkey", "bound_channel_ids"):
            assert restored[field] == identity[field]
        assert channel.signer.signing_status(channel.channel_id) == pending
        receive_hold(channel, invoice)
        channel.release(invoice)
        complete_inbound(channel, invoice, before)


# TEST-MAP: LSP-041
def test_preimage_stays_local_until_sdk_recovery_evidence_is_complete(lsp_devnet):
    with ExternalChannel(lsp_devnet) as channel:
        channel.create()
        invoice, before = start_inbound(channel)
        channel.pending()
        with pytest.raises(SdkError):
            channel.release(invoice)
        assert not {"settle_invoice", "create_preimage"} & set(
            channel.signer.last["observations"]["rpc_calls"]
        )
        receive_hold(channel, invoice)
        with pytest.raises(SdkError):
            channel.release(invoice, preimage="0x" + "ff" * 32)
        assert not {"settle_invoice", "create_preimage"} & set(
            channel.signer.last["observations"]["rpc_calls"]
        )
        assert (
            lsp_devnet.payer.rpc(
                "get_payment", {"payment_hash": invoice["payment_hash"]}
            ).get("payment_preimage")
            is None
        )
        channel.release(invoice)
        assert "settle_invoice" in channel.signer.last["observations"]["rpc_calls"]
        complete_inbound(channel, invoice, before)


# TEST-MAP: LSP-042
def test_restarted_sdk_cooperative_close_pays_exact_approved_outputs(lsp_devnet):
    with ExternalChannel(lsp_devnet) as channel:
        channel.create()
        before = channel.signer.snapshot()
        channel.signer.restart()
        assert channel.signer.snapshot() == before
        context = channel.opening["opening_context"]
        remote = context["remote_shutdown_script"]
        channel.signer.authorize(
            {
                "type": "Close",
                "channel_id": channel.channel_id,
                "local_script": channel.funding_script,
                "remote_script": remote,
                "local_fee_rate": 1000,
                "remote_fee_rate": 0,
            }
        )
        channel.tenant.rpc(
            "shutdown_channel",
            {
                "channel_id": channel.channel_id,
                "close_script": channel.funding_script,
                "fee_rate": "0x3e8",
                "force": False,
            },
        )

        def closed():
            return all(
                (c := channel.channel(client, include_closed=True))
                and c["state"]["state_name"] == "Closed"
                for client in (channel.tenant, channel.node)
            )

        channel.drive(closed, "both endpoints cooperatively Closed")
        row = channel.channel(include_closed=True)
        result = lsp_devnet.ckb.rpc(
            "get_transaction", [row["shutdown_transaction_hash"]]
        )
        assert result["tx_status"]["status"] == "committed"
        tx = result["transaction"]
        assert tx["inputs"][0]["previous_output"] == {
            "tx_hash": channel.funding_tx_hash,
            "index": "0x0",
        }
        assert len(tx["outputs"]) == 2 and all(
            o.get("type") is None for o in tx["outputs"]
        )

        def capacity_at(script):
            return sum(
                number(o["capacity"]) for o in tx["outputs"] if o["lock"] == script
            )

        fee = transaction_size_in_block(tx)
        assert (
            capacity_at(channel.funding_script) == number(context["local_amount"]) - fee
        )
        assert capacity_at(remote) == number(context["remote_amount"])
        assert sum(number(o["capacity"]) for o in tx["outputs"]) + fee == number(
            channel.opening["unsigned_funding_tx"]["outputs"][0]["capacity"]
        )
        assert (
            not row["pending_tlcs"]
            and not channel.channel(channel.node, include_closed=True)["pending_tlcs"]
        )


# TEST-MAP: LSP-039
def test_manual_and_auto_policy_distinguish_opening_inbound_and_outbound(lsp_devnet):
    with ExternalChannel(lsp_devnet) as channel:
        channel.open()
        channel.fund()
        opening = channel.pending()
        for policy in ("Manual", "Auto"):
            assert (
                channel.signer.command(
                    "prepare",
                    channel_id=channel.channel_id,
                    status=opening,
                    policy=policy,
                )["outcome"]
                == "NeedConfirmation"
            )
        channel.ready()
        invoice, before = start_inbound(channel)
        inbound = channel.pending(
            {"SendCommitmentSigned", "CompleteReceivedCommitment"}
        )
        assert inbound["settlement"]["tlcs"]
        assert all(t["inbound"] for t in inbound["settlement"]["tlcs"])
        args = bytes.fromhex(
            inbound["content"]["content"]["transaction"]["outputs"][0]["lock"]["args"][
                2:
            ]
        )
        assert int.from_bytes(args[28:36], "big") > 1
        assert (
            channel.signer.command(
                "prepare",
                channel_id=channel.channel_id,
                status=inbound,
                policy="Manual",
            )["outcome"]
            == "NeedConfirmation"
        )
        automatic = channel.signer.command(
            "prepare", channel_id=channel.channel_id, status=inbound, policy="Auto"
        )
        assert automatic["outcome"] == "ReadyToSubmit"
        assert automatic["submission"]["params"]["request_id"] == inbound["request_id"]
        assert (
            channel.signer.command("submit", submission=automatic["submission"])["type"]
            == "Applied"
        )
        receive_hold(channel, invoice)
        channel.release(invoice)
        complete_inbound(channel, invoice, before)

        # The public peer itself is the receiver: no route-fee guess is needed.
        preimage = "0x" + secrets.token_hex(32)
        payment_hash = "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
        amount = 1_000_000
        remote = channel.node.rpc(
            "new_invoice",
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(2 * 86_400_000),
                "expiry": hex(3600),
            },
        )
        channel.signer.authorize(
            {
                "type": "Outbound",
                "channel_id": channel.channel_id,
                "terms": payment_terms(payment_hash, amount, inbound=False),
            }
        )
        channel.tenant.rpc(
            "send_payment",
            {
                "invoice": remote["invoice_address"],
                "max_fee_amount": "0x0",
                "tlc_expiry_limit": hex(7 * 86_400_000),
            },
        )
        outbound = channel.pending(
            {"SendCommitmentSigned", "CompleteReceivedCommitment"}
        )
        assert any(
            not t["inbound"] and t["payment_hash"] == payment_hash
            for t in outbound["settlement"]["tlcs"]
        )
        for policy in ("Manual", "Auto"):
            assert (
                channel.signer.command(
                    "prepare",
                    channel_id=channel.channel_id,
                    status=outbound,
                    policy=policy,
                )["outcome"]
                == "NeedConfirmation"
            )
        assert channel.signer.signing_status(channel.channel_id) == outbound


def unapproved_hold(channel, amount=1_000_000, authorization_lifetime_ms=3_600_000):
    preimage = "0x" + secrets.token_hex(32)
    payment_hash = "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
    terms = payment_terms(payment_hash, amount)
    terms["expires_at_ms"] = int(time.time() * 1000) + authorization_lifetime_ms
    response = channel.tenant.rpc(
        "new_invoice",
        {
            "amount": hex(amount),
            "currency": "Fibd",
            "payment_hash": payment_hash,
            "hash_algorithm": "sha256",
            "final_expiry_delta": hex(2 * 86_400_000),
            "expiry": hex(3600),
            "allow_mpp": False,
            "allow_trampoline_routing": True,
        },
    )
    return {
        "invoice": response["invoice_address"],
        "payment_hash": payment_hash,
        "preimage": preimage,
        "amount": amount,
        "terms": terms,
    }


def approve_invoice(channel, invoice):
    channel.signer.authorize(
        {
            "type": "Invoice",
            "channel_id": channel.channel_id,
            "terms": invoice["terms"],
            "preimage": invoice["preimage"],
        }
    )


def start_unapproved_inbound(channel, invoice):
    before = (balances(channel.env.payer), balances(channel.tenant))
    channel.env.payer.rpc(
        "send_payment",
        {
            "invoice": invoice["invoice"],
            "max_fee_amount": hex(2_000_000),
            "tlc_expiry_limit": hex(7 * 86_400_000),
        },
    )
    return before


def assert_prepare_refused_unchanged(channel, status, policy):
    before = channel.signer.snapshot()
    response = channel.signer.raw(
        "prepare", channel_id=channel.channel_id, status=status, policy=policy
    )
    assert (
        "error" in response or response.get("result", {}).get("outcome") == "Denied"
    ), response
    assert "submit_channel_signature" not in response["observations"]["rpc_calls"]
    assert channel.signer.snapshot() == before


# TEST-MAP: LSP-040
def test_mandatory_sdk_validation_rejects_unapproved_and_mutated_commitment(lsp_devnet):
    with ExternalChannel(lsp_devnet) as channel:
        channel.create()
        lsp_devnet.open_channel(lsp_devnet.payer, channel.node, public=True)
        invoice = unapproved_hold(channel)
        before = start_unapproved_inbound(channel, invoice)
        status = channel.pending({"SendCommitmentSigned", "CompleteReceivedCommitment"})
        assert status["settlement"]["tlcs"]
        for policy in ("Manual", "Auto"):
            assert_prepare_refused_unchanged(channel, status, policy)
        approve_invoice(channel, invoice)
        valid = channel.signer.command(
            "prepare", channel_id=channel.channel_id, status=status, policy="Manual"
        )
        assert valid["outcome"] == "NeedConfirmation"
        mutations = []
        changed = deepcopy(status)
        changed["settlement"]["local_amount"] = hex(
            number(changed["settlement"]["local_amount"]) + 1
        )
        mutations.append(changed)
        changed = deepcopy(status)
        changed["settlement"]["tlcs"][0]["local_key_pubkey"] = lsp_devnet.public.pubkey
        mutations.append(changed)
        changed = deepcopy(status)
        changed["settlement"]["tlcs"][0]["inbound"] = not changed["settlement"]["tlcs"][
            0
        ]["inbound"]
        mutations.append(changed)
        changed = deepcopy(status)
        changed["settlement"] = None
        mutations.append(changed)
        changed = deepcopy(status)
        changed["session_evidence"]["peer_public_nonce"] = channel.params[
            "external_channel_signer"
        ]["revocation_nonce"]
        mutations.append(changed)
        changed = deepcopy(status)
        changed["session_evidence"]["peer_partial_signature"] = "0x" + "01" * 32
        mutations.append(changed)
        for changed in mutations:
            for policy in ("Manual", "Auto"):
                assert_prepare_refused_unchanged(channel, changed, policy)
        assert channel.signer.signing_status(channel.channel_id) == status
        receive_hold(channel, invoice)
        channel.release(invoice)
        complete_inbound(channel, invoice, before)


# TEST-MAP: LSP-040
def test_prepared_approval_expiry_rechecked_at_confirmation(lsp_devnet):
    with ExternalChannel(lsp_devnet) as channel:
        channel.create()
        lsp_devnet.open_channel(lsp_devnet.payer, channel.node, public=True)
        invoice = unapproved_hold(channel, authorization_lifetime_ms=15_000)
        approve_invoice(channel, invoice)
        before = start_unapproved_inbound(channel, invoice)
        status = channel.pending({"SendCommitmentSigned", "CompleteReceivedCommitment"})
        prepared = channel.signer.command(
            "prepare", channel_id=channel.channel_id, status=status, policy="Manual"
        )
        assert prepared["outcome"] == "NeedConfirmation"
        snapshot = channel.signer.snapshot()
        wait_for(
            lambda: time.time() * 1000 >= invoice["terms"]["expires_at_ms"] + 10,
            timeout=20,
            description="wallet approval expiration",
        )
        failed = channel.signer.raw("confirm", prepared_id=prepared["prepared_id"])
        assert "error" in failed
        assert "submit_channel_signature" not in failed["observations"]["rpc_calls"]
        assert channel.signer.snapshot() == snapshot
        for policy in ("Manual", "Auto"):
            assert_prepare_refused_unchanged(channel, status, policy)
        assert channel.signer.signing_status(channel.channel_id) == status
        assert invoice_status(channel, invoice) != "Paid"
        assert balances(channel.tenant) == before[1]


# TEST-MAP: LSP-033
def test_external_hosted_payer_obtains_fulfillment_only_from_own_rpc(lsp_devnet):
    """Desired liveness invariant: receiver Paid with payer Inflight is a failure."""
    with ExternalChannel(lsp_devnet, "payer-signer") as payer, ExternalChannel(
        lsp_devnet, "receiver-signer"
    ) as receiver:
        payer.create()
        receiver.create()
        # A private hosted channel cannot be updated by update_channel. The
        # independently approved trampoline budget covers the default route fee.
        invoice = receiver.hold_invoice()
        trampoline_budget = 5_000  # Explicit 0.5% of this one-million-shannon invoice.
        approved_debit = invoice["amount"] + trampoline_budget
        payer.signer.authorize(
            {
                "type": "Outbound",
                "channel_id": payer.channel_id,
                "terms": payment_terms(
                    invoice["payment_hash"], approved_debit, inbound=False
                ),
            }
        )
        before_payer, before_receiver = balances(payer.tenant), balances(
            receiver.tenant
        )
        payer.tenant.rpc(
            "send_payment",
            {
                "invoice": invoice["invoice"],
                "trampoline_hops": [lsp_devnet.public.pubkey],
                "max_fee_amount": hex(trampoline_budget),
                "max_fee_rate": "0x5",
                "tlc_expiry_limit": hex(7 * 86_400_000),
            },
        )
        released = False
        observations = {}
        errors = []
        end = time.monotonic() + 90
        while time.monotonic() < end:
            for side in (payer, receiver):
                polled = side.signer.poll(allow_error=True)
                if not polled["ok"]:
                    errors.append(polled.get("error"))
            if (
                not released
                and invoice_status(receiver, invoice) == "Received"
                and receiver.recoverable(invoice["payment_hash"])
                and receiver.signer.signing_status(receiver.channel_id)["type"]
                == "NoSignatureRequired"
            ):
                receiver.release(invoice)
                released = True
            # This command has no preimage argument and reads only payer.get_payment.
            learned = payer.signer.command(
                "outbound_fulfillment",
                channel_id=payer.channel_id,
                payment_hash=invoice["payment_hash"],
            )
            assert learned["source"] == "payer.get_payment"
            payer_signing = payer.signer.signing_status(payer.channel_id)
            receiver_signing = receiver.signer.signing_status(receiver.channel_id)
            observations = {
                "payer": learned["payment"],
                "receiver": invoice_status(receiver, invoice),
                "authorized_from_payer_rpc": learned["authorized"],
                "payer_signing": payer_signing["type"],
                "receiver_signing": receiver_signing["type"],
                "payer_signing_transition": payer_signing.get("transition"),
                "receiver_signing_transition": receiver_signing.get("transition"),
                "payer_signing_request_id": payer_signing.get("request_id"),
                "receiver_signing_request_id": receiver_signing.get("request_id"),
            }
            if (
                observations["payer"]["status"] == "Success"
                and observations["receiver"] == "Paid"
                and not pending_tlcs(payer.tenant)
                and not pending_tlcs(receiver.tenant)
            ):
                break
            if observations["payer"]["status"] == "Failed":
                break
            time.sleep(0.2)
        # Keep a compact, token/key/preimage-free diagnosis even when pytest
        # truncates an assertion containing the full get_payment response.
        diagnosis = {
            "case_id": "LSP-033",
            "payment_hash": invoice["payment_hash"],
            "payer_status": observations["payer"]["status"],
            "receiver_invoice_status": observations["receiver"],
            "payer_rpc_preimage_present": observations["payer"].get("payment_preimage")
            is not None,
            "authorized_from_payer_rpc": observations["authorized_from_payer_rpc"],
            "payer_signing": observations["payer_signing"],
            "receiver_signing": observations["receiver_signing"],
            "payer_signing_transition": observations["payer_signing_transition"],
            "receiver_signing_transition": observations["receiver_signing_transition"],
            "payer_signing_request_id": observations["payer_signing_request_id"],
            "receiver_signing_request_id": observations["receiver_signing_request_id"],
            "receiver_released_preimage": released,
            "approved_debit": approved_debit,
            "receiver_invoice_amount": invoice["amount"],
            "sdk_errors": errors[-3:],
        }
        (lsp_devnet.path / "lsp-033-observation.json").write_text(
            json.dumps(diagnosis, indent=2)
        )
        assert observations["payer"]["status"] == "Success", diagnosis
        assert observations["receiver"] == "Paid"
        assert observations["authorized_from_payer_rpc"]
        assert observations["payer"]["payment_preimage"] == invoice["preimage"]
        assert (
            observations["payer_signing"]
            == observations["receiver_signing"]
            == "NoSignatureRequired"
        )
        assert not pending_tlcs(payer.tenant) and not pending_tlcs(receiver.tenant)
        assert (
            sum(before_payer.values()) - sum(balances(payer.tenant).values())
            == approved_debit
        )
        assert (
            sum(balances(receiver.tenant).values()) - sum(before_receiver.values())
            == invoice["amount"]
        )
        assert lsp_devnet.wait_delivery(invoice["payment_hash"], "succeeded")


# TEST-MAP: LSP-050
@pytest.mark.parametrize(
    "pointer,value",
    [
        ("/opening_context/local_amount", "0x1"),
        ("/opening_context/commitment_fee_rate", "0x1"),
        ("/opening_context/commitment_delay_epoch", "0x10000000002"),
        ("/channel_id", "0x" + "ee" * 32),
        ("/unsigned_funding_tx/outputs/0/lock/code_hash", "0x" + "ee" * 32),
    ],
)
def test_sdk_rejects_hostile_opening_response_then_recovers_same_intent(
    lsp_devnet, pointer, value
):
    with ExternalChannel(lsp_devnet) as channel:
        rejected = channel.signer.raw(
            "open",
            params=channel.params,
            response_patch=[{"pointer": pointer, "value": value}],
        )
        assert "error" in rejected
        assert not {"submit_signed_funding_tx", "submit_channel_signature"} & set(
            rejected["observations"]["rpc_calls"]
        )
        originals = rejected["observations"]["opening_responses"]
        assert len(originals) == 1
        original = originals[0]["original"]
        assert original != originals[0]["sdk_input"]
        channel.signer.restart()  # Preserve pending intent; never replace the SDK store.
        # initialize reopens authoritative session state; cached status alone would
        # not detect an accidentally persisted malicious binding after rejection.
        restored = channel.signer.initialize()
        assert restored["bound_channel_ids"] == []
        assert (
            restored["channel_open_signer_material"]
            == channel.params["external_channel_signer"]
        )
        assert channel.open() == original
        assert_funding_inputs_live(channel)
        channel.fund()
        channel.ready()
