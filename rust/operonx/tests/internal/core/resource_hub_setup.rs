//! Integration tests for the parallel-API surface added during the
//! `bootstrap()` / `ResourceHub::auto()` refactor.
//!
//! Mirrors Python `tests/internal/core/registry/test_resource_hub.py`'s
//! TestResourceHubAuto / TestUnsetEnvVarWarning / TestDisambiguatedErrors /
//! TestBootstrap sections.

use std::env;
use std::sync::{Mutex, MutexGuard, OnceLock};

use operonx::{bootstrap, BootstrapOpts, OperonError, ResourceHub};

/// Serialize tests in this file — they all mutate the global ResourceHub
/// singleton, the process CWD, and process env vars. Cargo's default
/// thread-per-test parallelism would let them race.
fn serial_lock() -> MutexGuard<'static, ()> {
    static G: OnceLock<Mutex<()>> = OnceLock::new();
    let m = G.get_or_init(|| Mutex::new(()));
    m.lock().unwrap_or_else(|p| p.into_inner())
}

fn reset() {
    ResourceHub::reset_instance();
}

// ── auto() — discovery + idempotent install ────────────────────────────

#[test]
fn auto_finds_yaml_in_cwd() {
    let _g = serial_lock();
    reset();
    let tmp = tempfile::tempdir().expect("tmpdir");
    std::fs::write(
        tmp.path().join("resources.yaml"),
        "service:default:\n  host: localhost\n  port: 8080\n",
    )
    .unwrap();

    let prev = env::current_dir().ok();
    env::set_current_dir(tmp.path()).unwrap();
    let hub = ResourceHub::auto();
    if let Some(p) = prev {
        let _ = env::set_current_dir(p);
    }

    let hub = hub.expect("auto() should install hub when resources.yaml exists");
    assert!(hub.source_path().is_some(), "source_path must be set");
    let installed = ResourceHub::instance().expect("singleton installed");
    assert!(std::sync::Arc::ptr_eq(&hub, &installed));
    reset();
}

#[test]
fn auto_returns_none_when_missing() {
    let _g = serial_lock();
    reset();
    let tmp = tempfile::tempdir().expect("tmpdir");
    let prev = env::current_dir().ok();
    env::set_current_dir(tmp.path()).unwrap();

    let hub = ResourceHub::auto();
    if let Some(p) = prev {
        let _ = env::set_current_dir(p);
    }

    assert!(hub.is_none(), "auto() must return None when no file");
    assert!(
        ResourceHub::instance().is_err(),
        "no singleton should be installed"
    );
    reset();
}

#[test]
fn auto_idempotent_when_hub_already_set() {
    let _g = serial_lock();
    reset();
    let pre_hub = std::sync::Arc::new(ResourceHub::empty());
    ResourceHub::set_instance(pre_hub.clone());

    let tmp = tempfile::tempdir().expect("tmpdir");
    let prev = env::current_dir().ok();
    env::set_current_dir(tmp.path()).unwrap();
    let result = ResourceHub::auto();
    if let Some(p) = prev {
        let _ = env::set_current_dir(p);
    }

    let result = result.expect("idempotent auto() returns the existing hub");
    assert!(std::sync::Arc::ptr_eq(&result, &pre_hub));
    reset();
}

// ── bootstrap() ────────────────────────────────────────────────────────

#[test]
fn bootstrap_with_explicit_path() {
    let _g = serial_lock();
    reset();
    let tmp = tempfile::tempdir().expect("tmpdir");
    let cfg = tmp.path().join("config.yaml");
    std::fs::write(&cfg, "service:a:\n  host: a\n").unwrap();

    let hub = bootstrap(BootstrapOpts::new().resources(&cfg).no_env())
        .expect("explicit path returns hub");
    assert!(hub.source_path().is_some());
    let installed = ResourceHub::instance().expect("singleton installed");
    assert!(std::sync::Arc::ptr_eq(&hub, &installed));
    reset();
}

#[test]
fn bootstrap_idempotent() {
    let _g = serial_lock();
    reset();
    let tmp = tempfile::tempdir().expect("tmpdir");
    let cfg = tmp.path().join("first.yaml");
    std::fs::write(&cfg, "service:a:\n  host: a\n").unwrap();
    let pre = std::sync::Arc::new(ResourceHub::from_yaml(&cfg).unwrap());
    ResourceHub::set_instance(pre.clone());

    let result = bootstrap(BootstrapOpts::new().no_env())
        .expect("bootstrap returns existing hub");
    assert!(std::sync::Arc::ptr_eq(&result, &pre));
    reset();
}

// ── EnvVarUnset error variant ──────────────────────────────────────────

#[test]
fn get_raises_envvarunset_on_missing_var() {
    let _g = serial_lock();
    reset();
    env::remove_var("OPERONX_BOOTSTRAP_TEST_MISSING");

    let tmp = tempfile::tempdir().expect("tmpdir");
    let cfg = tmp.path().join("resources.yaml");
    std::fs::write(
        &cfg,
        "service:a:\n  host: ${OPERONX_BOOTSTRAP_TEST_MISSING}\n",
    )
    .unwrap();

    let hub = ResourceHub::from_yaml(&cfg).unwrap();
    let err = hub.get("service:a").unwrap_err();
    match err {
        OperonError::EnvVarUnset { var, key, .. } => {
            assert_eq!(var, "OPERONX_BOOTSTRAP_TEST_MISSING");
            // `key` is the resource key whose interpolation failed —
            // recovered from the dotted path "service:a.host".
            assert_eq!(key, "service:a");
        }
        other => panic!("expected EnvVarUnset, got {:?}", other),
    }
    reset();
}

// ── Disambiguated 'not found' message ──────────────────────────────────

#[test]
fn not_found_message_includes_source_and_available() {
    let _g = serial_lock();
    reset();
    let tmp = tempfile::tempdir().expect("tmpdir");
    let cfg = tmp.path().join("resources.yaml");
    std::fs::write(
        &cfg,
        "service:alpha:\n  host: a\nservice:beta:\n  host: b\n",
    )
    .unwrap();

    let hub = ResourceHub::from_yaml(&cfg).unwrap();
    let err = hub.get("service:gamma").unwrap_err();
    let msg = err.to_string();
    assert!(
        msg.contains("service:alpha") && msg.contains("service:beta"),
        "available keys must be listed; got: {msg}"
    );
    assert!(
        msg.contains(&cfg.canonicalize().unwrap().display().to_string())
            || msg.contains(&cfg.display().to_string()),
        "source path must be in the message; got: {msg}"
    );
    reset();
}

// ── Engine no longer clobbers a pre-installed hub ──────────────────────

#[test]
fn operon_builder_does_not_clobber_preinstalled_hub() {
    let _g = serial_lock();
    reset();
    let pre_hub = std::sync::Arc::new(ResourceHub::empty());
    ResourceHub::set_instance(pre_hub.clone());

    // Trivial single-op graph — used purely to construct the engine.
    let graph_json = r#"{
        "schema_version": "1.0",
        "name": "noclobber",
        "type": "graph",
        "ops": [],
        "edges": [],
        "inputs": {},
        "outputs": {}
    }"#;

    // The engine builder must NOT replace the pre-installed hub.
    let _ = operonx::OperonBuilder::new(graph_json).build();

    let after = ResourceHub::instance().expect("singleton still installed");
    assert!(
        std::sync::Arc::ptr_eq(&after, &pre_hub),
        "OperonBuilder::build() must not clobber a pre-installed hub"
    );
    reset();
}
