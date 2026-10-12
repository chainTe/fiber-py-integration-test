use biscuit_auth::{
    builder::{BlockBuilder, Fact, Term},
    error, Authorizer, AuthorizerBuilder, AuthorizerLimits, Biscuit, KeyPair,
};
use std::time::{Duration, Instant};

// Mirrors the unchanged 10 ms builder, time fact and LSP-read rule in the
// pinned product's crates/fiber-lib/src/rpc/biscuit.rs. No nodes or real keys.
fn builder() -> AuthorizerBuilder {
    AuthorizerBuilder::new().set_limits(AuthorizerLimits {
        max_time: Duration::from_millis(10),
        ..Default::default()
    })
}

fn lsp_read(token: &Biscuit) -> Result<Authorizer, error::Token> {
    builder()
        .code(r#"allow if read("lsp");"#)?
        .fact(Fact::new("time".to_string(), &[Term::Date(1_800_000_000)]))?
        .build(token)
}

fn assert_denied(label: &str, token: &Biscuit) {
    let result = lsp_read(token).expect("build negative authorization").authorize();
    match result {
        Err(error::Token::FailedLogic(_)) => println!("PASS {label}: denied by policy"),
        Err(other) => panic!("{label}: unexpected error, never counted as denial: {other:?}"),
        Ok(_) => panic!("{label}: unauthorized token was accepted"),
    }
}

fn main() {
    assert_eq!(builder().limits().max_time, Duration::from_millis(10));
    let root = KeyPair::new();
    let token = Biscuit::builder()
        .code(r#"
            read("lsp"); write("lsp"); read("node"); write("node");
            read("channels"); write("channels"); read("payments"); write("payments");
            read("invoices"); write("invoices"); read("peers"); write("peers");
            read("watchtower"); write("watchtower"); read("graph"); write("dev");
            tenant("synthetic-tenant"); node("synthetic-node");
        "#)
        .expect("synthetic operator facts")
        .build(&root)
        .expect("synthetic operator token");
    let encoded = token.to_base64().unwrap();
    let parsed = Biscuit::from_base64(&encoded, root.public()).unwrap();
    let mut max_us = 0_u128;
    for index in 0..100 {
        let mut authorizer = lsp_read(&parsed).unwrap();
        let start = Instant::now();
        if let Err(error) = authorizer.authorize() {
            panic!("normal authorization iteration {index} failed without retry: {error:?}");
        }
        max_us = max_us.max(start.elapsed().as_micros());
    }
    println!("PASS 100 normal authorizations: exact_limit_ms=10 max_authorize_us={max_us}");

    let tenants: Vec<(String,)> = builder()
        .build(&parsed).unwrap()
        .query("data($id) <- tenant($id)").unwrap();
    assert_eq!(tenants, vec![("synthetic-tenant".to_string(),)]);
    println!("PASS authority tenant query with exact 10 ms limit");

    let unrelated = Biscuit::builder().code(r#"read("node");"#).unwrap().build(&root).unwrap();
    assert_denied("unrelated permission", &unrelated);
    let write_only = Biscuit::builder().code(r#"write("lsp");"#).unwrap().build(&root).unwrap();
    assert_denied("write permission does not grant read", &write_only);
    let attenuated = parsed.append(
        BlockBuilder::new().code(r#"check if read("forbidden");"#).unwrap()
    ).unwrap();
    assert_denied("unsatisfied attenuation check", &attenuated);
    let appended_privilege = unrelated.append(
        BlockBuilder::new().code(r#"read("lsp");"#).unwrap()
    ).unwrap();
    assert_denied("appended block cannot grant authority permission", &appended_privilege);

    let other_root = KeyPair::new();
    assert!(Biscuit::from_base64(&encoded, other_root.public()).is_err());
    println!("PASS wrong-root signature rejected");
    assert!(Biscuit::from_base64("invalid-token", root.public()).is_err());
    println!("PASS malformed token rejected");
    println!("PASS source-based auth probe; debug_assertions={}", cfg!(debug_assertions));
}
