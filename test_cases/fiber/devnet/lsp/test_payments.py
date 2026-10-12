"""Real hosted internal-signer routing baseline; external SDK liveness is separate."""

import hashlib
import secrets
import time

import pytest

from framework.lsp_devnet import RpcError, wait_for

CKB = 100_000_000
AMOUNT = 1_000_000


def n(value):
    return int(value, 16) if isinstance(value, str) else value


def channels(client):
    return client.rpc("list_channels", {})["channels"]


def balances(client):
    return {c["channel_id"]: n(c["local_balance"]) for c in channels(client)}


def pending(client):
    return [tlc for channel in channels(client) for tlc in channel["pending_tlcs"]]


def make_invoice(client, amount=AMOUNT, hold=False, expiry=3600, **options):
    preimage = secrets.token_bytes(32)
    payment_hash = "0x" + hashlib.sha256(preimage).hexdigest()
    params = {
        "amount": hex(amount),
        "currency": "Fibd",
        "expiry": hex(expiry),
        "hash_algorithm": "sha256",
        "allow_trampoline_routing": True,
        "allow_mpp": False,
    }
    params["payment_hash" if hold else "payment_preimage"] = (
        payment_hash if hold else "0x" + preimage.hex()
    )
    params.update(options)
    result = client.rpc("new_invoice", params)
    assert result["invoice"]["data"]["payment_hash"] == payment_hash
    return result["invoice_address"], payment_hash, "0x" + preimage.hex(), result


def send(client, address, trampoline=None, **options):
    params = {"invoice": address, "max_fee_amount": hex(2_000_000)}
    if trampoline:
        params["trampoline_hops"] = [trampoline.pubkey]
    params.update(options)
    return client.rpc("send_payment", params)


def settle(client, preimage):
    return client.rpc(
        "settle_invoice",
        {
            "payment_hash": "0x"
            + hashlib.sha256(bytes.fromhex(preimage.removeprefix("0x"))).hexdigest(),
            "payment_preimage": preimage,
        },
    )


def invoice_state(client, payment_hash):
    return client.rpc("get_invoice", {"payment_hash": payment_hash})["status"]


def assert_complete(
    env,
    payer,
    receiver,
    payment_hash,
    preimage,
    amount,
    before_payer,
    before_receiver,
    hosted=True,
    public_node=None,
    fee_limit=2_000_000,
    expected_pending_payer_hashes=(),
):
    payment = env.wait_payment(payer, payment_hash, "Success")
    wait_for(
        lambda: invoice_state(receiver, payment_hash) == "Paid",
        description="receiver invoice paid",
    )
    assert payment["payment_preimage"] == preimage
    if hosted:
        delivery = env.wait_delivery(payment_hash, "succeeded", public_node=public_node)
        assert delivery["payment_hash"] == payment_hash
    expected_pending = set(expected_pending_payer_hashes)
    assert payment_hash not in expected_pending
    wait_for(
        lambda: {tlc["payment_hash"] for tlc in pending(payer)} == expected_pending
        and not pending(receiver),
        description="both endpoint TLCs drained",
    )
    paid = sum(before_payer.values()) - sum(balances(payer).values())
    received = sum(balances(receiver).values()) - sum(before_receiver.values())
    assert received == amount
    assert paid == amount + n(payment["fee"])
    assert n(payment["fee"]) <= fee_limit
    return payment


def topology(env, node=None):
    public = node or env.public
    tenant = env.register(public_node=public)
    env.open_hosted_channel(tenant, public_node=public)
    env.open_channel(env.payer, public, amount=500 * CKB, public=True)
    return tenant


def cold(tenant):
    tenant.evict()
    wait_for(
        lambda: tenant.status()["runtime_status"] == "cold"
        and not tenant.status()["channel_online"],
        description="tenant cold and disconnected",
    )


