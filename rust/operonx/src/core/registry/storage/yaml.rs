//! `YamlConfigStorage` — all configs in a single YAML file with env-var interpolation.
//!
//! Mirrors Python [`operonx/core/registry/storage/yaml.py`](../../../../../../operonx/core/registry/storage/yaml.py).
//!
//! Supports both nested and flat key formats (both resolve to `"category:name"`):
//!
//! ```yaml
//! # Nested (preferred)
//! llm:
//!   gpt-4o:
//!     api_type: openai
//!     api_key: ${OPENAI_API_KEY}
//!
//! # Flat (legacy)
//! llm:gpt-4o:
//!   api_type: openai
//! ```
//!
//! # Env-var interpolation
//! - `${VAR}`         — required; a missing var is collected and the full
//!   missing-list is raised once via [`OperonError::Config`] — mirrors Python's
//!   consolidated `RuntimeError`.
//! - `${VAR:default}` — optional, defaults to `default` when unset.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use parking_lot::Mutex;
use serde_json::Value;
use tracing::{debug, error, warn};

use super::base::{ConfigDict, ConfigStorage};
use crate::core::exceptions::OperonError;
use crate::core::registry::bootstrap_state;

/// YAML-file–backed config store with `${VAR}` interpolation.
pub struct YamlConfigStorage {
    file_path: PathBuf,
    write_lock: Mutex<()>,
}

impl YamlConfigStorage {
    /// Create a storage handle for `file_path`. Parent directory is created if missing.
    ///
    /// Performs a one-shot scan of the file at construction time and emits a
    /// `tracing::warn!` per `${VAR}` whose env var is currently unset, with
    /// the resource keys that reference it. This matches Python's
    /// `ResourceHubWarning` (W2) — early signal that a setup gap exists,
    /// without failing load. Lazy `${VAR}` resolution still happens at
    /// `load_one` / `load_all` time, where missing vars surface as
    /// [`OperonError::EnvVarUnset`].
    pub fn new(file_path: impl Into<PathBuf>) -> Result<Self, OperonError> {
        let file_path = file_path.into();
        if let Some(parent) = file_path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)?;
            }
        }
        let s = Self {
            file_path,
            write_lock: Mutex::new(()),
        };
        s.warn_on_unset_env_vars();
        Ok(s)
    }

    /// Scan once at construction. Best-effort — silently skips if the file
    /// is missing or malformed, since `load_one`/`load_all` will surface
    /// those errors at their own call sites.
    fn warn_on_unset_env_vars(&self) {
        let data = match self.load_file() {
            Ok(d) if !d.is_empty() => d,
            _ => return,
        };

        // Group findings by var → list of top-level keys (resources) referencing it.
        let mut var_keys: Vec<(String, Vec<String>)> = Vec::new();
        for (key, value) in &data {
            scan_unset_vars(value, |var| {
                if let Some((_, keys)) = var_keys.iter_mut().find(|(v, _)| v == var) {
                    if !keys.iter().any(|k| k == key) {
                        keys.push(key.clone());
                    }
                } else {
                    var_keys.push((var.to_string(), vec![key.clone()]));
                }
            });
        }
        if var_keys.is_empty() {
            return;
        }

        let mut lines = Vec::new();
        lines.push(format!(
            "resources.yaml at {} references unset environment variable(s); \
             resources will fail at resolution unless set before then:",
            self.file_path_str()
        ));
        for (var, keys) in &var_keys {
            lines.push(format!("  - ${{{}}} (used by: {})", var, keys.join(", ")));
        }
        warn!("{}", lines.join("\n"));
    }

    /// Load + flatten the YAML file into `"category:name" → Value::Object` map.
    ///
    /// Does **not** interpolate env vars — that happens in `load_one` / `load_all`.
    fn load_file(&self) -> Result<HashMap<String, Value>, OperonError> {
        if !self.file_path.exists() {
            return Ok(HashMap::new());
        }
        let text = fs::read_to_string(&self.file_path)?;
        if text.trim().is_empty() {
            return Ok(HashMap::new());
        }
        let raw: Value = serde_yaml::from_str(&text)?;
        let obj = match raw {
            Value::Object(m) => m,
            Value::Null => return Ok(HashMap::new()),
            _ => return Ok(HashMap::new()),
        };

        let mut flat = HashMap::new();
        for (key, value) in obj {
            match &value {
                Value::Object(inner) if !key.contains(':') => {
                    // Possibly nested: {category: {name: config}}.
                    let all_dicts = !inner.is_empty()
                        && inner.values().all(|v| matches!(v, Value::Object(_)));
                    if all_dicts {
                        for (name, config) in inner.clone() {
                            flat.insert(format!("{}:{}", key, name), config);
                        }
                    } else {
                        // Legacy flat key without colon.
                        flat.insert(key, value);
                    }
                }
                _ => {
                    flat.insert(key, value);
                }
            }
        }
        Ok(flat)
    }

    fn save_file(&self, data: &HashMap<String, Value>) -> Result<(), OperonError> {
        // serde_yaml writes keys in iteration order — HashMap order is nondeterministic,
        // but Python's yaml.dump(sort_keys=False) preserves dict order. We accept the
        // minor drift here; callers who care should use JSON storage.
        let text = serde_yaml::to_string(data)?;
        fs::write(&self.file_path, text)?;
        Ok(())
    }

    fn file_path_str(&self) -> String {
        self.file_path.display().to_string()
    }
}

