//! Local devnet adapter. Every signature comes from the pinned production SDK.
//! stdin/stdout is a sequential JSON-lines protocol; no hidden polling occurs.
mod chain;
mod transport;
mod watchtower;

use std::{
    collections::HashMap,
    fs,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{anyhow, bail, Context, Result};
use ckb_types::prelude::*;
use clap::Parser;
use fiber_lsp_sdk::{
    ChainVerifier, ChannelKeyId, HostedSession, HostedSessionState, PendingRequest, ProcessOutcome,
    RootKey, RootSigner, SigningPolicy, SubmitParams,
};
use fiber_lsp_sdk_agent::{Agent, AgentConfig, FiberRpc, FileSignerStore, FixtureAuthorization};
use fiber_types::Hash256;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use transport::Transport;

const PRODUCT_SHA: &str = "8db6a1175cc94b6f9386953fbd3e3ebbed2ff3f8";

#[derive(Parser)]
struct Args {
    #[arg(long)]
    rpc: String,
    #[arg(long)]
    store: PathBuf,
    #[arg(long)]
    operator_token: String,
    #[arg(long)]
    ckb_rpc: String,
    #[arg(long)]
    contracts_dir: PathBuf,
}

// This is the public test-agent's persisted directory format, not product state.
// Keep a single store instance alive at a time so snapshots never overwrite each other.
#[derive(Default, Deserialize, Serialize)]
struct AgentState {
    tenant_token: Option<String>,
    bindings: HashMap<String, String>,
    pending_channel_key_id: Option<String>,
    opening_request: Option<Vec<u8>>,
}

struct Driver {
    args: Args,
    rpc: Transport,
    prepared: HashMap<String, PendingRequest>,
}

impl Driver {
    async fn agent(&self) -> Result<Agent<Transport, FileSignerStore>> {
        Agent::open(
            self.rpc.clone(),
            AgentConfig {
                store_dir: self.args.store.clone(),
                status_file: Some(self.args.store.join("status.json")),
            },
        )
        .await
    }

    async fn session(&self, policy: SigningPolicy) -> Result<HostedSession<FileSignerStore>> {
        // Agent::open owns root-key creation; never create a second wallet identity.
        let agent = self.agent().await?;
        drop(agent);
        let bytes = fs::read(self.args.store.join("root.key"))?;
        let key: [u8; 32] = bytes
            .try_into()
            .map_err(|_| anyhow!("invalid root key length"))?;
        let root = RootSigner::open(
            RootKey::import(key)?,
            FileSignerStore::open(&self.args.store)?,
        )
        .await?;
        let path = self.args.store.join("agent.json");
        let state: AgentState = if path.exists() {
            serde_json::from_slice(&fs::read(path)?)?
        } else {
            AgentState::default()
        };
        Ok(HostedSession::new(root)
            .with_policy(policy)
            .with_state(HostedSessionState {
                tenant_token: state.tenant_token,
                bindings: state
                    .bindings
                    .into_iter()
                    .map(|(channel, key)| Ok((channel.parse()?, ChannelKeyId(key.parse()?))))
                    .collect::<Result<_>>()?,
                pending: state
                    .pending_channel_key_id
                    .map(|key| key.parse().map(ChannelKeyId))
                    .transpose()?,
                opening_request: state.opening_request,
            }))
    }

    fn persist(&self, session: &HostedSession<FileSignerStore>) -> Result<()> {
        let state = session.state();
        let serialized = AgentState {
            tenant_token: state.tenant_token.clone(),
            bindings: state
                .bindings
                .iter()
                .map(|(channel, key)| (format!("{channel:#x}"), format!("{:#x}", key.0)))
                .collect(),
            pending_channel_key_id: state.pending.map(|key| format!("{:#x}", key.0)),
            opening_request: state.opening_request.clone(),
        };
        let path = self.args.store.join("agent.json");
        fs::write(
            path.with_extension("json.tmp"),
            serde_json::to_vec_pretty(&serialized)?,
        )?;
        fs::rename(path.with_extension("json.tmp"), path)?;
        Ok(())
    }

    fn network(&self, udt: bool) -> Result<fiber_lsp_sdk::OpeningNetwork> {
        let script = |name| -> Result<ckb_types::packed::Script> {
            let bytes = fs::read(self.args.contracts_dir.join(name))?;
            Ok(ckb_types::packed::Script::new_builder()
                .code_hash(fiber_types::blake2b_hash_with_salt(&bytes, &[]).pack())
                .hash_type(ckb_types::core::ScriptHashType::Data2)
                .build())
        };
        Ok(fiber_lsp_sdk::OpeningNetwork {
            funding_lock: script("funding-lock")?,
            commitment_lock: script("commitment-lock")?,
            commitment_cell_deps: if udt { 3 } else { 2 },
            epoch_duration_ms: if cfg!(debug_assertions) {
                2000
            } else {
                14_400_000
            },
            minimum_shutdown_fee: 100_000_000,
        })
    }

    fn snapshot(&self) -> Result<Value> {
        let mut result = serde_json::Map::new();
        for name in [
            "root.key",
            "snapshot.bin",
            "agent.json",
            "watchtower-approvals.json",
            "watchtower-submissions.json",
        ] {
            let path = self.args.store.join(name);
            if path.exists() {
                let bytes = fs::read(path)?;
                result.insert(name.into(), json!({"bytes": bytes.len(), "blake2b":hex::encode(fiber_types::blake2b_hash_with_salt(&bytes,&[]))}));
            }
        }
        Ok(Value::Object(result))
    }

    async fn execute(&mut self, command: Value) -> Result<Value> {
        self.rpc
            .reset(command["capture"].as_bool().unwrap_or(false));
        let operation = string(&command, "op")?;
        match operation {
            "attenuate" => {
                let token =
                    biscuit_auth::UnverifiedBiscuit::from_base64(string(&command, "token")?)?;
                let block =
                    biscuit_auth::builder::BlockBuilder::new().code(string(&command, "code")?)?;
                Ok(json!({"token":token.append(block)?.to_base64()?}))
            }
            "watchtower_authorization" | "watchtower_prepare" => {
                self.watchtower_command(&command).await
            }
            "verify_commitment_lineage" => {
                let channel: Hash256 = string(&command, "channel_id")?.parse()?;
                let source: fiber_lsp_sdk::CommitmentReference =
                    serde_json::from_value(command["source"].clone())?;
                let outpoint: ckb_jsonrpc_types::OutPoint =
                    serde_json::from_value(command["outpoint"].clone())?;
                let session = self.session(SigningPolicy::Manual).await?;
                let records = session
                    .channel_signer(channel)
                    .await?
                    .recovery_records()
                    .await?;
                anyhow::ensure!(
                    records.iter().any(|r| r.reference == source),
                    "source is absent from local SDK recovery records"
                );
                let chain =
                    chain::LocalChain::new(&self.args.ckb_rpc, &self.args.contracts_dir).await?;
                chain
                    .verify_commitment_lineage(source.tx_hash, &outpoint.into())
                    .await?;
                Ok(json!({"verified":true}))
            }
            "identity" => {
                let session = self.session(SigningPolicy::Manual).await?;
                Ok(
                    json!({"tenant_id":session.tenant_id().as_str(), "root_signer_pubkey":fiber_json_types::Pubkey::from(fiber_types::Pubkey::from(session.identity_public_key())), "product_sha":PRODUCT_SHA}),
                )
            }
            "nonce" => {
                let session = self.session(SigningPolicy::Manual).await?;
                Ok(serde_json::to_value(
                    self.rpc
                        .get_tenant_registry_nonce(
                            fiber_types::Pubkey::from(session.identity_public_key()).into(),
                        )
                        .await?,
                )?)
            }
            "registration_proof" => {
                let session = self.session(SigningPolicy::Manual).await?;
                Ok(serde_json::to_value(session.begin_registration(
                    serde_json::from_value(command["nonce"].clone())?,
                )?)?)
            }
            "register" => {
                let mut session = self.session(SigningPolicy::Manual).await?;
                let params = if command.get("params").is_some() {
                    serde_json::from_value(command["params"].clone())?
                } else {
                    let nonce = self
                        .rpc
                        .get_tenant_registry_nonce(
                            fiber_types::Pubkey::from(session.identity_public_key()).into(),
                        )
                        .await?;
                    session.begin_registration(nonce)?
                };
                let result = self.rpc.register_tenant(params).await?;
                let output = serde_json::to_value(&result)?;
                session.finish_registration(result)?;
                self.persist(&session)?;
                Ok(output)
            }
            "initialize" => {
                self.agent().await?.initialize().await?;
                self.status()
            }
            "status" => self.status(),
            "snapshot" => self.snapshot(),
            "recovery_status" => {
                let channel: Hash256 = string(&command, "channel_id")?.parse()?;
                let session = self.session(SigningPolicy::Manual).await?;
                let signer = session.channel_signer(channel).await?;
                let revocations = signer.revocation_records().await?;
                let records: Vec<Value> = signer.recovery_records().await?.into_iter().map(|record| {
                    let revoked = revocations.iter().any(|r| r.context.revoked == record.reference);
                    let hashes: Vec<_> = record.settlement.data.tlcs.iter().map(|tlc|tlc.payment_hash).collect();
                    json!({"reference":record.reference,"complete_signature":record.complete_signature.is_some(),"revoked":revoked,"payment_hashes":hashes})
                }).collect();
                Ok(json!({"records":records}))
            }
            "open" => {
                if let Some(patches) = command.get("response_patch") {
                    self.rpc
                        .set_opening_patch(serde_json::from_value(patches.clone())?);
                }
                let params: fiber_json_types::OpenChannelWithExternalFundingParams =
                    serde_json::from_value(command["params"].clone())?;
                let network = self.network(params.funding_udt_type_script.is_some())?;
                let result = self
                    .agent()
                    .await?
                    .open_tenant_channel(params, &network, &self.args.ckb_rpc)
                    .await?;
                Ok(serde_json::to_value(result)?)
            }
            "authorize" => {
                let request = command
                    .get("authorization")
                    .context("missing authorization")?;
                if request["type"] == "Outbound" {
                    anyhow::ensure!(
                        request.get("preimage").is_none(),
                        "outbound authorization must not receive a preimage"
                    );
                    let channel: Hash256 = string(request, "channel_id")?.parse()?;
                    let terms: fiber_lsp_sdk::PaymentAuthorization =
                        serde_json::from_value(request["terms"].clone())?;
                    anyhow::ensure!(!terms.inbound, "outbound terms must have inbound:false");
                    let session = self.session(SigningPolicy::Manual).await?;
                    session
                        .channel_signer(channel)
                        .await?
                        .authorize_payment(terms)
                        .await?;
                } else if request["type"] == "Cancel" {
                    let channel: Hash256 = string(request, "channel_id")?.parse()?;
                    let hash = string(request, "payment_hash")?.parse()?;
                    self.session(SigningPolicy::Manual)
                        .await?
                        .channel_signer(channel)
                        .await?
                        .authorize_cancellation(hash)
                        .await?;
                } else {
                    let request: FixtureAuthorization = serde_json::from_value(request.clone())?;
                    let mut agent = self.agent().await?;
                    if matches!(request, FixtureAuthorization::ReleasePreimage { .. }) {
                        agent
                            .configure_chain(&self.args.ckb_rpc, &self.args.contracts_dir)
                            .await?;
                    }
                    agent.authorize(request).await?;
                }
                Ok(json!({"authorized":true}))
            }
            "outbound_fulfillment" => {
                anyhow::ensure!(
                    command.get("preimage").is_none(),
                    "payer must obtain preimage from its own get_payment"
                );
                let channel: Hash256 = string(&command, "channel_id")?.parse()?;
                let payment_hash: Hash256 = string(&command, "payment_hash")?.parse()?;
                let session = self.session(SigningPolicy::Manual).await?;
                let token = session.tenant_token().context("not registered")?;
                let result: Value = self
                    .rpc
                    .call(
                        "get_payment",
                        &json!({"payment_hash":format!("{payment_hash:#x}")}),
                        token,
                    )
                    .await?;
                match result.get("payment_preimage").and_then(Value::as_str) {
                    Some(preimage) => {
                        session
                            .channel_signer(channel)
                            .await?
                            .authorize_fulfillment(payment_hash, preimage.parse()?)
                            .await?;
                        Ok(
                            json!({"authorized":true,"source":"payer.get_payment", "payment":result}),
                        )
                    }
                    None => Ok(
                        json!({"authorized":false,"source":"payer.get_payment", "payment":result}),
                    ),
                }
            }
            "poll" => {
                let outcome = self.poll_once().await;
                let mut result = self.rpc.observations();
                result["ok"] = json!(outcome.is_ok());
                if let Err(error) = outcome {
                    result["error"] = json!(format!("{error:#}"));
                }
                Ok(result)
            }
            "signing_status" => {
                let channel: Hash256 = string(&command, "channel_id")?.parse()?;
                let session = self.session(SigningPolicy::Manual).await?;
                let token = session.tenant_token().context("not registered")?;
                if command["watchtower"].as_bool().unwrap_or(false) {
                    Ok(serde_json::to_value(
                        self.rpc
                            .get_watchtower_signing_status(token, channel.into())
                            .await?,
                    )?)
                } else {
                    Ok(serde_json::to_value(
                        self.rpc
                            .get_channel_signing_status(token, channel.into())
                            .await?,
                    )?)
                }
            }
            "prepare" => {
                let channel: Hash256 = string(&command, "channel_id")?.parse()?;
                let policy = match command["policy"].as_str().unwrap_or("Manual") {
                    "Manual" => SigningPolicy::Manual,
                    "Auto" => SigningPolicy::Auto,
                    _ => bail!("policy must be Manual or Auto"),
                };
                let mut session = self.session(policy).await?;
                let status = if let Some(status) = command.get("status") {
                    serde_json::from_value(status.clone())?
                } else {
                    self.rpc
                        .get_channel_signing_status(
                            session.tenant_token().context("not registered")?,
                            channel.into(),
                        )
                        .await?
                        .status
                };
                match session
                    .handle_channel_status(channel, status, now_ms())
                    .await?
                {
                    ProcessOutcome::Idle => Ok(json!({"outcome":"Idle"})),
                    ProcessOutcome::Denied => Ok(json!({"outcome":"Denied"})),
                    ProcessOutcome::ReadyToSubmit(params) => {
                        Ok(json!({"outcome":"ReadyToSubmit", "submission":submission(params)?}))
                    }
                    ProcessOutcome::NeedConfirmation(pending) => {
                        let id = format!("{:#x}", pending.request_id);
                        let review = format!("{:?}", pending.prepared.review());
                        self.prepared.insert(id.clone(), pending);
                        Ok(json!({"outcome":"NeedConfirmation","prepared_id":id,"review":review}))
                    }
                }
            }
            "confirm" => {
                let id = string(&command, "prepared_id")?;
                let pending = self
                    .prepared
                    .get(id)
                    .context("prepared request missing; prepare again after restart")?
                    .clone();
                let mut session = self.session(SigningPolicy::Manual).await?;
                let params = if pending.prepared.onchain_authorization().is_some() {
                    let chain =
                        chain::LocalChain::new(&self.args.ckb_rpc, &self.args.contracts_dir)
                            .await?;
                    session.confirm_onchain(pending, &chain).await?
                } else {
                    session.confirm(pending, now_ms()).await?
                };
                self.prepared.remove(id);
                Ok(json!({"submission":submission(params)?}))
            }
            "submit" => {
                let payload = command.get("submission").context("missing submission")?;
                let method = string(payload, "method")?;
                anyhow::ensure!(
                    matches!(
                        method,
                        "submit_channel_signature" | "submit_watchtower_signature"
                    ),
                    "only signature submission is allowed"
                );
                let session = self.session(SigningPolicy::Manual).await?;
                let response: Value = self
                    .rpc
                    .call(
                        method,
                        &payload["params"],
                        session.tenant_token().context("not registered")?,
                    )
                    .await?;
                Ok(response)
            }
            "shutdown" => Ok(json!({"shutdown":true})),
            _ => bail!("unknown operation {operation}"),
        }
    }

    async fn poll_once(&self) -> Result<()> {
        let chain = chain::LocalChain::new(&self.args.ckb_rpc, &self.args.contracts_dir).await?;
        let session = self.session(SigningPolicy::Manual).await?;
        let token = session
            .tenant_token()
            .context("agent is not registered")?
            .to_owned();
        let channels: Vec<_> = session.state().bindings.keys().copied().collect();
        drop(session);
        for channel in channels {
            // Same production channel policy as Agent::poll_once. Reopen sequentially:
            // a second FileSignerStore must never overwrite a newer snapshot.
            let mut session = self.session(SigningPolicy::Manual).await?;
            let status = self
                .rpc
                .get_channel_signing_status(&token, channel.into())
                .await?
                .status;
            let submission = match session
                .handle_channel_status(channel, status, now_ms())
                .await?
            {
                ProcessOutcome::Idle => None,
                ProcessOutcome::NeedConfirmation(pending) => {
                    Some(session.confirm(pending, now_ms()).await?)
                }
                ProcessOutcome::ReadyToSubmit(params) => Some(params),
                ProcessOutcome::Denied => bail!("signing policy denied a signing request"),
            };
            if let Some(params) = submission {
                let SubmitParams::Channel(params) = params else {
                    bail!("expected channel signature")
                };
                self.rpc.submit_channel_signature(&token, params).await?;
            }
            let status = match self
                .rpc
                .get_watchtower_signing_status(&token, channel.into())
                .await
            {
                Ok(result) => result.status,
                Err(error) if error.to_string().contains("watched channel not found") => continue,
                Err(error) => return Err(error),
            };
            if matches!(
                status,
                fiber_json_types::WatchtowerSigningStatus::NoSignatureRequired
            ) {
                continue;
            }
            let authorization = self
                .watchtower_authorization(channel, &session, &status, &chain)
                .await?;
            drop(session);
            // The exported Agent re-fetches real status, uses production verified
            // prepare+confirm APIs, submits, and records only accepted receipts.
            self.agent()
                .await?
                .poll_watchtower_verified(channel, authorization, &chain)
                .await?;
        }
        // Reopening loads accepted Agent receipts; initialize refreshes its standard
        // status projection. Already-registered, bound sessions do not register again.
        self.agent().await?.initialize().await
    }

    fn status(&self) -> Result<Value> {
        let mut status: Value = serde_json::from_slice(
            &fs::read(self.args.store.join("status.json")).context("initialize before status")?,
        )?;
        status["product_sha"] = json!(PRODUCT_SHA);
        Ok(status)
    }
}

fn submission(params: SubmitParams) -> Result<Value> {
    Ok(match params {
        SubmitParams::Channel(params) => {
            json!({"method":"submit_channel_signature","params":params})
        }
        SubmitParams::Watchtower(params) => {
            json!({"method":"submit_watchtower_signature","params":params})
        }
    })
}

fn string<'a>(value: &'a Value, field: &str) -> Result<&'a str> {
    value
        .get(field)
        .and_then(Value::as_str)
        .with_context(|| format!("missing string field {field}"))
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_millis() as u64
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    let rpc = Transport::new(args.rpc.clone(), args.operator_token.clone())?;
    let mut driver = Driver {
        args,
        rpc,
        prepared: HashMap::new(),
    };
    let mut lines = BufReader::new(tokio::io::stdin()).lines();
    let mut output = tokio::io::stdout();
    while let Some(line) = lines.next_line().await? {
        driver.rpc.reset(false);
        let request: Result<Value, _> = serde_json::from_str(&line);
        let (id, result, shutdown) = match request {
            Ok(command) => {
                let shutdown = command["op"] == "shutdown";
                (
                    command.get("id").cloned().unwrap_or(Value::Null),
                    driver.execute(command).await,
                    shutdown,
                )
            }
            Err(error) => (Value::Null, Err(error.into()), false),
        };
        let reply = match result {
            Ok(result) => json!({"id":id,"result":result,"observations":driver.rpc.observations()}),
            Err(error) => {
                json!({"id":id,"error":format!("{error:#}"),"observations":driver.rpc.observations()})
            }
        };
        output
            .write_all(serde_json::to_string(&reply)?.as_bytes())
            .await?;
        output.write_all(b"\n").await?;
        output.flush().await?;
        if shutdown {
            break;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn driver(label: &str) -> Driver {
        let path = std::env::temp_dir().join(format!(
            "fiber-sdk-driver-{label}-{}-{}",
            std::process::id(),
            now_ms()
        ));
        let args = Args {
            rpc: "http://127.0.0.1:1".into(),
            store: path,
            operator_token: "devnet-test-only".into(),
            ckb_rpc: "http://127.0.0.1:1".into(),
            contracts_dir: PathBuf::from("/nonexistent"),
        };
        Driver {
            rpc: Transport::new(args.rpc.clone(), args.operator_token.clone()).unwrap(),
            args,
            prepared: HashMap::new(),
        }
    }

    #[tokio::test]
    async fn root_identity_and_complete_session_survive_reopen() {
        let mut driver = driver("restart");
        let first = driver.execute(json!({"op":"identity"})).await.unwrap();
        let mut session = driver.session(SigningPolicy::Manual).await.unwrap();
        let material = session.allocate_pending_channel().await.unwrap();
        driver.persist(&session).unwrap();
        let before = driver.snapshot().unwrap();
        drop(session);
        let mut reopened = driver.session(SigningPolicy::Manual).await.unwrap();
        let restored = reopened.allocate_pending_channel().await.unwrap();
        assert_eq!(
            serde_json::to_value(fiber_lsp_sdk::json::open_material_to_rpc(&material)).unwrap(),
            serde_json::to_value(fiber_lsp_sdk::json::open_material_to_rpc(&restored)).unwrap()
        );
        drop(reopened);
        let second = driver.execute(json!({"op":"identity"})).await.unwrap();
        assert_eq!(first, second);
        assert_eq!(before, driver.snapshot().unwrap());
        fs::remove_dir_all(&driver.args.store).unwrap();
    }

    #[tokio::test]
    async fn registration_proof_does_not_contact_server_or_change_snapshot() {
        let mut driver = driver("proof");
        let identity = driver.execute(json!({"op":"identity"})).await.unwrap();
        let key = identity["root_signer_pubkey"].clone();
        let nonce = json!({"lsp_node_id":key, "root_signer_pubkey":key, "nonce":format!("0x{}","11".repeat(32))});
        let before = driver.snapshot().unwrap();
        let proof = driver
            .execute(json!({"op":"registration_proof","nonce":nonce}))
            .await
            .unwrap();
        assert_eq!(proof["nonce"], nonce["nonce"]);
        assert_eq!(proof["root_signer_pubkey"], key);
        assert_eq!(proof["signature"].as_str().unwrap().len(), 128);
        assert_eq!(driver.rpc.observations()["rpc_calls"], json!([]));
        assert_eq!(before, driver.snapshot().unwrap());
        fs::remove_dir_all(&driver.args.store).unwrap();
    }

    #[tokio::test]
    async fn attenuation_preserves_authority_and_verifies_without_issuer_key() {
        let mut driver = driver("attenuate");
        let root = biscuit_auth::KeyPair::new();
        let original = biscuit_auth::Biscuit::builder()
            .code("tenant(\"owner\"); read(\"channels\");")
            .unwrap()
            .build(&root)
            .unwrap();
        let result = driver.execute(json!({"op":"attenuate","token":original.to_base64().unwrap(),"code":"tenant(\"other\"); node(\"local\");"})).await.unwrap();
        let attenuated =
            biscuit_auth::Biscuit::from_base64(result["token"].as_str().unwrap(), root.public())
                .unwrap();
        assert_eq!(attenuated.block_count(), original.block_count() + 1);
        assert_eq!(
            attenuated.print_block_source(0).unwrap(),
            original.print_block_source(0).unwrap()
        );
        assert!(attenuated.print_block_source(1).unwrap().contains("other"));
        assert_eq!(driver.rpc.observations()["rpc_calls"], json!([]));
        assert!(!driver.args.store.exists());
    }

    #[tokio::test]
    async fn payer_rejects_caller_preimage_before_rpc() {
        let mut driver = driver("preimage");
        let error = driver
            .execute(json!({"op":"outbound_fulfillment","preimage":"secret"}))
            .await
            .unwrap_err();
        assert!(error.to_string().contains("own get_payment"));
        assert_eq!(driver.rpc.observations()["rpc_calls"], json!([]));
        assert!(!driver.args.store.exists());
    }
}