# TEST-MAP: LSP-019
def test_hosted_invoice_identity_hint_and_scoped_lookup(lsp_devnet):
    env = lsp_devnet
    tenant = topology(env)
    for hold in (False, True):
        address, payment_hash, _, result = make_invoice(tenant, hold=hold)
        decoded = env.payer.rpc("parse_invoice", {"invoice": address})
        assert decoded["invoice"] == result["invoice"]
        attrs = result["invoice"]["data"]["attrs"]
        assert any(a.get("trampoline_route_hint") == env.public.pubkey for a in attrs)
        assert any(a.get("payee_public_key") == tenant.invoice_pubkey for a in attrs)
        assert n(result["invoice"]["amount"]) == AMOUNT
        assert any(n(a["expiry_time"]) == 3600 for a in attrs if "expiry_time" in a)
        assert invoice_state(tenant, payment_hash) == "Open"
        operator = env.public.rpc(
            "lsp_get_invoice",
            {"tenant_id": tenant.tenant_id, "payment_hash": payment_hash},
        )
        assert operator == tenant.rpc("get_invoice", {"payment_hash": payment_hash})


# TEST-MAP: LSP-020
def test_inbound_mpp_rejects_before_invoice_write(lsp_devnet):
    env = lsp_devnet
    tenant = topology(env)
    payment_hash = "0x" + secrets.token_hex(32)
    params = {
        "amount": hex(AMOUNT),
        "currency": "Fibd",
        "expiry": hex(3600),
        "payment_hash": payment_hash,
        "allow_mpp": True,
        "allow_trampoline_routing": True,
    }
    with pytest.raises(RpcError, match="hosted tenant invoices do not support MPP"):
        tenant.rpc("new_invoice", params)
    with pytest.raises(RpcError, match="invoice not found"):
        tenant.rpc("get_invoice", {"payment_hash": payment_hash})
    params["allow_mpp"] = False
    created = tenant.rpc("new_invoice", params)
    assert created["invoice"]["data"]["payment_hash"] == payment_hash


# TEST-MAP: LSP-021
def test_online_inbound_settles_both_ends_once(lsp_devnet):
    env = lsp_devnet
    tenant = topology(env)
    address, payment_hash, preimage, _ = make_invoice(tenant)
    before_payer, before_receiver = balances(env.payer), balances(tenant)
    send(env.payer, address)
    assert_complete(
        env,
        env.payer,
        tenant,
        payment_hash,
        preimage,
        AMOUNT,
        before_payer,
        before_receiver,
    )


# TEST-MAP: LSP-022
# TEST-MAP: LSP-011
def test_offline_deferred_delivery_resumes_without_resending(lsp_devnet):
    env = lsp_devnet
    tenant = topology(env)
    address, payment_hash, preimage, _ = make_invoice(tenant)
    before_payer, before_receiver = balances(env.payer), balances(tenant)
    cold(tenant)
    send(env.payer, address)
    delivery = env.wait_delivery(payment_hash, "deferred")
    assert n(delivery["attempt_count"]) == 0
    assert tenant.status()["runtime_status"] == "cold"
    assert (
        env.payer.rpc("get_payment", {"payment_hash": payment_hash})["status"]
        == "Inflight"
    )
    with pytest.raises(RpcError, match="unfinished hosted payment deliveries"):
        tenant.evict()
    assert tenant.status()["runtime_status"] == "cold"
    tenant.ensure()
    assert_complete(
        env,
        env.payer,
        tenant,
        payment_hash,
        preimage,
        AMOUNT,
        before_payer,
        before_receiver,
    )
    tenant.evict()
    assert tenant.status()["runtime_status"] == "cold"


# TEST-MAP: LSP-023
def test_deferred_and_final_delivery_survive_host_restart(lsp_devnet):
    env = lsp_devnet
    tenant = topology(env)
    address, payment_hash, preimage, _ = make_invoice(tenant)
    before_payer, before_receiver = balances(env.payer), balances(tenant)
    cold(tenant)
    send(env.payer, address)
    before = env.wait_delivery(payment_hash, "deferred")
    env.restart(env.public)
    after = env.wait_delivery(payment_hash, "deferred")
    for key in (
        "payment_hash",
        "tenant_id",
        "private_channel_id",
        "incoming_channel_id",
        "incoming_tlc_id",
        "created_at",
        "buffer_deadline",
        "attempt_count",
    ):
        assert after[key] == before[key], key
    assert tenant.status()["runtime_status"] == "cold"
    tenant.ensure()
    assert_complete(
        env,
        env.payer,
        tenant,
        payment_hash,
        preimage,
        AMOUNT,
        before_payer,
        before_receiver,
    )
    terminal_balance = balances(tenant)
    env.restart(env.public)
    assert env.wait_delivery(payment_hash, "succeeded")["status"] == "succeeded"
    tenant.ensure()
    wait_for(
        lambda: tenant.status()["channel_online"],
        description="channel restored after final payment",
    )
    assert balances(tenant) == terminal_balance
    assert not pending(tenant)


