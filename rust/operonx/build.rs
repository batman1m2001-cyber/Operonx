//! Build script — compiles vendored protos when the `triton` feature is on.
//!
//! When `triton` is enabled, `tonic-build` reads `proto/triton/grpc_service.proto`
//! and emits the generated Rust client + message types into `OUT_DIR`. The
//! `triton.rs` provider includes it via `include!(concat!(env!("OUT_DIR"), ...))`.
//!
//! When `triton` is disabled, the script is a no-op so tier-1 lean builds
//! (no protoc required) keep working.

fn main() {
    println!("cargo:rerun-if-changed=proto/triton/grpc_service.proto");
    println!("cargo:rerun-if-changed=build.rs");

    if std::env::var("CARGO_FEATURE_TRITON").is_ok() {
        tonic_build::configure()
            .build_client(true)
            .build_server(true) // emit the server trait too, for unit-test mocks
            .compile(&["proto/triton/grpc_service.proto"], &["proto/triton"])
            .expect("compile triton .proto");
    }
}