impl ConfigStorage for YamlConfigStorage {
    fn load_one(&self, key: &str) -> Result<Option<ConfigDict>, OperonError> {
        let data = self.load_file()?;
        let Some(raw) = data.get(key) else { return Ok(None) };
        let Value::Object(map) = raw else { return Ok(None) };

        let mut missing: Vec<(String, String)> = Vec::new();
        let resolved = interpolate_env_vars(&Value::Object(map.clone()), &mut missing, key);
        if !missing.is_empty() {
            return Err(missing_env_error(&missing, Some(self.file_path.clone())));
        }
        match resolved {
            Value::Object(m) => Ok(Some(m)),
            _ => Ok(None),
        }
    }

    fn load_all(&self) -> Result<HashMap<String, ConfigDict>, OperonError> {
        let data = match self.load_file() {
            Ok(d) => d,
            Err(e) => {
                error!("invalid YAML file {:?}: {}", self.file_path, e);
                return Ok(HashMap::new());
            }
        };

        let mut missing: Vec<(String, String)> = Vec::new();
        let mut out = HashMap::with_capacity(data.len());
        for (key, value) in data {
            let Value::Object(map) = value else { continue };
            let resolved = interpolate_env_vars(&Value::Object(map), &mut missing, &key);
            if let Value::Object(m) = resolved {
                out.insert(key, m);
            }
        }
        if !missing.is_empty() {
            return Err(missing_env_error(&missing, Some(self.file_path.clone())));
        }
        Ok(out)
    }

    fn save(&self, key: &str, config: ConfigDict) -> Result<bool, OperonError> {
        let _guard = self.write_lock.lock();
        match self.load_file() {
            Ok(mut data) => {
                data.insert(key.to_string(), Value::Object(config));
                self.save_file(&data)?;
                debug!("saved config {}", key);
                Ok(true)
            }
            Err(e) => {
                error!("cannot save config '{}': {}", key, e);
                Ok(false)
            }
        }
    }

    fn remove(&self, key: &str) -> Result<bool, OperonError> {
        let _guard = self.write_lock.lock();
        let mut data = self.load_file()?;
        if data.remove(key).is_some() {
            self.save_file(&data)?;
            debug!("removed config {}", key);
            Ok(true)
        } else {
            Ok(false)
        }
    }
}

// ── Env-var interpolation ────────────────────────────────────────────────

