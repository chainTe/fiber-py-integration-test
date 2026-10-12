"""Hosted registry and authority boundaries against the pinned real FNN process."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import secrets

import pytest

from framework.lsp_devnet import RpcError, Tenant, ckb_hash, wait_for


def number(value):
    return int(value, 16) if isinstance(value, str) else value


def counts(node):
    status = node.rpc("lsp_get_status")
    return number(status["registered_tenants"]), number(status["active_tenants"])


def tenants(node):
    return node.rpc("lsp_list_tenants")["tenants"]


def challenge(env, secret, node=None):
    node = node or env.public
    return node.rpc(
        "lsp_get_tenant_registry_nonce", {"root_signer_pubkey": env.root_pubkey(secret)}
    )


def proof(env, secret, nonce):
    return env.registry_proof(secret, nonce)


def invoice(tenant, **overrides):
    params = {
        "amount": hex(1_000_000),
        "currency": "Fibd",
        "expiry": hex(3600),
        "allow_trampoline_routing": True,
        "payment_preimage": "0x" + secrets.token_hex(32),
    }
    params.update(overrides)
    return tenant.rpc("new_invoice", params)


# TEST-MAP: LSP-001
def test_authenticated_registration_and_tenant_activation(lsp_devnet):
    env = lsp_devnet
    before = counts(env.public)
    tenant = env.register()
    state = tenant.status()
    assert state["runtime_status"] == "cold"
    assert state["root_signer_pubkey"] == env.root_pubkey(tenant.root_secret)
    expected_id = ckb_hash(
        b"fiber-hosted-lsp-tenant-id/v1"
        + bytes.fromhex(env.root_pubkey(tenant.root_secret))
    ).hex()
    assert tenant.tenant_id == expected_id
    assert counts(env.public) == (before[0] + 1, before[1])
    assert tenant.rpc("list_channels", {})["channels"] == []
    assert tenant.status()["runtime_status"] == "active"
    assert counts(env.public) == (before[0] + 1, before[1] + 1)


# TEST-MAP: LSP-002
def test_registry_latest_nonce_single_use(lsp_devnet):
    env = lsp_devnet
    secret = secrets.token_bytes(32)
    old = challenge(env, secret)
    latest = challenge(env, secret)
    assert old["nonce"] != latest["nonce"]
    before = counts(env.public)
    with pytest.raises(RpcError):
        env.public.rpc("lsp_register_tenant", proof(env, secret, old))
    assert counts(env.public) == before
    params = proof(env, secret, latest)
    result = env.public.rpc("lsp_register_tenant", params)
    assert result["access_token"]
    assert counts(env.public) == (before[0] + 1, before[1])
    with pytest.raises(RpcError):
        env.public.rpc("lsp_register_tenant", params)
    assert counts(env.public) == (before[0] + 1, before[1])


# TEST-MAP: LSP-003
@pytest.mark.parametrize("mutation", ["root", "lsp", "signature", "nonce"])
def test_bad_registry_proof_preserves_live_nonce(lsp_devnet, mutation):
    env = lsp_devnet
    secret = secrets.token_bytes(32)
    nonce = challenge(env, secret)
    good = proof(env, secret, nonce)
    bad = deepcopy(good)
    if mutation == "root":
        bad["root_signer_pubkey"] = env.root_pubkey(secrets.token_bytes(32))
    elif mutation == "lsp":
        wrong_domain = {**nonce, "lsp_node_id": env.payer.pubkey}
        bad = proof(env, secret, wrong_domain)
    elif mutation == "signature":
        bad["signature"] = "0x" + "00" * 64
    else:
        bad["nonce"] = "0x" + secrets.token_hex(32)
    before = counts(env.public)
    with pytest.raises(RpcError):
        env.public.rpc("lsp_register_tenant", bad)
    assert counts(env.public) == before
    result = env.public.rpc("lsp_register_tenant", good)
    assert result["tenant"]["root_signer_pubkey"] == env.root_pubkey(secret)
    assert counts(env.public) == (before[0] + 1, before[1])


# TEST-MAP: LSP-004
def test_refresh_preserves_existing_identity_channel_and_invoice(lsp_devnet):
    env = lsp_devnet
    tenant = env.register()
    env.open_hosted_channel(tenant)
    created = invoice(tenant)
    payment_hash = created["invoice"]["data"]["payment_hash"]
    before = tenant.status()
    before_count = counts(env.public)
    refreshed = env.public.rpc(
        "lsp_register_tenant",
        proof(env, tenant.root_secret, challenge(env, tenant.root_secret)),
    )
    after = refreshed["tenant"]
    for field in (
        "tenant_id",
        "root_signer_pubkey",
        "invoice_pubkey",
        "created_at",
        "private_channel_id",
    ):
        assert after[field] == before[field], field
    refreshed_client = Tenant(env.public, refreshed)
    assert (
        refreshed_client.rpc("get_invoice", {"payment_hash": payment_hash})["status"]
        == "Open"
    )
    assert counts(env.public) == before_count
    assert len(refreshed_client.rpc("list_channels", {})["channels"]) == 1


# TEST-MAP: LSP-005
def test_registry_nonce_consumption_survives_process_restart(lsp_devnet):
    env = lsp_devnet
    secret = secrets.token_bytes(32)
    params = proof(env, secret, challenge(env, secret))
    env.restart(env.public)
    registered = env.public.rpc("lsp_register_tenant", params)
    env.restart(env.public)
    with pytest.raises(RpcError):
        env.public.rpc("lsp_register_tenant", params)
    matches = [
        t
        for t in tenants(env.public)
        if t["tenant_id"] == registered["tenant"]["tenant_id"]
    ]
    assert len(matches) == 1
    assert matches[0]["invoice_pubkey"] == registered["tenant"]["invoice_pubkey"]
    assert counts(env.public)[0] == 1


# TEST-MAP: LSP-006
def test_tenant_token_cannot_call_operator_or_unscoped_methods(lsp_devnet):
    env = lsp_devnet
    tenant = env.register()
    public_invoice = invoice(env.public)
    public_hash = public_invoice["invoice"]["data"]["payment_hash"]
    before = counts(env.public)
    for token in (None, "invalid-biscuit"):
        with pytest.raises(RpcError):
            env.public.rpc("lsp_list_tenants", token=token)
    for method, params in (
        ("lsp_get_status", None),
        ("lsp_list_tenants", None),
        ("lsp_ensure_tenant", {"tenant_id": tenant.tenant_id}),
        ("lsp_evict_tenant", {"tenant_id": tenant.tenant_id}),
        ("node_info", None),
        (
            "open_channel",
            {
                "pubkey": env.payer.pubkey,
                "funding_amount": hex(500 * 10**8),
                "public": False,
            },
        ),
        ("cancel_invoice", {"payment_hash": public_hash}),
        ("parse_invoice", {"invoice": public_invoice["invoice_address"]}),
    ):
        with pytest.raises(RpcError):
            tenant.rpc(method, params)
    assert counts(env.public) == before
    assert (
        env.public.rpc("get_invoice", {"payment_hash": public_hash})["status"] == "Open"
    )
    assert tenant.rpc("list_channels", {})["channels"] == []


# TEST-MAP: LSP-007
# TEST-MAP: LSP-008
def test_tenant_and_public_namespace_isolation(lsp_devnet):
    env = lsp_devnet
    alice, bob = env.register(), env.register()
    env.open_hosted_channel(alice)
    env.open_hosted_channel(bob)
    a_invoice, b_invoice = invoice(alice), invoice(bob)
    public_invoice = invoice(env.public)
    public_hash = public_invoice["invoice"]["data"]["payment_hash"]
    for tenant in (alice, bob):
        with pytest.raises(RpcError):
            tenant.rpc("get_invoice", {"payment_hash": public_hash})
    assert (
        env.public.rpc("get_invoice", {"payment_hash": public_hash})["status"] == "Open"
    )
    a_hash = a_invoice["invoice"]["data"]["payment_hash"]
    b_hash = b_invoice["invoice"]["data"]["payment_hash"]
    a_channels = alice.rpc("list_channels", {})["channels"]
    b_channels = bob.rpc("list_channels", {})["channels"]
    assert len(a_channels) == len(b_channels) == 1
    assert a_channels[0]["channel_id"] != b_channels[0]["channel_id"]
    for caller, other_hash, other_id in (
        (alice, b_hash, b_channels[0]["channel_id"]),
        (bob, a_hash, a_channels[0]["channel_id"]),
    ):
        for method, params in (
            ("get_invoice", {"payment_hash": other_hash}),
            ("get_channel_signing_status", {"channel_id": other_id}),
            ("update_channel", {"channel_id": other_id, "enabled": False}),
            ("shutdown_channel", {"channel_id": other_id, "force": True}),
        ):
            with pytest.raises(RpcError):
                caller.rpc(method, params)
    assert bob.rpc("get_invoice", {"payment_hash": b_hash})["status"] == "Open"
    assert alice.rpc("get_invoice", {"payment_hash": a_hash})["status"] == "Open"
    with pytest.raises(RpcError):
        env.public.rpc("get_invoice", {"payment_hash": a_hash})
    assert len(env.public.rpc("list_channels", {})["channels"]) == 2
    # Rejection alone is insufficient: neither owner's enabled flag, balances,
    # routing terms, funding identity or live protocol state may be mutated.
    stable_fields = (
        "channel_id",
        "channel_outpoint",
        "state",
        "enabled",
        "local_balance",
        "remote_balance",
        "offered_tlc_balance",
        "received_tlc_balance",
        "pending_tlcs",
        "tlc_expiry_delta",
        "tlc_fee_proportional_millionths",
        "shutdown_transaction_hash",
    )
    for owner, original in ((alice, a_channels[0]), (bob, b_channels[0])):
        assert original["enabled"] is True
        current = owner.rpc("list_channels", {})["channels"]
        assert len(current) == 1
        assert {key: current[0][key] for key in stable_fields} == {
            key: original[key] for key in stable_fields
        }


# TEST-MAP: LSP-009
def test_ensure_evict_idempotency_keeps_identity_and_channel(lsp_devnet):
    env = lsp_devnet
    tenant = env.register()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: tenant.ensure(), range(4)))
    assert all(r["tenant_id"] == tenant.tenant_id for r in results)
    assert counts(env.public) == (1, 1)
    env.open_hosted_channel(tenant)
    before = tenant.status()
    before_balance = env.snapshot(tenant)
    tenant.evict()
    tenant.evict()
    assert counts(env.public) == (1, 0)
    tenant.ensure()
    wait_for(
        lambda: tenant.status()["channel_online"],
        description="hosted channel reconnect",
    )
    after = tenant.status()
    assert before["private_channel_id"] == after["private_channel_id"]
    assert before["invoice_pubkey"] == after["invoice_pubkey"]
    assert len(tenant.rpc("list_channels", {})["channels"]) == 1
    assert env.snapshot(tenant) == before_balance


# TEST-MAP: LSP-010
def test_active_runtime_quota_requires_explicit_evict(lsp_devnet):
    env = lsp_devnet
    limited = env.create_node("limited", lsp=True, lsp_config={"max_active_tenants": 1})
    alice, bob = env.register(public_node=limited), env.register(public_node=limited)
    alice.ensure()
    with pytest.raises(RpcError):
        bob.ensure()
    assert alice.status()["runtime_status"] == "active"
    assert bob.status()["runtime_status"] == "cold"
    assert counts(limited) == (2, 1)
    alice.evict()
    bob.ensure()
    assert counts(limited) == (2, 1)
    assert bob.status()["runtime_status"] == "active"


# TEST-MAP: LSP-012
def test_operator_cold_observation_and_tenant_activation_are_distinct(lsp_devnet):
    env = lsp_devnet
    tenant = env.register()
    assert tenant.status()["runtime_status"] == "cold"
    assert counts(env.public) == (1, 0)
    assert len(tenants(env.public)) == 1
    assert counts(env.public) == (1, 0)
    tenant.rpc("list_channels", {})
    assert counts(env.public) == (1, 1)
    tenant.evict()
    with pytest.raises(RpcError):
        tenant.rpc("get_invoice", {"payment_hash": "0x" + "01" * 32})
    assert counts(env.public) == (1, 1)


# TEST-MAP: LSP-049
def test_attenuation_facts_cannot_change_authority_identity(lsp_devnet):
    from framework.lsp_signer import Signer

    env = lsp_devnet
    alice, bob = env.register(), env.register()
    env.open_hosted_channel(alice)
    env.open_hosted_channel(bob)
    own = invoice(alice)
    other = invoice(bob)
    own_hash = own["invoice"]["data"]["payment_hash"]
    other_hash = other["invoice"]["data"]["payment_hash"]
    owner_channel = bob.status()["private_channel_id"]
    expected = alice.rpc("list_channels", {})
    with Signer(env, "attenuation-helper") as helper:
        for node_fact in (bob.tenant_id, "local"):
            token = helper.command(
                "attenuate",
                token=alice.token,
                code=f'tenant("{bob.tenant_id}"); node("{node_fact}");',
            )["token"]
            assert env.public.rpc("list_channels", {}, token=token) == expected
            assert (
                env.public.rpc("get_invoice", {"payment_hash": own_hash}, token=token)[
                    "status"
                ]
                == "Open"
            )
            for method, params in (
                ("get_invoice", {"payment_hash": other_hash}),
                ("get_channel_signing_status", {"channel_id": owner_channel}),
                ("get_watchtower_signing_status", {"channel_id": owner_channel}),
                ("shutdown_channel", {"channel_id": owner_channel, "force": True}),
            ):
                with pytest.raises(RpcError):
                    env.public.rpc(method, params, token=token)
            assert (
                bob.rpc("get_invoice", {"payment_hash": other_hash})["status"] == "Open"
            )
            assert bob.rpc("get_channel_signing_status", {"channel_id": owner_channel})
        operator = helper.command(
            "attenuate",
            token=env.public.operator_token,
            code=f'tenant("{alice.tenant_id}");',
        )["token"]
        assert env.public.rpc("list_channels", {}, token=operator) == env.public.rpc(
            "list_channels", {}
        )
        with pytest.raises(RpcError):
            env.public.rpc("get_invoice", {"payment_hash": own_hash}, token=operator)
