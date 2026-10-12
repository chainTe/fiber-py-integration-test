//! Independent lineage provider for the pinned contract's 57/58-byte formats.
//! Live cells, maturity and median time retain the exported DevChain checks.
use anyhow::{ensure, Context, Result};
use ckb_types::{
    packed::{CellDep, CellInput, CellOutput, OutPoint, Script, Transaction},
    prelude::*,
};
use fiber_lsp_sdk::{ChainVerifier, SignerError, VerifiedCell};
use fiber_lsp_sdk_agent::DevChain;
use fiber_types::{CommitmentContractFeatures, Hash256};
use serde::de::DeserializeOwned;
use serde_json::json;

pub(crate) struct LocalChain {
    inner: DevChain,
    url: String,
    client: reqwest::Client,
    funding_deps: Vec<CellDep>,
    commitment_lock: Script,
    pub(crate) settlement_deps: Vec<CellDep>,
}

impl LocalChain {
    pub(crate) async fn new(url: &str, contracts: &std::path::Path) -> Result<Self> {
        let mut inner = DevChain::new(url)?;
        // DevChain authenticates all three local binaries against genesis data.
        let settlement_deps = inner.settlement_deps(contracts).await?;
        let funding = settlement_deps[0]
            .clone()
            .as_builder()
            .out_point(
                settlement_deps[0]
                    .out_point()
                    .as_builder()
                    .index(6u32)
                    .build(),
            )
            .build();
        let funding_deps = vec![funding, settlement_deps[1].clone()];
        let commitment_lock = Script::new_builder()
            .code_hash(
                fiber_types::blake2b_hash_with_salt(
                    &std::fs::read(contracts.join("commitment-lock"))?,
                    &[],
                )
                .pack(),
            )
            .hash_type(ckb_types::core::ScriptHashType::Data2)
            .build();
        Ok(Self {
            inner,
            url: url.into(),
            funding_deps,
            commitment_lock,
            settlement_deps,
            client: reqwest::Client::builder()
                .no_proxy()
                .timeout(std::time::Duration::from_secs(10))
                .build()?,
        })
    }

    async fn call<T: DeserializeOwned>(
        &self,
        method: &str,
        params: serde_json::Value,
    ) -> Result<T> {
        let body: serde_json::Value = self
            .client
            .post(&self.url)
            .json(&json!({"jsonrpc":"2.0","id":1,"method":method,"params":params}))
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        ensure!(
            body.get("error").is_none(),
            "CKB {method}: {}",
            body["error"]
        );
        Ok(serde_json::from_value(body["result"].clone())?)
    }

    async fn committed(&self, hash: &ckb_types::packed::Byte32) -> Result<Transaction> {
        let hash: ckb_types::H256 = hash.unpack();
        let response: serde_json::Value = self.call("get_transaction", json!([hash])).await?;
        ensure!(
            response["tx_status"]["status"] == "committed",
            "transaction is not committed"
        );
        let tx: ckb_jsonrpc_types::TransactionView =
            serde_json::from_value(response["transaction"].clone())?;
        let header: ckb_jsonrpc_types::HeaderView = self
            .call("get_header", json!([response["tx_status"]["block_hash"]]))
            .await?;
        let canonical: ckb_jsonrpc_types::HeaderView = self
            .call("get_header_by_number", json!([header.inner.number]))
            .await?;
        ensure!(
            canonical.hash == header.hash,
            "transaction was reorganized out"
        );
        let packed: Transaction = tx.inner.into();
        ensure!(
            packed.calc_tx_hash() == hash.pack() && tx.hash == hash,
            "transaction hash mismatch"
        );
        ensure!(
            packed.raw().version() == 0u32.pack(),
            "unsupported transaction version"
        );
        Ok(packed)
    }

