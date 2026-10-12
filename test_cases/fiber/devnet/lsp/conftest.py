"""Fresh, real-process LSP state for every test; missing binaries are errors."""

import pytest

from framework.lsp_devnet import LspDevnet


@pytest.fixture
def lsp_devnet(tmp_path):
    env = LspDevnet(tmp_path / "hosted-lsp")
    try:
        env.start()
        yield env
    finally:
        env.stop()


@pytest.fixture
def lsp_env(lsp_devnet):
    return lsp_devnet
