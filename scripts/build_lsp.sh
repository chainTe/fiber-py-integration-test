#!/usr/bin/env bash
# Build only the exact user-selected product and the production-SDK test driver.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
product=${LSP_PRODUCT_DIR:-"$root/../fiber-lsp"}
expected=8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8
[[ -d "$product/.git" ]] || { echo "Check out https://github.com/chenyukang/fiber at $expected to $product" >&2; exit 2; }
[[ "$(git -C "$product" rev-parse HEAD)" == "$expected" ]] || { echo 'Wrong product commit; refusing to test a different revision' >&2; exit 2; }
git -C "$product" diff --quiet HEAD || { echo 'Product has tracked changes; refusing unpinned verification' >&2; exit 2; }
# Keep product debug assertions and dev RPCs while optimizing its unchanged
# key-encryption KDF and authorization dependencies. Cryptographic parameters
# and the authorization engine's exact 10 ms budget are retained.
cargo build --locked --manifest-path "$product/Cargo.toml" -p fiber-bin -p fiber-lsp-sdk-agent \
  --config 'profile.dev.package.scrypt.opt-level=3' \
  --config 'profile.dev.package.salsa20.opt-level=3' \
  --config 'profile.dev.package.biscuit-auth.opt-level=3'
bash "$root/tools/lsp-sdk-driver/build.sh"