/// Walk `value` recursively, substituting `${VAR}` / `${VAR:default}` in strings.
///
/// Missing required vars are collected into `missing` as `(var_name, path)`.
/// `path` uses dotted/bracketed notation (`llm:gpt-4o.api_key`, `x.items[2].url`).
pub(super) fn interpolate_env_vars(
    value: &Value,
    missing: &mut Vec<(String, String)>,
    path: &str,
) -> Value {
    match value {
        Value::String(s) => Value::String(substitute_placeholders(s, missing, path)),
        Value::Object(m) => {
            let mut out = serde_json::Map::with_capacity(m.len());
            for (k, v) in m {
                let child_path = if path.is_empty() {
                    k.clone()
                } else {
                    format!("{}.{}", path, k)
                };
                out.insert(k.clone(), interpolate_env_vars(v, missing, &child_path));
            }
            Value::Object(out)
        }
        Value::Array(arr) => {
            let mut out = Vec::with_capacity(arr.len());
            for (i, v) in arr.iter().enumerate() {
                let child_path = format!("{}[{}]", path, i);
                out.push(interpolate_env_vars(v, missing, &child_path));
            }
            Value::Array(out)
        }
        other => other.clone(),
    }
}

/// Replace every `${VAR}` / `${VAR:default}` in `s`. Missing required vars are
/// collected; the original `${VAR}` placeholder is left in place so the caller
/// can raise a consolidated error.
fn substitute_placeholders(s: &str, missing: &mut Vec<(String, String)>, path: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < bytes.len() {
        // Look for "${"
        if i + 1 < bytes.len() && bytes[i] == b'$' && bytes[i + 1] == b'{' {
            // Find matching '}'.
            if let Some(end_rel) = s[i + 2..].find('}') {
                let end = i + 2 + end_rel;
                let inner = &s[i + 2..end];
                let (var_name, default) = match inner.find(':') {
                    Some(p) => (&inner[..p], Some(&inner[p + 1..])),
                    None => (inner, None),
                };
                // Reject empty var names or inner content with braces.
                if var_name.is_empty() || var_name.contains('{') {
                    out.push_str(&s[i..end + 1]);
                    i = end + 1;
                    continue;
                }
                match std::env::var(var_name) {
                    Ok(v) => out.push_str(&v),
                    Err(_) => {
                        if let Some(d) = default {
                            out.push_str(d);
                        } else {
                            missing.push((var_name.to_string(), path.to_string()));
                            out.push_str(&s[i..end + 1]); // keep literal `${VAR}`
                        }
                    }
                }
                i = end + 1;
                continue;
            }
        }
        out.push(s[i..].chars().next().unwrap());
        i += s[i..].chars().next().unwrap().len_utf8();
    }
    out
}

fn missing_env_error(
    missing: &[(String, String)],
    source_path: Option<PathBuf>,
) -> OperonError {
    // Pick the first missing entry as the canonical (var, key) for the typed
    // variant. The first entry's `path` is something like
    // `"llm:gpt-4o.api_key"`; we strip the field-suffix to recover the
    // resource key. Disambiguates branch (4) from generic Config errors so
    // callers can match on `OperonError::EnvVarUnset`.
    let (var, key) = if let Some((var, path)) = missing.first() {
        let key = path.split_once('.').map(|(k, _)| k).unwrap_or(path).to_string();
        (var.clone(), key)
    } else {
        (String::new(), String::new())
    };
    OperonError::EnvVarUnset {
        var,
        key,
        source_path,
        env_paths: bootstrap_state::env_paths(),
    }
}