# TEST-MAP: LSP-024
def test_offline_buffer_timeout_does_not_revive_payment(lsp_devnet):
    env = lsp_devnet
    public = env.create_node(
        "short-buffer", lsp=True, lsp_config={"max_buffer_duration_ms": 3000}
    )
    tenant = topology(env, public)
    address, payment_hash, _, _ = make_invoice(tenant)
    before = balances(tenant)
    cold(tenant)
    send(env.payer, address)
    delivery = env.wait_delivery(payment_hash, "failed", public_node=public)
    assert n(delivery["attempt_count"]) == 0
    assert delivery["failure_reason"]
    payment = env.wait_payment(env.payer, payment_hash, "Failed")
    assert payment.get("payment_preimage") is None
    tenant.ensure()
    wait_for(
        lambda: tenant.status()["channel_online"],
        description="reactivation after deadline",
    )
    assert invoice_state(tenant, payment_hash) != "Paid"
    assert balances(tenant) == before

    def expired_tlcs_drained():
        # RPC channel.rs:565 returns all_tlcs, including failed history retained
        # until a later commitment (channel.rs:8312). Only the exact final ACK
        # state is retired; any other/unknown state or reserved amount fails.
        for client in (env.payer, public, tenant):
            for channel in channels(client):
                if n(channel["offered_tlc_balance"]) or n(
                    channel["received_tlc_balance"]
                ):
                    return False
                for tlc in channel["pending_tlcs"]:
                    if tlc["payment_hash"] != payment_hash or tlc["status"] not in (
                        {"Outbound": "RemoveAckConfirmed"},
                        {"Inbound": "RemoveAckConfirmed"},
                    ):
                        return False
        return True

    try:
        wait_for(
            expired_tlcs_drained,
            description="expired TLC removal confirmed and reservations released",
        )
    except AssertionError as error:
        raise AssertionError(
            "Expired payment left active TLCs or reserved funds: "
            f"payer={channels(env.payer)!r}; public={channels(public)!r}; "
            f"tenant={channels(tenant)!r}"
        ) from error
    address2, hash2, preimage2, _ = make_invoice(tenant)
    before_payer, before_receiver = balances(env.payer), balances(tenant)
    send(env.payer, address2)
    assert_complete(
        env,
        env.payer,
        tenant,
        hash2,
        preimage2,
        AMOUNT,
        before_payer,
        before_receiver,
        public_node=public,
    )


# TEST-MAP: LSP-026
# TEST-MAP: LSP-011
# TEST-MAP: LSP-007
def test_dispatched_hold_payment_outlives_buffer_deadline(lsp_devnet):
    env = lsp_devnet
    public = env.create_node(
        "hold-buffer", lsp=True, lsp_config={"max_buffer_duration_ms": 3000}
    )
    tenant = topology(env, public)
    address, payment_hash, preimage, _ = make_invoice(tenant, hold=True)
    before_payer, before_receiver = balances(env.payer), balances(tenant)
    send(env.payer, address)
    delivery = env.wait_delivery(payment_hash, "in_flight", public_node=public)
    wait_for(
        lambda: invoice_state(tenant, payment_hash) == "Received",
        description="hold invoice received",
    )
    with pytest.raises(RpcError, match="unfinished hosted payment deliveries"):
        tenant.evict()
    wait_for(
        lambda: time.time() * 1000 > n(delivery["buffer_deadline"]) + 1000,
        description="past original buffer deadline",
        timeout=15,
    )
    assert (
        public.rpc("lsp_get_payment_delivery", {"payment_hash": payment_hash})["status"]
        == "in_flight"
    )
    assert invoice_state(tenant, payment_hash) == "Received"
    assert (
        env.payer.rpc("get_payment", {"payment_hash": payment_hash})["status"]
        == "Inflight"
    )
    intruder = env.register(public_node=public)
    with pytest.raises(RpcError, match="(?i)invoice not found"):
        settle(intruder, preimage)
    assert invoice_state(tenant, payment_hash) == "Received"
    settle(tenant, preimage)
    assert_complete(
        env,
        env.payer,
        tenant,
        payment_hash,
        preimage,
        AMOUNT,
        before_payer,
        before_receiver,
        public_node=public,
    )


