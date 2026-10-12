#!/usr/bin/env python3
"""Offline checks for the SDK driver's process protocol, not LSP integration."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

PRODUCT_SHA = "8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    binary = parser.parse_args().binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="lsp-sdk-driver-cli-") as directory:
        args = [
            str(binary),
            "--rpc",
            "http://127.0.0.1:1",
            "--store",
            directory,
            "--operator-token",
            "fixture-unused",
            "--ckb-rpc",
            "http://127.0.0.1:1",
            "--contracts-dir",
            "/unused",
        ]

        def call(commands):
            process = subprocess.run(
                args,
                input="".join(json.dumps(command) + "\n" for command in commands),
                text=True,
                capture_output=True,
                timeout=30,
                check=True,
            )
            rows = [json.loads(line) for line in process.stdout.splitlines()]
            assert len(rows) == len(commands), "One response per command is required"
            assert [row["id"] for row in rows] == [item["id"] for item in commands]
            assert not process.stderr, "Unexpected driver diagnostic output"
            return rows

        first = call(
            [
                {"id": 1, "op": "identity"},
                {"id": 2, "op": "snapshot"},
                {"id": 3, "op": "outbound_fulfillment", "preimage": "injected"},
                {"id": 4, "op": "shutdown"},
            ]
        )
        second = call(
            [
                {"id": 5, "op": "identity"},
                {"id": 6, "op": "snapshot"},
                {"id": 7, "op": "shutdown"},
            ]
        )
        assert first[0]["result"]["product_sha"] == PRODUCT_SHA
        assert "own get_payment" in first[2]["error"]
        assert first[0]["result"] == second[0]["result"], "Identity changed on restart"
        assert first[1]["result"] == second[1]["result"], "Store changed on restart"
        assert all(not row["observations"]["rpc_calls"] for row in first + second)
    print(
        "PASS: 7 JSONL commands across 2 actual process launches; identity/store "
        "preserved; injected payer preimage rejected before RPC; no RPC observed"
    )


if __name__ == "__main__":
    main()
