"""Isolated, real-process devnet for the pinned hosted-LSP integration suite.

The genesis contracts and the public *test-only* Biscuit key/token come from the
product checkout. No prepacked chain snapshot, shared ports, global process
cleanup, or upstream shell startup scripts are used.
"""

from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from typing import Callable

import pytest
import requests
import toml
import yaml
from coincurve import PrivateKey

CKB = 100_000_000
PRODUCT_SHA = "8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8"
SECP_CODE_HASH = "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8"
_DEFAULT_TOKEN = object()


def ckb_hash(data: bytes) -> bytes:
    """CKB's personalized Blake2b-256, also used by TenantRegistryPayload."""
    return hashlib.blake2b(data, digest_size=32, person=b"ckb-default-hash").digest()


def _secret_bytes(value) -> bytes:
    return bytes.fromhex(value.removeprefix("0x")) if isinstance(value, str) else value


def root_pubkey(root_secret) -> str:
    """RPC public keys are compressed hexadecimal without a 0x prefix."""
    return PrivateKey(_secret_bytes(root_secret)).public_key.format().hex()


def registry_proof(root_secret, challenge: dict) -> dict:
    """Sign the product's exact v1 RootSigner registration payload independently."""
    payload = (
        b"fiber-hosted-lsp-tenant-registry/v1"
        + bytes.fromhex(challenge["lsp_node_id"].removeprefix("0x"))
        + bytes.fromhex(challenge["root_signer_pubkey"].removeprefix("0x"))
        + bytes.fromhex(challenge["nonce"].removeprefix("0x"))
    )
    # Recoverable signatures have an extra recovery byte; RPC wants compact r||s.
    signature = PrivateKey(_secret_bytes(root_secret)).sign_recoverable(
        ckb_hash(payload), hasher=None
    )[:64]
    return {
        "root_signer_pubkey": challenge["root_signer_pubkey"],
        "nonce": challenge["nonce"],
        "signature": "0x" + signature.hex(),
    }


class RpcError(RuntimeError):
    """Preserve the full HTTP/JSON-RPC failure instead of flattening its data."""

    def __init__(self, method, url, http_status, body):
        self.method = method
        self.url = url
        self.http_status = http_status
        self.body = body
        self.error = body.get("error") if isinstance(body, dict) else body
        super().__init__(f"{method} at {url}: HTTP {http_status}: {body!r}")


class RpcClient:
    def __init__(self, rpc_url: str, token=None):
        self.rpc_url = rpc_url
        self.token = token
        self.last_http_status = None

    def raw_rpc(self, method, params=None, token=_DEFAULT_TOKEN):
        selected = self.token if token is _DEFAULT_TOKEN else token
        headers = {"Authorization": f"Bearer {selected}"} if selected else {}
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params if isinstance(params, list) else [params]
        response = requests.post(self.rpc_url, json=body, headers=headers, timeout=20)
        self.last_http_status = response.status_code
        try:
            return response.json()
        except ValueError:
            return {
                "error": {"http_status": response.status_code, "body": response.text}
            }

    def rpc(self, method, params=None, token=_DEFAULT_TOKEN):
        body = self.raw_rpc(method, params, token=token)
        error = body.get("error") if isinstance(body, dict) else None
        if (
            isinstance(error, dict)
            and error.get("message") == "Unauthorized: Biscuit authorization timed out"
        ):
            # An authorizer runtime timeout is not a tested policy denial. A
            # pytest outcome bypasses even broad raises(Exception) assertions,
            # as well as our RPC polling loops; never retry this response.
            pytest.fail(
                f"Unexpected Biscuit authorization runtime timeout: {method} at "
                f"{self.rpc_url}: HTTP {self.last_http_status}: {body!r}",
                pytrace=False,
            )
        if self.last_http_status != 200 or "error" in body:
            raise RpcError(method, self.rpc_url, self.last_http_status, body)
        if "result" not in body:
            raise RpcError(method, self.rpc_url, self.last_http_status, body)
        return body["result"]


def wait_for(predicate: Callable, timeout=30, description="condition", interval=0.2):
    """Bounded polling; assertion failures in a predicate are never swallowed."""
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            last = predicate()
            if last:
                return last
        except (requests.RequestException, RpcError) as error:
            last = error
        time.sleep(interval)
    raise AssertionError(
        f"Timed out after {timeout}s waiting for {description}; last={last!r}"
    )


