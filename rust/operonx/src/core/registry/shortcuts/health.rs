//! `HealthCheckResult` — result aggregator for [`ResourceHub::health_check`].
//!
//! Mirrors Python [`operon/core/registry/shortcuts/health.py`](../../../../../../operon/core/registry/shortcuts/health.py).
//!
//! [`ResourceHub::health_check`]: crate::core::registry::resource_hub::ResourceHub::health_check

use std::collections::HashMap;

/// Per-resource pass/fail summary returned by `ResourceHub::health_check`.
#[derive(Debug, Clone)]
pub struct HealthCheckResult {
    /// Key → healthy (`true`) / unhealthy (`false`).
    pub results: HashMap<String, bool>,
    /// Key → error message (only populated for failures).
    pub errors: HashMap<String, String>,
}

impl HealthCheckResult {
    /// `true` when every resource passed.
    pub fn healthy(&self) -> bool {
        !self.results.is_empty() && self.results.values().all(|v| *v)
    }

    /// Keys of resources that passed.
    pub fn passed(&self) -> Vec<&str> {
        self.results
            .iter()
            .filter_map(|(k, v)| if *v { Some(k.as_str()) } else { None })
            .collect()
    }

    /// Keys of resources that failed.
    pub fn failed(&self) -> Vec<&str> {
        self.results
            .iter()
            .filter_map(|(k, v)| if !*v { Some(k.as_str()) } else { None })
            .collect()
    }
}

impl std::fmt::Display for HealthCheckResult {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let total = self.results.len();
        let passed = self.passed().len();
        let status = if self.healthy() { "HEALTHY" } else { "UNHEALTHY" };
        write!(f, "<HealthCheckResult {} {}/{}>", status, passed, total)
    }
}