# TEST-MAP: LSP-027
@pytest.mark.parametrize("offline", [True, False])
def test_second_payer_cannot_replace_first_execution(lsp_devnet, offline):
    env = lsp_devnet
    tenant = topology(env)
    env.open_channel(env.receiver, env.public, amount=500 * CKB, public=True)
    address, payment_hash, preimage, _ = make_invoice(tenant, hold=not offline)
    before_payer, before_receiver = balances(env.payer), balances(tenant)
    second_before = balances(env.receiver)
    if offline:
        cold(tenant)
    send(env.payer, address)
    first = env.wait_delivery(payment_hash, "deferred" if offline else "in_flight")
    send(env.receiver, address)
    env.wait_payment(env.receiver, payment_hash, "Failed")
    current = env.public.rpc("lsp_get_payment_delivery", {"payment_hash": payment_hash})
    for key in ("incoming_channel_id", "incoming_tlc_id", "created_at"):
        assert current[key] == first[key]
    assert (
        env.payer.rpc("get_payment", {"payment_hash": payment_hash})["status"]
        == "Inflight"
    )
    assert balances(env.receiver) == second_before
    if offline:
        tenant.ensure()
    else:
        assert invoice_state(tenant, payment_hash) == "Received"
        settle(tenant, preimage)
    assert_complete(
        env,
        env.payer,
        tenant,
        payment_hash,
        preimage,
        AMOUNT,
        before_payer,
        before_receiver,
    )


# TEST-MAP: LSP-029
# TEST-MAP: LSP-007
# TEST-MAP: LSP-008
def test_tenant_outbound_ordinary_payment_and_payment_isolation(lsp_devnet):
    env = lsp_devnet
    tenant = env.register()
    other = env.register()
    env.open_hosted_channel(tenant)
    env.open_channel(env.public, env.receiver, amount=500 * CKB, public=True)
    address, payment_hash, preimage, _ = make_invoice(env.receiver)
    before_payer, before_receiver = balances(tenant), balances(env.receiver)
    send(tenant, address, trampoline=env.public)
    payment = assert_complete(
        env,
        tenant,
        env.receiver,
        payment_hash,
        preimage,
        AMOUNT,
        before_payer,
        before_receiver,
        hosted=False,
    )
    operator = env.public.rpc(
        "lsp_get_payment",
        {"tenant_id": tenant.tenant_id, "payment": {"payment_hash": payment_hash}},
    )
    assert operator["status"] == payment["status"]
    with pytest.raises(RpcError, match="Payment session not found"):
        other.rpc("get_payment", {"payment_hash": payment_hash})
    assert all(
        p["payment_hash"] != payment_hash
        for p in other.rpc("list_payments", {})["payments"]
    )
    with pytest.raises(RpcError, match="hosted payment delivery not found"):
        env.public.rpc("lsp_get_payment_delivery", {"payment_hash": payment_hash})