    async fn lineage(&self, source: Hash256, outpoint: &OutPoint) -> Result<()> {
        ensure!(
            outpoint.index() == 0u32.pack(),
            "commitment must be output zero"
        );
        let mut current = outpoint.clone();
        // Each step uses actual committed input zero / output zero, never LSP ancestry.
        // A cap bounds RPC work and fails closed on excessively long histories.
        for _ in 0..128 {
            let tx = self.committed(&current.tx_hash()).await?;
            let output = tx
                .raw()
                .outputs()
                .get(0)
                .context("missing commitment output")?;
            validate_lock(&output, &self.commitment_lock)?;
            if matches_commitment(source, &tx, &self.funding_deps) {
                return Ok(());
            }
            let parent = tx
                .raw()
                .inputs()
                .get(0)
                .context("missing ancestor")?
                .previous_output();
            ensure!(
                parent.index() == 0u32.pack(),
                "ancestor must be output zero"
            );
            let previous = self.committed(&parent.tx_hash()).await?;
            let previous_output = previous
                .raw()
                .outputs()
                .get(0)
                .context("missing ancestor output")?;
            validate_descendant(&output, &previous_output, &self.commitment_lock)?;
            current = parent;
        }
        anyhow::bail!("commitment ancestry exceeds fixture limit")
    }
}

fn validate_lock(output: &CellOutput, trusted: &Script) -> Result<()> {
    let lock = output.lock();
    let args = lock.args().raw_data();
    let features = CommitmentContractFeatures::from_lock_args(&args).map_err(anyhow::Error::msg)?;
    // Support only the formats emitted by the pinned node builder. The SDK's
    // parser is broader (58+zero); rejecting that shape is this fixture's scope,
    // not a claim that the production SDK or bundled contract rejects it.
    ensure!(
        features.lock_args_len() == args.len(),
        "unsupported fixture commitment feature layout"
    );
    ensure!(
        lock.code_hash() == trusted.code_hash() && lock.hash_type() == trusted.hash_type(),
        "untrusted commitment contract"
    );
    ensure!(args[56] <= 1, "unsupported commitment version");
    Ok(())
}

fn validate_descendant(output: &CellOutput, previous: &CellOutput, trusted: &Script) -> Result<()> {
    validate_lock(output, trusted)?;
    validate_lock(previous, trusted)?;
    let args = output.lock().args().raw_data();
    let previous_args = previous.lock().args().raw_data();
    ensure!(
        output.type_() == previous.type_()
            && args.len() == previous_args.len()
            && args[..36] == previous_args[..36]
            && args[57..] == previous_args[57..]
            && args[56] == 1,
        "not a commitment descendant"
    );
    Ok(())
}

fn matches_commitment(source: Hash256, tx: &Transaction, funding_deps: &[CellDep]) -> bool {
    // The recovery record commits the transaction with deps cleared. Broadcasting
    // installs independently verified funding deps; no other field may differ.
    tx.raw().cell_deps().into_iter().collect::<Vec<_>>() == funding_deps
        && (tx.calc_tx_hash() == ckb_types::packed::Byte32::from(source)
            || Hash256::from(fiber_types::compute_tx_message(tx)) == source)
}

fn invalid(error: impl std::fmt::Display) -> SignerError {
    SignerError::InvalidContent(format!("independent local lineage verification: {error}"))
}