class Node(RpcClient):
    def __init__(self, env, name, key, lsp=False, lsp_config=None):
        self.env = env
        self.name = name
        self.path = env.path / name
        self.path.mkdir()
        self.is_lsp = lsp
        self.operator_token = env.operator_token if lsp else None
        self.rpc_port, self.p2p_port = env.reserve_port(), env.reserve_port()
        super().__init__(f"http://127.0.0.1:{self.rpc_port}", self.operator_token)
        self.process = None
        self.log_handle = None
        self.log_path = self.path / "fnn.log"
        self._info = None
        (self.path / "ckb").mkdir()
        (self.path / "ckb" / "key").write_text(key.hex())
        (self.path / "ckb" / "key").chmod(0o600)
        self.config = {
            "services": ["fiber", "rpc", "ckb"] + (["lsp"] if lsp else []),
            "fiber": {
                "chain": str(env.spec_path),
                "listening_addr": f"/ip4/127.0.0.1/tcp/{self.p2p_port}",
                "announced_addrs": [f"/ip4/127.0.0.1/tcp/{self.p2p_port}"],
                "announced_node_name": name,
                "auto_announce_node": True,
                "announce_private_addr": True,
                "open_channel_auto_accept_min_ckb_funding_amount": 0,
                "auto_accept_channel_ckb_funding_amount": 500 * CKB,
                "watchtower_check_interval_seconds": 1,
                "gossip_store_maintenance_interval_ms": 1000,
                "gossip_network_maintenance_interval_ms": 1000,
            },
            "rpc": {
                "listening_addr": f"127.0.0.1:{self.rpc_port}",
                "enabled_modules": [
                    "channel",
                    "payment",
                    "graph",
                    "info",
                    "invoice",
                    "peer",
                    "pubsub",
                    "watchtower",
                    "dev",
                    "lsp",
                ],
            },
            "ckb": {
                "rpc_url": env.ckb.rpc_url,
                "tx_tracing_polling_interval_ms": 100,
            },
        }
        if lsp:
            self.config["rpc"].update(
                {
                    "biscuit_public_key": "ed25519/17b172749be74276f0ed35a5d0685752684a3c5722114bba447a2f301136db79",
                    "biscuit_private_key_path": str(
                        env.product / "tests/nodes/biscuit-test-private-key"
                    ),
                }
            )
            self.config["lsp"] = dict(lsp_config or {})

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return self
        (self.path / "config.yml").write_text(yaml.safe_dump(self.config))
        self.log_handle = self.log_path.open("a")
        child_env = os.environ.copy()
        # Do not allow ambient Fiber settings to override this isolated config.
        for key in list(child_env):
            if key.startswith(("FIBER_", "CKB_", "LSP_", "RPC_")):
                child_env.pop(key)
        child_env.update(
            {
                "FIBER_SECRET_KEY_PASSWORD": "hosted-lsp-devnet-test-only",
                "RUST_LOG": os.getenv("LSP_TEST_RUST_LOG", "info"),
            }
        )
        self.env.release_port(self.rpc_port)
        self.env.release_port(self.p2p_port)
        self.process = subprocess.Popen(
            [str(self.env.fnn_binary), "-d", str(self.path)],
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            env=child_env,
            cwd=self.path,
        )

        def ready():
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"{self.name} exited {self.process.returncode}: {self.log_tail()}"
                )
            self._info = self.rpc("node_info")
            return self._info

        # Debug builds execute the production password KDF without optimization.
        # This remains bounded, and does not weaken or replace that KDF.
        wait_for(
            ready,
            timeout=float(os.getenv("LSP_TEST_START_TIMEOUT", "300")),
            description=f"{self.name} RPC readiness",
        )
        return self

    def stop(self):
        _stop_process(self.process)
        if self.log_handle:
            self.log_handle.close()
        self.process = None

    def restart(self, lsp_config=None):
        self.stop()
        if lsp_config:
            self.config.setdefault("lsp", {}).update(lsp_config)
        return self.start()

    def log_tail(self):
        return self.log_path.read_text(errors="replace")[-8000:]

    @property
    def pubkey(self):
        return self._info["pubkey"]

    @property
    def address(self):
        return self._info["addresses"][0].replace("0.0.0.0", "127.0.0.1")

    def connect(self, other):
        result = self.rpc("connect_peer", {"address": other.address})
        wait_for(
            lambda: any(
                peer["pubkey"] == other.pubkey
                for peer in self.rpc("list_peers")["peers"]
            )
            and any(
                peer["pubkey"] == self.pubkey
                for peer in other.rpc("list_peers")["peers"]
            ),
            description=f"{self.name} and {other.name} peer sessions",
        )
        return result


