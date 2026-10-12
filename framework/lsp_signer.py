"""Real production-SDK subprocess and explicit hosted-channel test operations.

No hidden background signer, state injection, or payment-preimage forwarding is
used. Each command is one local JSONL exchange with the pinned Rust SDK driver.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import selectors
import subprocess
import time

import pytest

from framework.lsp_devnet import CKB, PRODUCT_SHA, RpcClient, wait_for


class SdkError(RuntimeError):
    def __init__(self, operation, envelope):
        self.operation = operation
        self.envelope = envelope
        super().__init__(f"SDK {operation}: {envelope.get('error', envelope)}")


class Signer:
    """An independently owned real SDK process with a complete persistent store."""

    def __init__(self, env, name="signer", node=None):
        self.env = env
        self.node = node or env.public
        self.path = env.path / name
        self.path.mkdir(mode=0o700)
        self.store = self.path / "store"
        self.store.mkdir(mode=0o700)
        repo = Path(__file__).resolve().parents[1]
        self.binary = Path(
            os.getenv(
                "LSP_SDK_DRIVER_BIN",
                repo.parent
                / "runtime/adapter-target/debug/fiber-lsp-integration-sdk-driver",
            )
        )
        if not self.binary.is_file():
            raise RuntimeError(f"Required real SDK driver is missing: {self.binary}")
        self.process = None
        self.sequence = 0
        self.tenant = None
        self.last = None
        self.start()

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return self
        self.stderr = (self.path / "driver.stderr").open("ab")
        self.process = subprocess.Popen(
            [
                str(self.binary),
                "--rpc",
                self.node.rpc_url,
                "--store",
                str(self.store),
                "--operator-token",
                self.env.operator_token,
                "--ckb-rpc",
                self.env.ckb.rpc_url,
                "--contracts-dir",
                str(self.env.product / "tests/deploy/contracts"),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            cwd=self.path,
            bufsize=0,
        )
        return self

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.process is not None:
            self.process.stdin.close()
            self.process.stdout.close()
        self.stderr.close()
        self.process = None

    close = stop

    def restart(self):
        self.stop()
        return self.start()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()

    def raw(self, op, timeout=60, **fields):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("SDK driver is not running")
        self.sequence += 1
        self.process.stdin.write(
            (json.dumps({"id": self.sequence, "op": op, **fields}) + "\n").encode()
        )
        self.process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout):
                self.stop()  # Never reuse a stream whose response boundary is unknown.
                raise TimeoutError(f"SDK {op} did not return within {timeout}s")
            line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(
                f"SDK driver exited during {op}; code={self.process.poll()}"
            )
        result = json.loads(line)
        assert result["id"] == self.sequence, "SDK JSONL response sequence mismatch"
        self.last = result
        error = result.get("error")
        if isinstance(result.get("result"), dict):
            error = error or result["result"].get("error")
        if "Biscuit authorization timed out" in str(error):
            # This is evaluator timing failure, not a semantic rejection. In
            # particular, pytest.raises(SdkError) must never accept it as proof.
            pytest.fail(f"SDK {op}: unexpected Biscuit authorization timed out")
        return result

    def command(self, op, **fields):
        result = self.raw(op, **fields)
        if "error" in result:
            # Never put bearer tokens in assertion diagnostics.
            redacted = deepcopy(result)
            if self.tenant:
                redacted["error"] = str(redacted["error"]).replace(
                    self.tenant.token, "<tenant-token>"
                )
            redacted["error"] = str(redacted["error"]).replace(
                self.env.operator_token, "<operator-token>"
            )
            raise SdkError(op, redacted)
        return result["result"]

    def initialize(self):
        result = self.command("initialize")
        assert result["product_sha"] == PRODUCT_SHA
        self.tenant = SignerTenant(self.node, result)
        return result

    def status(self):
        return self.command("status")

    def snapshot(self):
        return self.command("snapshot")

    def signing_status(self, channel_id, watchtower=False):
        return self.command(
            "signing_status", channel_id=channel_id, watchtower=watchtower
        )["status"]

    def poll(self, capture=False, allow_error=False):
        result = self.command("poll", capture=capture)
        if not allow_error:
            assert result[
                "ok"
            ], f"SDK poll refused a real request: {result.get('error')}"
        return result

    def authorize(self, authorization):
        return self.command("authorize", authorization=authorization)


class SignerTenant(RpcClient):
    def __init__(self, node, status):
        super().__init__(node.rpc_url, status["tenant_token"])
        self.node = node
        self.tenant_id = status["tenant_id"]
        self.name = self.tenant_id
        self.root_pubkey = status["root_signer_pubkey"]

    def status(self):
        return self.node.env.tenant_status(self.tenant_id, public_node=self.node)

    @property
    def invoice_pubkey(self):
        return self.status()["invoice_pubkey"]

    @property
    def pubkey(self):
        return self.invoice_pubkey

    def ensure(self):
        return self.node.rpc("lsp_ensure_tenant", {"tenant_id": self.tenant_id})

    def evict(self):
        return self.node.rpc("lsp_evict_tenant", {"tenant_id": self.tenant_id})


def number(value):
    return int(value, 16) if isinstance(value, str) else value


def payment_terms(payment_hash, amount, inbound=True):
    now = int(time.time() * 1000)
    return {
        "payment_hash": payment_hash,
        "hash_algorithm": "sha256",
        "inbound": inbound,
        "amount": amount,
        "expires_at_ms": now + 3_600_000,
        "min_tlc_expiry_delta_ms": 86_400_000,
        "max_tlc_expiry_ms": now + 8 * 86_400_000,
    }


class ExternalChannel:
    """One wallet's explicit opening intent, independent funding wallet and SDK."""

    def __init__(self, env, name="signer", node=None, amount=500 * CKB):
        self.env = env
        self.node = node or env.public
        self.wallet = env.new_wallet()
        self.funding_script = self.wallet["lock_script"]
        self.signer = Signer(env, name, self.node)
        try:
            status = self.signer.initialize()
        except BaseException:
            self.signer.stop()
            raise
        self.tenant = self.signer.tenant
        self.params = {
            "pubkey": self.node.pubkey,
            "public": False,
            "funding_amount": hex(amount),
            "commitment_fee_rate": "0x3e8",
            "commitment_delay_epoch": "0x10000000001",
            "shutdown_script": deepcopy(self.funding_script),
            "funding_lock_script": deepcopy(self.funding_script),
            "external_channel_signer": status["channel_open_signer_material"],
        }
        self.opening = None
        self.channel_id = None
        self.funding_tx_hash = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.signer.stop()

    def open(self):
        self.opening = self.signer.command("open", params=self.params)
        self.channel_id = self.opening["channel_id"]
        assert self.signer.status()["bound_channel_ids"] == [self.channel_id]
        return self.opening

    def signed_funding(self, transaction=None):
        return self.node.rpc(
            "sign_external_funding_tx",
            {
                "unsigned_funding_tx": transaction
                or self.opening["unsigned_funding_tx"],
                "private_key": self.wallet["private_key"],
            },
        )["signed_funding_tx"]

    def fund(self, transaction=None):
        result = self.tenant.rpc(
            "submit_signed_funding_tx",
            {
                "channel_id": self.channel_id,
                "signed_funding_tx": transaction or self.signed_funding(),
            },
        )
        self.funding_tx_hash = result["funding_tx_hash"]
        return result

    def channel(self, client=None, include_closed=False):
        rows = (client or self.tenant).rpc(
            "list_channels", {"include_closed": include_closed}
        )["channels"]
        return next((row for row in rows if row["channel_id"] == self.channel_id), None)

    def drive(
        self, predicate, description="external signer progress", timeout=90, mine=True
    ):
        def step():
            self.signer.poll()
            if mine:
                self.env.mine(2)
            return predicate()

        return wait_for(step, timeout=timeout, description=description, interval=0.2)

    def ready(self):
        def both_ready():
            sides = [self.channel(), self.channel(self.node)]
            return all(c and c["state"]["state_name"] == "ChannelReady" for c in sides)

        self.drive(both_ready, "both external channel endpoints ChannelReady")
        assert self.tenant.status()["private_channel_id"] == self.channel_id
        assert (
            not self.channel()["is_public"] and not self.channel(self.node)["is_public"]
        )
        funding = self.env.ckb.rpc("get_transaction", [self.funding_tx_hash])
        assert funding["tx_status"]["status"] == "committed"
        tx = funding["transaction"]
        context = self.opening["opening_context"]
        local, peer = self.channel(), self.channel(self.node)
        assert not local["pending_tlcs"] and not peer["pending_tlcs"]
        assert local["local_balance"] == peer["remote_balance"]
        assert local["remote_balance"] == peer["local_balance"]
        assert number(local["local_balance"]) + number(
            context["local_reserved_ckb_amount"]
        ) == number(context["local_amount"])
        assert number(local["remote_balance"]) + number(
            context["remote_reserved_ckb_amount"]
        ) == number(context["remote_amount"])
        assert number(context["local_amount"]) == number(self.params["funding_amount"])
        assert number(tx["outputs"][0]["capacity"]) == number(
            context["local_amount"]
        ) + number(context["remote_amount"])
        assert tx["outputs"] == self.opening["unsigned_funding_tx"]["outputs"]
        assert tx["outputs_data"] == self.opening["unsigned_funding_tx"]["outputs_data"]
        live = self.env.ckb.rpc(
            "get_live_cell", [{"tx_hash": self.funding_tx_hash, "index": "0x0"}, True]
        )
        assert live["status"] == "live"
        assert (
            live["cell"]["output"] == self.opening["unsigned_funding_tx"]["outputs"][0]
        )
        assert len(self.env.channels(self.tenant)) == 1
        return self

    def create(self):
        self.open()
        self.fund()
        return self.ready()

    def pending(self, transitions=None, timeout=30):
        def read():
            status = self.signer.signing_status(self.channel_id)
            if status["type"] == "SignatureRequired" and (
                not transitions or status["transition"] in transitions
            ):
                return status
            return False

        return wait_for(
            read, timeout=timeout, description="real pending channel signature"
        )

    def recoverable(self, payment_hash):
        """Read real SDK recovery evidence; never accept an older matching state."""
        records = self.signer.command("recovery_status", channel_id=self.channel_id)[
            "records"
        ]
        latest = next(
            (
                record
                for record in reversed(records)
                if not record["reference"]["for_remote"]
                and record["complete_signature"]
            ),
            None,
        )
        return bool(
            latest
            and not latest["revoked"]
            and payment_hash in latest["payment_hashes"]
        )

    def hold_invoice(self, amount=1_000_000):
        preimage = "0x" + secrets.token_hex(32)
        payment_hash = "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
        terms = payment_terms(payment_hash, amount)
        self.signer.authorize(
            {
                "type": "Invoice",
                "channel_id": self.channel_id,
                "terms": terms,
                "preimage": preimage,
            }
        )
        result = self.tenant.rpc(
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
            "invoice": result["invoice_address"],
            "payment_hash": payment_hash,
            "preimage": preimage,
            "amount": amount,
            "terms": terms,
        }

    def release(self, invoice, target="Invoice", preimage=None):
        return self.signer.authorize(
            {
                "type": "ReleasePreimage",
                "channel_id": self.channel_id,
                "payment_hash": invoice["payment_hash"],
                "preimage": preimage or invoice["preimage"],
                "target": target,
            }
        )


def transaction_size_in_block(tx):
    """Independent Molecule size oracle for a JSON CKB transaction."""
    size_bytes = lambda value: len(bytes.fromhex(value.removeprefix("0x")))
    script_size = lambda script: 53 + size_bytes(script["args"])
    outputs = [
        24 + script_size(o["lock"]) + (script_size(o["type"]) if o.get("type") else 0)
        for o in tx["outputs"]
    ]
    dynvec = lambda sizes: 4 + (4 * len(sizes) + sum(sizes) if sizes else 0)
    raw_size = (
        28
        + 4
        + 4
        + 37 * len(tx["cell_deps"])
        + 4
        + 32 * len(tx["header_deps"])
        + 4
        + 44 * len(tx["inputs"])
        + dynvec(outputs)
        + dynvec([4 + size_bytes(x) for x in tx["outputs_data"]])
    )
    return 4 + 12 + raw_size + dynvec([4 + size_bytes(x) for x in tx["witnesses"]])