# TEST-MAP: LSP-030
def test_outbound_mpp_really_uses_two_downstream_channels(lsp_devnet):
    env = lsp_devnet
    tenant = env.register()
    env.open_hosted_channel(tenant)
    for _ in range(2):
        env.open_channel(env.public, env.receiver, amount=120 * CKB, public=False)
    available = [
        n(c["local_balance"])
        for c in channels(env.public)
        if c["pubkey"] == env.receiver.pubkey
    ]
    assert len(available) == 2
    amount = max(available) + min(available) // 2
    assert max(available) < amount < sum(available)
    address, payment_hash, preimage, _ = make_invoice(
        env.receiver, amount=amount, hold=True, allow_mpp=True
    )
    # This capacity-forced payment is much larger than the baseline amount.
    # Authorize the standard 0.5% fee ceiling instead of the small fixed cap.
    fee_limit = (amount * 5 + 999) // 1000
    before_payer, before_receiver = balances(tenant), balances(env.receiver)
    send(
        tenant,
        address,
        trampoline=env.public,
        max_parts=hex(2),
        max_fee_amount=hex(fee_limit),
    )
    wait_for(
        lambda: len(
            [
                c
                for c in channels(env.public)
                if c["pubkey"] == env.receiver.pubkey
                and any(t["payment_hash"] == payment_hash for t in c["pending_tlcs"])
            ]
        )
        >= 2,
        description="two real downstream MPP channels",
        timeout=60,
    )
    downstream = [
        c
        for c in channels(env.public)
        if c["pubkey"] == env.receiver.pubkey
        and any(t["payment_hash"] == payment_hash for t in c["pending_tlcs"])
    ]
    assert len({c["channel_id"] for c in downstream}) == 2
    assert (
        sum(
            n(t["amount"])
            for c in downstream
            for t in c["pending_tlcs"]
            if t["payment_hash"] == payment_hash
        )
        == amount
    )
    wait_for(
        lambda: invoice_state(env.receiver, payment_hash) == "Received",
        description="receiver has committed the complete MPP amount",
    )
    settle(env.receiver, preimage)
    assert_complete(
        env,
        tenant,
        env.receiver,
        payment_hash,
        preimage,
        amount,
        before_payer,
        before_receiver,
        hosted=False,
        fee_limit=fee_limit,
    )
    with pytest.raises(RpcError, match="hosted payment delivery not found"):
        env.public.rpc("lsp_get_payment_delivery", {"payment_hash": payment_hash})


# TEST-MAP: LSP-031
def test_payment_between_tenants_on_distinct_lsps(lsp_devnet):
    env = lsp_devnet
    remote = env.create_node("remote-lsp", lsp=True)
    alice, bob = env.register(), env.register(public_node=remote)
    env.open_hosted_channel(alice)
    env.open_hosted_channel(bob, public_node=remote)
    env.open_channel(env.public, remote, amount=500 * CKB, public=True)
    address, payment_hash, preimage, _ = make_invoice(bob)
    before_payer, before_receiver = balances(alice), balances(bob)
    # Explicit hops replace the invoice's route hint; both LSPs must be named
    # so Public T can reach Bob through the remote LSP's private channel.
    send(alice, address, trampoline_hops=[env.public.pubkey, remote.pubkey])
    assert_complete(
        env,
        alice,
        bob,
        payment_hash,
        preimage,
        AMOUNT,
        before_payer,
        before_receiver,
        public_node=remote,
    )
    assert alice.tenant_id != bob.tenant_id
    assert (
        remote.rpc("lsp_get_payment_delivery", {"payment_hash": payment_hash})[
            "tenant_id"
        ]
        == bob.tenant_id
    )

    with pytest.raises(RpcError, match="hosted payment delivery not found"):
        env.public.rpc("lsp_get_payment_delivery", {"payment_hash": payment_hash})
    with pytest.raises(RpcError, match="tenant .* is not registered"):
        env.public.rpc(
            "lsp_get_invoice",
            {"tenant_id": bob.tenant_id, "payment_hash": payment_hash},
        )
    with pytest.raises(RpcError, match="Payment session not found"):
        bob.rpc("get_payment", {"payment_hash": payment_hash})
    with pytest.raises(RpcError, match="invoice not found"):
        alice.rpc("get_invoice", {"payment_hash": payment_hash})


# TEST-MAP: LSP-032
def test_failed_outbound_payment_does_not_poison_next_payment(lsp_devnet):
    env = lsp_devnet
    tenant = env.register()
    env.open_hosted_channel(tenant)
    env.open_channel(env.public, env.receiver, amount=500 * CKB, public=True)
    before_tenant, before_receiver = balances(tenant), balances(env.receiver)
    address, payment_hash, _, _ = make_invoice(env.receiver, amount=1_000_000 * CKB)
    try:
        send(tenant, address, trampoline=env.public)
    except RpcError as error:
        # Attribute a synchronous rejection to the requested insufficient route,
        # never to a timeout, broken transport, or local programming exception.
        message = str(error.error).lower()
        assert any(
            word in message for word in ("route", "balance", "capacity", "path")
        ), message
    else:
        env.wait_payment(tenant, payment_hash, "Failed")
    assert invoice_state(env.receiver, payment_hash) != "Paid"
    wait_for(
        lambda: not pending(tenant) and not pending(env.receiver),
        description="failed route TLC cleanup",
    )
    assert balances(tenant) == before_tenant
    assert balances(env.receiver) == before_receiver
    address2, hash2, preimage2, _ = make_invoice(env.receiver)
    send(tenant, address2, trampoline=env.public)
    assert_complete(
        env,
        tenant,
        env.receiver,
        hash2,
        preimage2,
        AMOUNT,
        before_tenant,
        before_receiver,
        hosted=False,
    )


