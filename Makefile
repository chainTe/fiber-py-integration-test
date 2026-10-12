.PHONY: prepare test clean docs

prepare:
	python3 -m venv venv
	. venv/bin/activate
	python3 -m pip install --upgrade pip
	pip install -r requirements.txt
	echo "install ckb"
	python3 -m download
	python3 -m download_fiber
	sh prepare.sh

prepare_develop_testnet:
	python3 -m venv venv
	. venv/bin/activate
	python3 -m pip install --upgrade pip
	pip install -r requirements.txt
	echo "install ckb"
	python3 -m download

	echo "install fiber"
	python3 -m download_fiber
	cp download/0.202.0/ckb-cli ./source/ckb-cli
	bash develop_fiber.sh


develop_prepare:
	python3 -m venv venv
	. venv/bin/activate
	python3 -m pip install --upgrade pip
	pip install -r requirements.txt
	echo "install ckb"
	python3 -m download

	python3 -m download_ckb_light_client
	echo "install ckb cli"
	bash develop_prepare.sh

test_cases := \
    test_cases/replace_rpc \
    test_cases/ckb_cli \
    test_cases/ckb2023 \
    test_cases/contracts \
    test_cases/example \
    test_cases/framework \
    test_cases/light_client \
    test_cases/mocking \
    test_cases/node_compatible \
    test_cases/rpc \
    test_cases/soft_fork \
    test_cases/issue \
    test_cases/tx_pool_refactor \
    test_cases/feature \
    test_cases/config \
    test_cases/miner \
    test_cases/get_fee_rate_statistics


fiber_test_cases := \
    test_cases/fiber/devnet/open_channel \
	test_cases/fiber/devnet/accept_channel \
	test_cases/fiber/devnet/cancel_invoice \
	test_cases/fiber/devnet/connect_peer \
	test_cases/fiber/devnet/disconnect_peer \
	test_cases/fiber/devnet/get_invoice \
	test_cases/fiber/devnet/graph_channels \
	test_cases/fiber/devnet/graph_nodes \
	test_cases/fiber/devnet/list_channels \
	test_cases/fiber/devnet/new_invoice \
	test_cases/fiber/devnet/send_payment \
	test_cases/fiber/devnet/shutdown_channel \
	test_cases/fiber/devnet/update_channel \
	test_cases/fiber/devnet/issue \
	test_cases/fiber/devnet/migration \
	test_cases/fiber/devnet/security \
	test_cases/fiber/devnet/watch_tower \
	test_cases/fiber/devnet/fee_stats \
	test_cases/fiber/devnet/fnn-cli

fiber_testnet_cases := \
	test_cases/fiber/testnet


fiber_mainnet_cases := \
	test_cases/fiber/mainnet

fiber_sync_state_cases := \
	test_cases/fiber/sync_state



test:
	@failed_cases=; \
    for test_case in $(test_cases); do \
        echo "Running tests for $$test_case"; \
        if ! bash test.sh "$$test_case"; then \
            echo "$$test_case" >> failed_test_cases.txt; \
        fi \
    done; \
    if [ -s failed_test_cases.txt ]; then \
        echo "Some test cases failed: $$(cat failed_test_cases.txt)"; \
        rm -f failed_test_cases.txt; \
        exit 1; \
    fi


fiber_testnet_test:
	@failed_cases=; \
    for test_case in $(fiber_testnet_cases); do \
        echo "Running tests for $$test_case"; \
        if ! bash test.sh "$$test_case"; then \
            echo "$$test_case" >> failed_test_cases.txt; \
        fi \
    done; \
    if [ -s failed_test_cases.txt ]; then \
        echo "Some test cases failed: $$(cat failed_test_cases.txt)"; \
        rm -f failed_test_cases.txt; \
        exit 1; \
    fi

fiber_mainnet_test:
	@failed_cases=; \
    for test_case in $(fiber_mainnet_cases); do \
        echo "Running tests for $$test_case"; \
        if ! bash test.sh "$$test_case"; then \
            echo "$$test_case" >> failed_test_cases.txt; \
        fi \
    done; \
    if [ -s failed_test_cases.txt ]; then \
        echo "Some test cases failed: $$(cat failed_test_cases.txt)"; \
        rm -f failed_test_cases.txt; \
        exit 1; \
    fi

fiber_sync_state_test:
	@failed_cases=; \
    for test_case in $(fiber_sync_state_cases); do \
        echo "Running tests for $$test_case"; \
        if ! bash test.sh "$$test_case"; then \
            echo "$$test_case" >> failed_test_cases.txt; \
        fi \
    done; \
    if [ -s failed_test_cases.txt ]; then \
        echo "Some test cases failed: $$(cat failed_test_cases.txt)"; \
        rm -f failed_test_cases.txt; \
        exit 1; \
    fi

fiber_test:
	@failed_cases=; \
    for test_case in $(fiber_test_cases); do \
        echo "Running tests for $$test_case"; \
        if ! bash test.sh "$$test_case"; then \
            echo "$$test_case" >> failed_test_cases.txt; \
        fi \
    done; \
    if [ -s failed_test_cases.txt ]; then \
        echo "Some test cases failed: $$(cat failed_test_cases.txt)"; \
        rm -f failed_test_cases.txt; \
        exit 1; \
    fi

fiber_test_demo:
	@failed_cases=; \
	echo "Running tests for $$FIBER_TEST_DEMO"; \
    for test_case in $(FIBER_TEST_DEMO); do \
        echo "Running tests for $$test_case"; \
        if ! bash test.sh "$$test_case"; then \
            echo "$$test_case" >> failed_test_cases.txt; \
        fi \
    done; \
    if [ -s failed_test_cases.txt ]; then \
        echo "Some test cases failed: $$(cat failed_test_cases.txt)"; \
        rm -f failed_test_cases.txt; \
        exit 1; \
    fi

develop_test:
	@failed_cases=; \
    for test_case in $(TestCases); do \
        echo "Running tests for $$test_case"; \
        if ! bash test.sh "$$test_case"; then \
            echo "$$test_case" >> failed_test_cases.txt; \
        fi \
    done; \
    if [ -s failed_test_cases.txt ]; then \
        echo "Some test cases failed: $$(cat failed_test_cases.txt)"; \
        rm -f failed_test_cases.txt; \
        exit 1; \
    fi



clean:
	- pkill ckb
	- pkill fnn
	- rm -rf tmp
	- rm -rf report

docs:
	python -m pytest --docs=docs/soft.md --doc-type=md test_cases/soft_fork

# Isolated Hosted LSP suite. See test_cases/fiber/devnet/lsp/README.md.
.PHONY: lsp_test
lsp_test:
	python -m pytest -o addopts= -vv --tb=short --junitxml=reports/hosted-lsp/junit.xml test_cases/fiber/devnet/lsp
