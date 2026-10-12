use std::time::Instant;

fn main() {
    // Public synthetic input only. This mirrors the product's KDF parameters.
    let params = scrypt::Params::recommended();
    let mut output = [0_u8; 32];
    let start = Instant::now();
    scrypt::scrypt(
        b"lsp-runtime-synthetic-probe",
        b"public-nonsecret-salt",
        &params,
        &mut output,
    )
    .expect("synthetic KDF probe");
    println!(
        "log_n={} r={} p={} elapsed_ms={} probe_debug_assertions={}",
        params.log_n(), params.r(), params.p(), start.elapsed().as_millis(),
        cfg!(debug_assertions),
    );
    print!("synthetic_derived_key=");
    for byte in output {
        print!("{byte:02x}");
    }
    println!();
}