/// Walk `value` and call `on_unset(var)` for every `${VAR}` (no default) whose
/// env var is currently unset. Used by [`YamlConfigStorage::warn_on_unset_env_vars`].
fn scan_unset_vars(value: &Value, mut on_unset: impl FnMut(&str)) {
    fn walk(v: &Value, cb: &mut dyn FnMut(&str)) {
        match v {
            Value::String(s) => {
                let bytes = s.as_bytes();
                let mut i = 0;
                while i < bytes.len() {
                    if i + 1 < bytes.len() && bytes[i] == b'$' && bytes[i + 1] == b'{' {
                        if let Some(end_rel) = s[i + 2..].find('}') {
                            let end = i + 2 + end_rel;
                            let inner = &s[i + 2..end];
                            // Skip references with defaults (`${VAR:default}`).
                            let var_name = match inner.find(':') {
                                Some(_) => {
                                    i = end + 1;
                                    continue;
                                }
                                None => inner,
                            };
                            if !var_name.is_empty()
                                && !var_name.contains('{')
                                && std::env::var(var_name).is_err()
                            {
                                cb(var_name);
                            }
                            i = end + 1;
                            continue;
                        }
                    }
                    i += s[i..].chars().next().map(|c| c.len_utf8()).unwrap_or(1);
                }
            }
            Value::Object(m) => {
                for v in m.values() {
                    walk(v, cb);
                }
            }
            Value::Array(arr) => {
                for v in arr {
                    walk(v, cb);
                }
            }
            _ => {}
        }
    }
    walk(value, &mut on_unset);
}

// Unused but exposed for potential callers that want just the path string.
#[allow(dead_code)]
fn path_of(p: &Path) -> String {
    p.display().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn set_var(k: &str, v: &str) {
        // SAFETY: tests are serialized by default in Rust; env vars are process-wide.
        // We use unique names per test to avoid cross-test interference.
        std::env::set_var(k, v);
    }

    fn unset_var(k: &str) {
        std::env::remove_var(k);
    }

    #[test]
    fn interpolates_simple_var() {
        set_var("OPERONX_YAML_TEST_SIMPLE", "hello");
        let mut missing = Vec::new();
        let out = substitute_placeholders("x=${OPERONX_YAML_TEST_SIMPLE}", &mut missing, "k");
        assert_eq!(out, "x=hello");
        assert!(missing.is_empty());
        unset_var("OPERONX_YAML_TEST_SIMPLE");
    }

    #[test]
    fn uses_default_when_unset() {
        unset_var("OPERONX_YAML_TEST_DEFAULT");
        let mut missing = Vec::new();
        let out = substitute_placeholders(
            "x=${OPERONX_YAML_TEST_DEFAULT:fallback}",
            &mut missing,
            "k",
        );
        assert_eq!(out, "x=fallback");
        assert!(missing.is_empty());
    }

    #[test]
    fn collects_missing_required_var() {
        unset_var("OPERONX_YAML_TEST_REQUIRED");
        let mut missing = Vec::new();
        let out = substitute_placeholders(
            "x=${OPERONX_YAML_TEST_REQUIRED}",
            &mut missing,
            "llm.api_key",
        );
        assert_eq!(out, "x=${OPERONX_YAML_TEST_REQUIRED}");
        assert_eq!(missing.len(), 1);
        assert_eq!(missing[0].0, "OPERONX_YAML_TEST_REQUIRED");
        assert_eq!(missing[0].1, "llm.api_key");
    }

    #[test]
    fn interpolates_nested_structure() {
        set_var("OPERONX_YAML_TEST_NESTED", "resolved");
        let input: Value = serde_json::json!({
            "outer": {
                "inner": "${OPERONX_YAML_TEST_NESTED}",
                "list": ["${OPERONX_YAML_TEST_NESTED}:fallback", "static"],
            }
        });
        let mut missing = Vec::new();
        let out = interpolate_env_vars(&input, &mut missing, "root");
        assert_eq!(
            out,
            serde_json::json!({
                "outer": {
                    "inner": "resolved",
                    "list": ["resolved:fallback", "static"],
                }
            })
        );
        assert!(missing.is_empty());
        unset_var("OPERONX_YAML_TEST_NESTED");
    }
}