# TEST-MAP: LSP-034
def test_rejected_missing_expiry_invoice_leaves_no_partial_write(lsp_devnet):
    """Desired atomicity invariant. Keep a real failure if the pinned product violates it."""
    env = lsp_devnet
    tenant = topology(env)
    payment_hash = "0x" + secrets.token_hex(32)
    params = {
        "amount": hex(AMOUNT),
        "currency": "Fibd",
        "payment_hash": payment_hash,
        "allow_trampoline_routing": True,
    }
    with pytest.raises(RpcError, match="hosted invoice must have a finite expiry"):
        tenant.rpc("new_invoice", params)
    with pytest.raises(RpcError, match="invoice not found"):
        leaked = tenant.rpc("get_invoice", {"payment_hash": payment_hash})
        data = leaked["invoice"]["data"]
        expiry = [a["expiry_time"] for a in data["attrs"] if "expiry_time" in a]
        # Deliberately omit the invoice address and all secret-bearing fields.
        pytest.fail(
            "Rejected hosted invoice persisted: "
            f"status={leaked['status']}, payment_hash={data['payment_hash']}, "
            f"expiry_time={expiry if expiry else 'absent'}",
            pytrace=False,
        )
    params["expiry"] = hex(3600)
    assert (
        tenant.rpc("new_invoice", params)["invoice"]["data"]["payment_hash"]
        == payment_hash
    )


# TEST-MAP: LSP-025
@pytest.mark.parametrize("winner", ["invoice", "tlc_safety"])
def test_deferred_deadline_uses_the_actual_earliest_bound(lsp_devnet, winner):
    env = lsp_devnet
    buffer_cap = 24 * 60 * 60 * 1000
    final_delta = 60_000
    public = env.create_node(
        "deadline", lsp=True, lsp_config={"max_buffer_duration_ms": buffer_cap}
    )
    tenant = topology(env, public)
    # Concrete real channel policy, not a synthetic TrampolineForwardingRequest.
    incoming = next(c for c in channels(public) if c["pubkey"] == env.payer.pubkey)
    incoming_delta = 60_000
    public.rpc(
        "update_channel",
        {"channel_id": incoming["channel_id"], "tlc_expiry_delta": hex(incoming_delta)},
    )
    wait_for(
        lambda: n(
            next(
                c for c in channels(public) if c["channel_id"] == incoming["channel_id"]
            )["tlc_expiry_delta"]
        )
        == incoming_delta,
        description="forwarding expiry policy updated",
    )
    expiry_seconds = 8 if winner == "invoice" else 48 * 60 * 60
    address, payment_hash, preimage, result = make_invoice(
        tenant, expiry=expiry_seconds, final_expiry_delta=hex(final_delta)
    )
    before_payer, before_receiver = balances(env.payer), balances(tenant)
    cold(tenant)
    send(
        env.payer,
        address,
        final_tlc_expiry_delta=hex(final_delta),
        tlc_expiry_limit=hex(20 * 60 * 60 * 1000),
    )
    delivery = env.wait_delivery(payment_hash, "deferred", public_node=public)
    observed_channel = next(
        c
        for c in channels(public)
        if c["channel_id"] == delivery["incoming_channel_id"]
    )
    tlc = next(
        t for t in observed_channel["pending_tlcs"] if t["payment_hash"] == payment_hash
    )
    cap_deadline = n(delivery["created_at"]) + buffer_cap
    invoice_deadline = n(result["invoice"]["data"]["timestamp"]) + expiry_seconds * 1000
    safety_deadline = n(tlc["expiry"]) - incoming_delta - final_delta - 30_000
    expected = min(cap_deadline, invoice_deadline, safety_deadline)
    assert n(delivery["buffer_deadline"]) == expected
    if winner == "invoice":
        assert invoice_deadline < min(cap_deadline, safety_deadline)
        env.wait_delivery(payment_hash, "failed", public_node=public, timeout=20)
        env.wait_payment(env.payer, payment_hash, "Failed")
    else:
        assert safety_deadline < min(cap_deadline, invoice_deadline)
        assert safety_deadline > int(time.time() * 1000)
        # Proves the safety term, without pretending to wait through a long route budget.
        tenant.ensure()
        assert_complete(
            env,
            env.payer,
            tenant,
            payment_hash,
            preimage,
            AMOUNT,
            before_payer,
            before_receiver,
            public_node=public,
        )