class Tenant(RpcClient):
    def __init__(self, node: Node, registration: dict, root_secret=None):
        super().__init__(node.rpc_url, registration["access_token"])
        self.node = node
        self.registration = registration
        self.tenant_id = registration["tenant"]["tenant_id"]
        self.invoice_pubkey = registration["tenant"]["invoice_pubkey"]
        self.root_secret = root_secret
        self.root_pubkey = registration["tenant"].get("root_signer_pubkey")

    @property
    def pubkey(self):
        return self.invoice_pubkey

    def ensure(self):
        return self.node.rpc("lsp_ensure_tenant", {"tenant_id": self.tenant_id})

    def evict(self):
        return self.node.rpc("lsp_evict_tenant", {"tenant_id": self.tenant_id})

    def status(self):
        # This operator read is cold; tenant-scoped ordinary RPC would activate it.
        return self.node.env.tenant_status(self.tenant_id, public_node=self.node)


def _stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class LspDevnet:
    """One fresh CKB chain and independently owned FNN processes per test."""

    root_pubkey = staticmethod(root_pubkey)
    registry_proof = staticmethod(registry_proof)

    def __init__(
        self, path, product=None, fnn_binary=None, ckb_binary=None, ckb_cli=None
    ):
        self.path = Path(path).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        repo = Path(__file__).resolve().parents[1]
        self.product = Path(
            product or os.getenv("LSP_PRODUCT_DIR", repo.parent / "fiber-lsp")
        ).resolve()
        runtime = repo.parent / "runtime/ckb_v0.202.0_x86_64-unknown-linux-gnu-portable"
        runtime_fnn = repo.parent / "runtime/target/debug/fnn"
        default_fnn = (
            runtime_fnn if runtime_fnn.is_file() else self.product / "target/debug/fnn"
        )
        self.fnn_binary = Path(
            fnn_binary or os.getenv("LSP_FNN_BIN", default_fnn)
        ).resolve()
        self.ckb_binary = Path(
            ckb_binary or os.getenv("LSP_CKB_BIN", runtime / "ckb")
        ).resolve()
        self.ckb_cli = Path(
            ckb_cli or os.getenv("LSP_CKB_CLI_BIN", runtime / "ckb-cli")
        ).resolve()
        for executable in (self.fnn_binary, self.ckb_binary, self.ckb_cli):
            if not executable.is_file():
                raise RuntimeError(
                    f"Required real devnet executable is missing: {executable}"
                )
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.product, text=True
        ).strip()
        if sha != PRODUCT_SHA:
            raise RuntimeError(
                f"Hosted-LSP fixture requires product {PRODUCT_SHA}, found {sha}"
            )
        self.nodes = []
        self._ports = {}
        self._allocated_ports = set()
        self.ckb_process = None
        self.ckb_log_handle = None
        self._payer = self._receiver = None
        self._keys = [PrivateKey().secret for _ in range(16)]
        self._wallet_keys = [PrivateKey().secret for _ in range(16)]
        self._next_wallet = 0
        self.spec_path = self.path / "dev.toml"
        bru = (self.product / "tests/bruno/environments/test.bru").read_text()
        self.operator_token = re.search(
            r"^  LSP_OPERATOR_TOKEN: (\S+)$", bru, re.M
        ).group(1)
        self.ckb = RpcClient(f"http://127.0.0.1:{self.reserve_port()}")

    def reserve_port(self):
        while True:
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            if port not in self._allocated_ports:
                self._allocated_ports.add(port)
                self._ports[port] = sock
                return port
            sock.close()

    def release_port(self, port):
        sock = self._ports.pop(port, None)
        if sock:
            sock.close()

    def start(self):
        ckb_dir = self.path / "ckb"
        rpc_port = int(self.ckb.rpc_url.rsplit(":", 1)[1])
        p2p_port = self.reserve_port()
        result = subprocess.run(
            [
                str(self.ckb_binary),
                "init",
                "-C",
                str(ckb_dir),
                "-c",
                "dev",
                "--force",
                "--rpc-port",
                str(rpc_port),
                "--p2p-port",
                str(p2p_port),
                "--ba-arg",
                "0xc8328aabcd9b9e8e64fbc566c4385c3bdeb219d7",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError(f"CKB init failed: {result.stdout}\n{result.stderr}")
        spec_source = self.product / "tests/nodes/deployer/dev.toml"
        spec = toml.loads(spec_source.read_text())
        contracts = {}
        for cell in spec["genesis"]["system_cells"]:
            if "file" in cell["file"]:
                path = (spec_source.parent / cell["file"]["file"]).resolve()
                cell["file"]["file"] = str(path)
                contracts[path.name] = "0x" + ckb_hash(path.read_bytes()).hex()
        # Preserve genesis contract ordering while adding isolated dev-only funds.
        for key in self._keys + self._wallet_keys:
            lock_args = "0x" + ckb_hash(PrivateKey(key).public_key.format())[:20].hex()
            for _ in range(8):
                spec["genesis"]["issued_cells"].append(
                    {
                        "capacity": 1_000_000 * CKB,
                        "lock": {
                            "code_hash": SECP_CODE_HASH,
                            "args": lock_args,
                            "hash_type": "type",
                        },
                    }
                )
        self.spec_path.write_text(toml.dumps(spec))
        shutil.copy2(self.spec_path, ckb_dir / "specs/dev.toml")
        config_file = ckb_dir / "ckb.toml"
        config = toml.loads(config_file.read_text())
        config["network"]["listen_addresses"] = [f"/ip4/127.0.0.1/tcp/{p2p_port}"]
        modules = config["rpc"]["modules"]
        if "IntegrationTest" not in modules:
            modules.append("IntegrationTest")
        config_file.write_text(toml.dumps(config))
        self.release_port(rpc_port)
        self.release_port(p2p_port)
        self.ckb_log_handle = (ckb_dir / "process.log").open("a")
        self.ckb_process = subprocess.Popen(
            [str(self.ckb_binary), "run", "-C", str(ckb_dir), "--indexer"],
            stdout=self.ckb_log_handle,
            stderr=subprocess.STDOUT,
        )

        def ready():
            if self.ckb_process.poll() is not None:
                log = (ckb_dir / "process.log").read_text(errors="replace")[-8000:]
                raise RuntimeError(f"CKB exited {self.ckb_process.returncode}: {log}")
            return self.ckb.rpc("get_block_by_number", ["0x0"])

        genesis = wait_for(ready, timeout=30, description="CKB genesis RPC")
        for index, name in (
            (5, "auth"),
            (6, "funding-lock"),
            (7, "commitment-lock"),
            (8, "simple_udt"),
        ):
            actual = (
                "0x"
                + ckb_hash(
                    bytes.fromhex(genesis["transactions"][0]["outputs_data"][index][2:])
                ).hex()
            )
            assert (
                actual == contracts[name]
            ), f"Wrong genesis {name} dependency at output {index}"
        (self.path / "provenance.json").write_text(
            json.dumps(
                {
                    "product_sha": PRODUCT_SHA,
                    "genesis_hash": genesis["header"]["hash"],
                    "contracts": contracts,
                    "fnn_binary": str(self.fnn_binary),
                    "ckb_binary": str(self.ckb_binary),
                },
                indent=2,
            )
        )
        self.mine(20)
        self.wait_indexer()
        self.public = self.create_node("public-t", lsp=True)
        self.lsp = self.public
        return self

    def stop(self):
        for node in reversed(self.nodes):
            node.stop()
        _stop_process(self.ckb_process)
        if self.ckb_log_handle:
            self.ckb_log_handle.close()
        for sock in self._ports.values():
            sock.close()
        self._ports.clear()

    def create_node(self, name=None, lsp=False, lsp_config=None):
        index = len(self.nodes)
        if index >= len(self._keys):
            raise ValueError(
                "This isolated genesis provides at most 16 public node wallets"
            )
        node = Node(self, name or f"node-{index}", self._keys[index], lsp, lsp_config)
        self.nodes.append(node)
        return node.start()

    add_node = create_node

    def new_wallet(self):
        """Return one independent, genesis-funded, devnet-only signing wallet."""
        if self._next_wallet >= len(self._wallet_keys):
            raise ValueError(
                "This isolated genesis provides at most 16 SDK test wallets"
            )
        key = self._wallet_keys[self._next_wallet]
        self._next_wallet += 1
        pubkey = root_pubkey(key)
        result = subprocess.run(
            [
                str(self.ckb_cli),
                "util",
                "key-info",
                "--pubkey",
                pubkey,
                "--local-only",
                "--output-format",
                "json",
            ],
            env={**os.environ, "HOME": str(self.path)},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        info = json.loads(result.stdout[result.stdout.index("{") :])
        return {
            "private_key": "0x" + key.hex(),
            "pubkey": pubkey,
            "address": info["address"]["testnet"],
            "lock_script": {
                "code_hash": SECP_CODE_HASH,
                "hash_type": "type",
                "args": info["lock_arg"],
            },
        }

    @property
    def payer(self):
        if self._payer is None:
            self._payer = self.create_node("payer")
        return self._payer

    @property
    def receiver(self):
        if self._receiver is None:
            self._receiver = self.create_node("receiver")
        return self._receiver

    def restart(self, node, lsp_config=None):
        return node.restart(lsp_config=lsp_config)

    def mine(self, blocks=1):
        for _ in range(blocks):
            parent = self.ckb.rpc("get_tip_header")
            height = int(parent["number"], 16) + 1

            def current_template():
                template = self.ckb.rpc("get_block_template", [])
                tip = self.ckb.rpc("get_tip_header")
                assert tip["hash"] == parent["hash"], (
                    "Unexpected concurrent miner changed this isolated chain: "
                    f"expected parent {parent['hash']}, observed {tip['hash']}"
                )
                if (
                    template["parent_hash"] == parent["hash"]
                    and int(template["number"], 16) == height
                ):
                    return template
                return False

            # CKB's tx-pool template can lag the verified canonical tip. Plain
            # generate_block may then create an uncle even after a prior tip
            # wait. Submit only an observed matching template, exactly once.
            # This RPC retains normal block verification; errors propagate.
            template = wait_for(
                current_template,
                timeout=20,
                description=f"mining template for canonical child {height}",
                interval=0.02,
            )
            block_hash = self.ckb.rpc("generate_block_with_template", [template])
            wait_for(
                lambda: self.ckb.rpc("get_tip_header")["hash"] == block_hash,
                timeout=20,
                description=f"mined block {block_hash} adopted",
                interval=0.02,
            )
            tip = self.ckb.rpc("get_tip_header")
            assert int(tip["number"], 16) == height
            assert tip["parent_hash"] == parent["hash"]

    def mine_epochs(self, epochs=1):
        def canonical_epoch():
            value = int(self.ckb.rpc("get_tip_header")["epoch"], 16)
            number = value & 0xFFFFFF
            index = (value >> 24) & 0xFFFF
            length = (value >> 40) & 0xFFFF
            return Fraction(number) + Fraction(index, length or 1)

        target = canonical_epoch() + epochs
        result = self.ckb.rpc("generate_epochs", [hex(epochs)])
        # CKB returns the last processed template's epoch. Require the real
        # canonical chain to reach maturity before returning to watchtower tests.
        wait_for(
            lambda: canonical_epoch() >= target,
            timeout=20,
            description=f"canonical epoch reaches {target}",
            interval=0.02,
        )
        return result

    def tick(self):
        self.mine(1)

    def wait_indexer(self):
        height = self.ckb.rpc("get_tip_block_number")
        return wait_for(
            lambda: (self.ckb.rpc("get_indexer_tip") or {}).get("block_number")
            == height,
            description="CKB indexer synchronization",
        )

    def wait(self, predicate, description="condition", timeout=30, mine=False):
        def check():
            if mine:
                self.tick()
            return predicate()

        return wait_for(check, timeout=timeout, description=description)

    def register(self, root_secret=None, public_node=None):
        node = public_node or self.public
        secret = (
            _secret_bytes(root_secret)
            if root_secret is not None
            else PrivateKey().secret
        )
        challenge = node.rpc(
            "lsp_get_tenant_registry_nonce",
            {"root_signer_pubkey": root_pubkey(secret)},
        )
        result = node.rpc("lsp_register_tenant", registry_proof(secret, challenge))
        return Tenant(node, result, secret)

    register_tenant = register

    def tenant_status(self, tenant_id, public_node=None):
        node = public_node or self.public
        tenants = node.rpc("lsp_list_tenants")["tenants"]
        return next((item for item in tenants if item["tenant_id"] == tenant_id), None)

    def channels(self, client):
        return client.rpc("list_channels", {})["channels"]

    def open_channel(self, left, right, amount=500 * CKB, public=False):
        left.connect(right)
        before = {channel["channel_id"] for channel in self.channels(left)}
        params = {
            "pubkey": right.pubkey,
            "funding_amount": hex(amount),
            "public": public,
        }
        # list_peers exposes connected transports, not the asynchronous Init
        # handshake. This one pre-mutation setup error is safe to retry. Do not
        # retry other failures or use this helper for opening rejection tests.
        deadline = time.monotonic() + 15
        while True:
            try:
                left.rpc("open_channel", params)
                break
            except RpcError as error:
                if (
                    "waiting for peer to send Init message" not in str(error.error)
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(0.1)

        def ready():
            self.mine(2)
            for channel in self.channels(left):
                if channel["channel_id"] in before or channel["pubkey"] != right.pubkey:
                    continue
                if channel["state"]["state_name"] == "ChannelReady":
                    other = next(
                        (
                            item
                            for item in self.channels(right)
                            if item["channel_id"] == channel["channel_id"]
                        ),
                        None,
                    )
                    if other and other["state"]["state_name"] == "ChannelReady":
                        return channel["channel_id"]
            return False

        return wait_for(
            ready,
            timeout=90,
            description=f"{left.name} ↔ {right.name} ChannelReady",
            interval=0.3,
        )

    def open_hosted_channel(self, tenant, public_node=None, amount=500 * CKB):
        node = public_node or tenant.node
        tenant.ensure()
        node.rpc(
            "open_channel",
            {
                "pubkey": tenant.invoice_pubkey,
                "funding_amount": hex(amount),
                "public": False,
            },
        )

        def ready():
            self.mine(2)
            status = tenant.status()
            if status["channel_online"] and status["private_channel_id"]:
                channel_id = status["private_channel_id"]
                sides = (self.channels(tenant), self.channels(node))
                if all(
                    any(
                        c["channel_id"] == channel_id
                        and c["state"]["state_name"] == "ChannelReady"
                        for c in channels
                    )
                    for channels in sides
                ):
                    return channel_id
            return False

        return wait_for(
            ready, timeout=90, description="hosted private ChannelReady", interval=0.3
        )

    def wait_payment(self, client, payment_hash, status="Success", timeout=60):
        def ready():
            result = client.rpc("get_payment", {"payment_hash": payment_hash})
            if result["status"] == status:
                return result
            if result["status"] in ("Success", "Failed"):
                raise AssertionError(f"Expected payment {status}, observed {result!r}")
            return False

        return wait_for(
            ready, timeout=timeout, description=f"payment {payment_hash}: {status}"
        )

    def wait_delivery(self, payment_hash, status, public_node=None, timeout=60):
        node = public_node or self.public

        def ready():
            result = node.rpc(
                "lsp_get_payment_delivery", {"payment_hash": payment_hash}
            )
            return result if result["status"] == status else False

        return wait_for(
            ready, timeout=timeout, description=f"delivery {payment_hash}: {status}"
        )

    def snapshot(self, client):
        """Exact integer balance/TLC snapshot, scoped to the selected RPC identity."""
        return {
            channel["channel_id"]: {
                field: int(channel[field], 16)
                for field in (
                    "local_balance",
                    "remote_balance",
                    "offered_tlc_balance",
                    "received_tlc_balance",
                )
            }
            | {"pending_tlcs": channel["pending_tlcs"], "state": channel["state"]}
            for channel in self.channels(client)
        }

    def fund_address(self, address, capacity_ckb=100_000):
        """Fund an SDK-controlled dev wallet from the checked-in dev genesis key."""
        self.wait_indexer()
        result = subprocess.run(
            [
                str(self.ckb_cli),
                "--url",
                self.ckb.rpc_url,
                "--output-format",
                "json",
                "wallet",
                "transfer",
                "--to-address",
                address,
                "--capacity",
                str(capacity_ckb),
                "--fee-rate",
                "2000",
                "--privkey-path",
                str(self.product / "tests/nodes/deployer/ckb/plain_key"),
            ],
            env={**os.environ, "HOME": str(self.path)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(
                f"Devnet wallet funding failed: {result.stdout}\n{result.stderr}"
            )
        hashes = re.findall(r"0x[0-9a-fA-F]{64}", result.stdout)
        if not hashes:
            raise RuntimeError(f"No funding transaction hash: {result.stdout}")
        tx_hash = hashes[-1]
        self.wait(
            lambda: self.ckb.rpc("get_transaction", [tx_hash])["tx_status"]["status"]
            == "committed",
            description="SDK dev wallet funding confirmed",
            mine=True,
        )
        self.wait_indexer()
        return tx_hash
