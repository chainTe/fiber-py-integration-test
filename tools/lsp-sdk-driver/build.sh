#!/usr/bin/env bash
# Dependencies are intentionally the unchanged sibling product checkout.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
product=$(cd "$here/../../../fiber-lsp" && pwd)
expected=8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8
actual=$(git -C "$product" rev-parse HEAD)
[[ "$actual" = "$expected" ]] || { echo "Wrong product revision: $actual (expected $expected)" >&2; exit 2; }
git -C "$product" diff --quiet HEAD -- || { echo 'Product tracked source has modifications; refusing an unpinned build' >&2; exit 2; }
exec cargo +1.95.0 build --locked --manifest-path "$here/Cargo.toml" "$@"