# TEST-MAP: LSP-028
def test_pending_delivery_quotas_are_isolated_and_released(lsp_devnet):
    env = lsp_devnet
    public = env.create_node(
        "quotas",
        lsp=True,
        lsp_config={
            "max_pending_deliveries": 2,
            "max_pending_deliveries_per_tenant": 1,
        },
    )
    alice, bob, carol = [env.register(public_node=public) for _ in range(3)]
    for tenant in (alice, bob, carol):
        env.open_hosted_channel(tenant, public_node=public)
    env.open_channel(env.payer, public, amount=500 * CKB, public=True)
    # Admission rejection is a node failure to the outer trampoline payer.
    # Separate probes keep that payer's route penalties from preventing the
    # next request from reaching the LSP and exercising its distinct quota.
    per_tenant_probe = env.create_node("per-tenant-quota-probe")
    global_probe = env.create_node("global-quota-probe")
    for probe in (per_tenant_probe, global_probe):
        env.open_channel(probe, public, amount=500 * CKB, public=True)
    a1, a2, b1, c1 = (
        make_invoice(alice),
        make_invoice(alice),
        make_invoice(bob),
        make_invoice(carol),
    )
    for tenant in (alice, bob, carol):
        cold(tenant)

    def rejected_by_quota(log_offset, reason):
        # The public payer error is TemporaryNodeFailure, which also represents
        # other failures. Require this operation's service-side admission reason
        # from network.rs:5219 in addition to the RPC and durable-state oracles.
        with public.log_path.open() as log:
            log.seek(log_offset)
            return f"Hosted trampoline delivery rejected: {reason}" in log.read()

    send(env.payer, a1[0])
    first_a = env.wait_delivery(a1[1], "deferred", public_node=public)
    rejection_start = public.log_path.stat().st_size
    send(per_tenant_probe, a2[0], timeout=hex(5))
    env.wait_payment(per_tenant_probe, a2[1], "Failed", timeout=20)
    wait_for(
        lambda: rejected_by_quota(
            rejection_start,
            f"pending hosted delivery limit reached for tenant {alice.tenant_id}",
        ),
        description="A2 reached the LSP and hit Alice's delivery quota",
    )
    with pytest.raises(RpcError, match="hosted payment delivery not found"):
        public.rpc("lsp_get_payment_delivery", {"payment_hash": a2[1]})
    send(env.payer, b1[0])
    first_b = env.wait_delivery(b1[1], "deferred", public_node=public)
    rejection_start = public.log_path.stat().st_size
    send(global_probe, c1[0], timeout=hex(5))
    env.wait_payment(global_probe, c1[1], "Failed", timeout=20)
    wait_for(
        lambda: rejected_by_quota(
            rejection_start, "global pending hosted delivery limit reached"
        ),
        description="C1 reached the LSP and hit the global delivery quota",
    )
    with pytest.raises(RpcError, match="hosted payment delivery not found"):
        public.rpc("lsp_get_payment_delivery", {"payment_hash": c1[1]})
    for original in (first_a, first_b):
        actual = public.rpc(
            "lsp_get_payment_delivery", {"payment_hash": original["payment_hash"]}
        )
        assert actual["status"] == "deferred"
        assert actual["incoming_tlc_id"] == original["incoming_tlc_id"]
        assert actual["created_at"] == original["created_at"]
    # Rejected payer attempts are terminal before a slot is released, preventing retry races.
    alice.ensure()
    env.wait_payment(env.payer, a1[1], "Success")
    env.wait_delivery(a1[1], "succeeded", public_node=public)
    carol.ensure()
    fresh = make_invoice(carol)
    before_payer, before_receiver = balances(env.payer), balances(carol)
    send(env.payer, fresh[0])
    assert_complete(
        env,
        env.payer,
        carol,
        fresh[1],
        fresh[2],
        AMOUNT,
        before_payer,
        before_receiver,
        public_node=public,
        # Bob must still occupy the other slot while Carol reuses Alice's.
        # Only that exact TLC may remain; the fresh hash and all others clear.
        expected_pending_payer_hashes=(b1[1],),
    )
    assert (
        public.rpc("lsp_get_payment_delivery", {"payment_hash": b1[1]})["status"]
        == "deferred"
    )
    bob.ensure()
    env.wait_payment(env.payer, b1[1], "Success")
    env.wait_delivery(b1[1], "succeeded", public_node=public)
    wait_for(
        lambda: all(not pending(client) for client in (env.payer, alice, bob, carol)),
        description="all successful quota-test endpoint TLCs drained",
    )