#[async_trait::async_trait]
impl ChainVerifier for LocalChain {
    async fn live_cell(&self, outpoint: &OutPoint) -> Result<VerifiedCell, SignerError> {
        self.inner.live_cell(outpoint).await
    }
    async fn verify_commitment_lineage(
        &self,
        source: Hash256,
        outpoint: &OutPoint,
    ) -> Result<(), SignerError> {
        self.lineage(source, outpoint).await.map_err(invalid)
    }
    async fn verify_maturity(&self, inputs: &[CellInput]) -> Result<(), SignerError> {
        self.inner.verify_maturity(inputs).await
    }
    async fn median_time_ms(&self) -> Result<u64, SignerError> {
        self.inner.median_time_ms().await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn trusted() -> Script {
        Script::new_builder()
            .code_hash([7u8; 32].pack())
            .hash_type(ckb_types::core::ScriptHashType::Data2)
            .build()
    }
    fn output(args: Vec<u8>) -> CellOutput {
        CellOutput::new_builder()
            .lock(trusted().as_builder().args(args.pack()).build())
            .build()
    }
    fn args(len: usize, version: u8) -> Vec<u8> {
        let mut result = vec![0; len];
        result[56] = version;
        if len == 58 {
            result[57] = 1;
        }
        result
    }

    #[test]
    fn lineage_accepts_legacy_and_full_hash_with_invariant_features() {
        for len in [57, 58] {
            let previous = output(args(len, 0));
            let mut child_args = args(len, 1);
            child_args[36] = 42; // Settlement witness hash legitimately changes.
            assert!(validate_descendant(&output(child_args), &previous, &trusted()).is_ok());
        }
        // The SDK parser accepts 58+zero, but the pinned builder does not emit
        // it. This provider intentionally supports only generated fixture forms.
        let mut parent = args(58, 0);
        parent[57] = 0;
        let mut child = args(58, 1);
        child[57] = 0;
        assert!(validate_lock(&output(parent.clone()), &trusted()).is_err());
        assert!(validate_lock(&output(child.clone()), &trusted()).is_err());
        assert!(validate_descendant(&output(child), &output(parent), &trusted()).is_err());
    }

    #[test]
    fn lineage_rejects_lengths_unknown_or_changed_features_and_versions() {
        let previous = output(args(58, 0));
        for len in [0, 36, 56, 59] {
            assert!(validate_descendant(&output(vec![0; len]), &previous, &trusted()).is_err());
        }
        for feature in [0, 2, 255] {
            let mut value = args(58, 1);
            value[57] = feature;
            assert!(validate_descendant(&output(value), &previous, &trusted()).is_err());
        }
        assert!(validate_descendant(&output(args(57, 1)), &previous, &trusted()).is_err());
        for version in [0, 2, 255] {
            assert!(
                validate_descendant(&output(args(58, version)), &previous, &trusted()).is_err()
            );
        }
        assert!(
            validate_descendant(&output(args(58, 1)), &output(args(58, 2)), &trusted()).is_err()
        );
    }

    #[test]
    fn lineage_rejects_changed_prefix_contract_and_type() {
        let previous = output(args(58, 0));
        let child = output(args(58, 1));
        let mut changed = args(58, 1);
        changed[35] = 1;
        assert!(validate_descendant(&output(changed), &previous, &trusted()).is_err());
        for lock in [
            trusted().as_builder().code_hash([9u8; 32].pack()).build(),
            trusted()
                .as_builder()
                .hash_type(ckb_types::core::ScriptHashType::Type)
                .build(),
        ] {
            let changed = child
                .clone()
                .as_builder()
                .lock(lock.as_builder().args(args(58, 1).pack()).build())
                .build();
            assert!(validate_descendant(&changed, &previous, &trusted()).is_err());
        }
        let changed = child.as_builder().type_(Some(trusted()).pack()).build();
        assert!(validate_descendant(&changed, &previous, &trusted()).is_err());
    }

    #[test]
    fn lineage_template_requires_exact_known_funding_dependencies() {
        let template = ckb_types::core::TransactionBuilder::default()
            .input(CellInput::default())
            .output(output(args(58, 0)))
            .output_data(ckb_types::packed::Bytes::default())
            .build()
            .data();
        let source = template.calc_tx_hash().into();
        let deps = vec![CellDep::new_builder()
            .out_point(OutPoint::new_builder().index(6u32).build())
            .build()];
        let broadcast = template
            .clone()
            .as_builder()
            .raw(template.raw().as_builder().cell_deps(deps.clone()).build())
            .build();
        assert!(matches_commitment(source, &broadcast, &deps));
        assert!(!matches_commitment(source, &broadcast, &[]));
        assert!(!matches_commitment(source, &template, &deps));
        let changed = broadcast
            .clone()
            .as_builder()
            .raw(
                broadcast
                    .raw()
                    .as_builder()
                    .outputs(vec![output(args(58, 1))])
                    .build(),
            )
            .build();
        assert!(!matches_commitment(source, &changed, &deps));
    }
}
