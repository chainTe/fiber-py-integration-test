//! Split prepare/confirm controls using production SDK and independent chain evidence.
use anyhow::{anyhow, Context, Result};
use ckb_types::prelude::*;
use fiber_lsp_sdk::{ChainVerifier, OnchainSpendAuthorization, ProcessOutcome, SigningPolicy};
use fiber_lsp_sdk_agent::{FiberRpc, FileSignerStore};
use fiber_types::Hash256;
use serde_json::{json, Value};

use crate::{chain::LocalChain, string, Driver};

impl Driver {
    pub(crate) async fn watchtower_command(&mut self, command: &Value) -> Result<Value> {
        let channel: Hash256 = string(command, "channel_id")?.parse()?;
        let session = self.session(SigningPolicy::Manual).await?;
        let chain = LocalChain::new(&self.args.ckb_rpc, &self.args.contracts_dir).await?;
        let status = if let Some(status) = command.get("status") {
            serde_json::from_value(status.clone())?
        } else {
            self.rpc
                .get_watchtower_signing_status(
                    session.tenant_token().context("not registered")?,
                    channel.into(),
                )
                .await?
                .status
        };
        if command["op"] == "watchtower_prepare" {
            let authorization: OnchainSpendAuthorization = serde_json::from_value(
                command
                    .get("authorization")
                    .context("prepare requires fixed independently approved authorization")?
                    .clone(),
            )?;
            match session
                .handle_watchtower_status_verified(channel, status, authorization, &chain)
                .await?
            {
                ProcessOutcome::NeedConfirmation(pending) => {
                    let id = format!("{:#x}", pending.request_id);
                    let review = format!("{:?}", pending.prepared.review());
                    self.prepared.insert(id.clone(), pending);
                    return Ok(
                        json!({"outcome":"NeedConfirmation","prepared_id":id,"review":review}),
                    );
                }
                ProcessOutcome::Idle => return Ok(json!({"outcome":"Idle"})),
                _ => anyhow::bail!("watchtower SDK must require confirmation"),
            }
        }

        let authorization = self
            .watchtower_authorization(channel, &session, &status, &chain)
            .await?;
        Ok(json!({"authorization":authorization,"status":status}))
    }

    pub(crate) async fn watchtower_authorization(
        &self,
        channel: Hash256,
        session: &fiber_lsp_sdk::HostedSession<FileSignerStore>,
        status: &fiber_json_types::WatchtowerSigningStatus,
        chain: &LocalChain,
    ) -> Result<OnchainSpendAuthorization> {
        // This mirrors the exported Agent's bounded plain-CKB fixture policy:
        // an explicitly approved local destination and fee cap, exact live inputs,
        // locally verified recovery history, and independently loaded dependencies.
        // No destination or fee cap is learned from a server signing request.
        let approvals: Value = serde_json::from_slice(&std::fs::read(
            self.args.store.join("watchtower-approvals.json"),
        )?)?;
        let approval = &approvals[format!("{channel:#x}")];
        let destination: ckb_jsonrpc_types::Script =
            serde_json::from_value(approval["destination"].clone())?;
        let max_fee = approval["max_fee"]
            .as_u64()
            .context("missing persisted watchtower fee cap")?;
        let fiber_json_types::WatchtowerSigningStatus::SignatureRequired { content, .. } = &status
        else {
            anyhow::bail!("no actual pending watchtower request");
        };
        let transaction: ckb_types::packed::Transaction = content.transaction.clone().into();
        let first = transaction
            .raw()
            .inputs()
            .get(0)
            .context("missing commitment input")?
            .previous_output();
        let signer = session.channel_signer(channel).await?;
        let mut source = None;
        for record in signer.recovery_records().await? {
            if chain
                .verify_commitment_lineage(record.reference.tx_hash, &first)
                .await
                .is_ok()
            {
                source = Some(record.reference);
                break;
            }
        }
        let source = source
            .ok_or_else(|| anyhow!("spend has no independently verified commitment ancestor"))?;
        let destination: ckb_types::packed::Script = destination.into();
        let mut total = 0u64;
        let mut additional_inputs = Vec::new();
        for (index, input) in transaction.raw().inputs().into_iter().enumerate() {
            let cell = chain.live_cell(&input.previous_output()).await?;
            if index > 0 {
                anyhow::ensure!(
                    cell.output.lock() == destination
                        && cell.output.type_().to_opt().is_none()
                        && cell.data.is_empty(),
                    "unapproved sponsor input"
                );
                additional_inputs.push(input.previous_output());
            }
            let capacity: u64 = cell.output.capacity().unpack();
            total = total.checked_add(capacity).context("capacity overflow")?;
        }
        for output in transaction.raw().outputs() {
            let capacity: u64 = output.capacity().unpack();
            total = total.checked_sub(capacity).context("negative fee")?;
        }
        anyhow::ensure!(total <= max_fee, "watchtower fee exceeds local cap");
        Ok(OnchainSpendAuthorization {
            source,
            destination,
            fee: total,
            additional_inputs,
            cell_deps: chain.settlement_deps.clone(),
        })
    }
}
