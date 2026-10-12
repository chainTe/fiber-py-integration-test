//! Instrumentation around the production HTTP transport. No fabricated server replies.
use std::sync::{Arc, Mutex};

use anyhow::{bail, Result};
use async_trait::async_trait;
use fiber_json_types as rpc;
use fiber_lsp_sdk_agent::{FiberRpc, HttpFiberRpc};
use serde::{de::DeserializeOwned, Serialize};
use serde_json::{json, Value};

#[derive(Default)]
struct Observations {
    capture: bool,
    calls: Vec<String>,
    captured: Vec<Value>,
    opening_patch: Vec<Value>,
    opening_responses: Vec<Value>,
    submission_results: Vec<Value>,
    watchtower_statuses: Vec<Value>,
}

#[derive(Clone)]
pub struct Transport {
    inner: HttpFiberRpc,
    url: String,
    client: reqwest::Client,
    observations: Arc<Mutex<Observations>>,
}

impl Transport {
    pub fn new(url: String, operator: String) -> Result<Self> {
        Ok(Self {
            inner: HttpFiberRpc::new(&url, operator)?,
            url,
            client: reqwest::Client::builder()
                .no_proxy()
                .timeout(std::time::Duration::from_secs(15))
                .build()?,
            observations: Arc::default(),
        })
    }

    pub fn reset(&self, capture: bool) {
        *self.observations.lock().expect("observation mutex") = Observations {
            capture,
            ..Default::default()
        };
    }

    pub fn observations(&self) -> Value {
        let data = self.observations.lock().expect("observation mutex");
        json!({"rpc_calls": data.calls, "captured": data.captured, "opening_responses":data.opening_responses, "submission_results":data.submission_results,"watchtower_statuses":data.watchtower_statuses})
    }

    pub fn set_opening_patch(&self, patch: Vec<Value>) {
        self.observations
            .lock()
            .expect("observation mutex")
            .opening_patch = patch;
    }

    fn submitted(&self, method: &str, result: Value) {
        self.observations
            .lock()
            .expect("observation mutex")
            .submission_results
            .push(json!({"method":method,"result":result}));
    }

    fn called(&self, name: &str) {
        self.observations
            .lock()
            .expect("observation mutex")
            .calls
            .push(name.into());
    }

    fn capture<P: Serialize>(&self, method: &str, params: &P) -> Result<()> {
        let mut data = self.observations.lock().expect("observation mutex");
        if data.capture {
            data.captured
                .push(json!({"method": method, "params": params}));
            bail!("submission captured locally; nothing was sent to the LSP");
        }
        Ok(())
    }

    pub async fn call<P: Serialize + Sync, R: DeserializeOwned>(
        &self,
        method: &str,
        params: &P,
        token: &str,
    ) -> Result<R> {
        self.called(method);
        let response = self
            .client
            .post(&self.url)
            .bearer_auth(token)
            .json(&json!({"jsonrpc":"2.0", "id":1, "method":method, "params":[params]}))
            .send()
            .await?;
        let status = response.status();
        let value: Value = response.json().await?;
        anyhow::ensure!(status.is_success(), "HTTP {status}: {value}");
        if let Some(error) = value.get("error") {
            bail!("RPC {method}: {error}");
        }
        let result = value.get("result").cloned().unwrap_or(Value::Null);
        if matches!(
            method,
            "submit_channel_signature" | "submit_watchtower_signature"
        ) {
            self.submitted(method, result.clone());
        }
        Ok(serde_json::from_value(result)?)
    }
}

#[async_trait]
impl FiberRpc for Transport {
    async fn settle_invoice(&self, token: &str, params: rpc::SettleInvoiceParams) -> Result<()> {
        self.called("settle_invoice");
        self.inner.settle_invoice(token, params).await
    }
    async fn create_preimage(&self, token: &str, params: rpc::CreatePreimageParams) -> Result<()> {
        self.called("create_preimage");
        self.inner.create_preimage(token, params).await
    }
    async fn open_tenant_channel(
        &self,
        token: &str,
        params: rpc::OpenChannelWithExternalFundingParams,
    ) -> Result<rpc::OpenTenantChannelResult> {
        self.called("open_tenant_channel");
        let actual = self.inner.open_tenant_channel(token, params).await?;
        let original = serde_json::to_value(actual)?;
        let mut changed = original.clone();
        let mut data = self.observations.lock().expect("observation mutex");
        for patch in &data.opening_patch {
            let pointer = patch["pointer"]
                .as_str()
                .ok_or_else(|| anyhow::anyhow!("patch pointer required"))?;
            let slot = changed
                .pointer_mut(pointer)
                .ok_or_else(|| anyhow::anyhow!("opening patch target {pointer} does not exist"))?;
            *slot = patch
                .get("value")
                .ok_or_else(|| anyhow::anyhow!("patch value required"))?
                .clone();
        }
        data.opening_responses
            .push(json!({"original":original,"sdk_input":changed}));
        Ok(serde_json::from_value(changed)?)
    }
    async fn get_tenant_registry_nonce(
        &self,
        key: rpc::Pubkey,
    ) -> Result<rpc::GetLspTenantRegistryNonceResult> {
        self.called("lsp_get_tenant_registry_nonce");
        self.inner.get_tenant_registry_nonce(key).await
    }
    async fn register_tenant(
        &self,
        params: rpc::RegisterLspTenantParams,
    ) -> Result<rpc::RegisterLspTenantResult> {
        self.called("lsp_register_tenant");
        self.inner.register_tenant(params).await
    }
    async fn get_channel_signing_status(
        &self,
        token: &str,
        channel: rpc::Hash256,
    ) -> Result<rpc::GetChannelSigningStatusResult> {
        self.called("get_channel_signing_status");
        self.inner.get_channel_signing_status(token, channel).await
    }
    async fn submit_channel_signature(
        &self,
        token: &str,
        params: rpc::SubmitChannelSignatureParams,
    ) -> Result<rpc::SubmitChannelSignatureResult> {
        self.capture("submit_channel_signature", &params)?;
        self.called("submit_channel_signature");
        let result = self.inner.submit_channel_signature(token, params).await?;
        self.submitted("submit_channel_signature", serde_json::to_value(result)?);
        Ok(result)
    }
    async fn get_watchtower_signing_status(
        &self,
        token: &str,
        channel: rpc::Hash256,
    ) -> Result<rpc::GetWatchtowerSigningStatusResult> {
        self.called("get_watchtower_signing_status");
        let result = self
            .inner
            .get_watchtower_signing_status(token, channel)
            .await?;
        self.observations
            .lock()
            .expect("observation mutex")
            .watchtower_statuses
            .push(json!({"channel_id":channel,"status":result.status}));
        Ok(result)
    }
    async fn submit_watchtower_signature(
        &self,
        token: &str,
        params: rpc::SubmitWatchtowerSignatureParams,
    ) -> Result<rpc::SubmitWatchtowerSignatureResult> {
        self.capture("submit_watchtower_signature", &params)?;
        self.called("submit_watchtower_signature");
        let result = self
            .inner
            .submit_watchtower_signature(token, params)
            .await?;
        self.submitted("submit_watchtower_signature", serde_json::to_value(result)?);
        Ok(result)
    }
}