# TEST-MAP: LSP-011
def test_runtime_busy_guard_without_a_hosted_delivery(lsp_devnet):
    env = lsp_devnet
    tenant = env.register()
    env.open_hosted_channel(tenant)
    env.open_channel(env.public, env.receiver, amount=500 * CKB, public=True)
    address, payment_hash, preimage, _ = make_invoice(env.receiver, hold=True)
    before_payer, before_receiver = balances(tenant), balances(env.receiver)
    send(tenant, address, trampoline=env.public)
    wait_for(
        lambda: invoice_state(env.receiver, payment_hash) == "Received",
        description="ordinary receiver holds outgoing hosted TLC",
    )
    assert pending(tenant)
    with pytest.raises(RpcError, match="hosted payment delivery not found"):
        env.public.rpc("lsp_get_payment_delivery", {"payment_hash": payment_hash})
    with pytest.raises(RpcError, match="hosted tenant runtime is busy"):
        tenant.evict()
    assert tenant.status()["runtime_status"] == "active"
    settle(env.receiver, preimage)
    assert_complete(
        env,
        tenant,
        env.receiver,
        payment_hash,
        preimage,
        AMOUNT,
        before_payer,
        before_receiver,
        hosted=False,
    )
    tenant.evict()
    assert tenant.status()["runtime_status"] == "cold"


# TEST-MAP: LSP-008
def test_three_rpc_identities_keep_their_own_outgoing_sessions(lsp_devnet):
    env = lsp_devnet
    alice, bob = env.register(), env.register()
    env.open_hosted_channel(alice)
    env.open_hosted_channel(bob)
    env.open_channel(env.public, env.receiver, amount=500 * CKB, public=True)
    hashes = {}
    for name, payer in (("alice", alice), ("bob", bob), ("public", env.public)):
        address, payment_hash, preimage, _ = make_invoice(env.receiver)
        before_payer, before_receiver = balances(payer), balances(env.receiver)
        send(payer, address, trampoline=env.public if name != "public" else None)
        assert_complete(
            env,
            payer,
            env.receiver,
            payment_hash,
            preimage,
            AMOUNT,
            before_payer,
            before_receiver,
            hosted=False,
        )
        hashes[name] = payment_hash
    for name, tenant in (("alice", alice), ("bob", bob)):
        listed = tenant.rpc("list_payments", {})["payments"]
        assert {payment["payment_hash"] for payment in listed} == {hashes[name]}
        for other in set(hashes) - {name}:
            with pytest.raises(RpcError, match="Payment session not found"):
                tenant.rpc("get_payment", {"payment_hash": hashes[other]})
    assert (
        env.public.rpc("get_payment", {"payment_hash": hashes["public"]})["status"]
        == "Success"
    )
    # Public T legitimately owns downstream forwarding sessions for A/B. Its
    # namespace need not hide their hash; it must not return the tenant's session.
    for name, tenant in (("alice", alice), ("bob", bob)):
        tenant_session = tenant.rpc("get_payment", {"payment_hash": hashes[name]})
        public_session = env.public.rpc("get_payment", {"payment_hash": hashes[name]})
        assert n(public_session["fee"]) < n(tenant_session["fee"])
