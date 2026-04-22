//! `JsonConfigStorage` — all configs in a single JSON file.
//!
//! Mirrors Python [`operon/core/registry/storage/json.py`](../../../../../../operon/core/registry/storage/json.py).
//! Unlike the YAML backend, this one does **not** do env-var interpolation —
//! JSON configs are treated as already-resolved material (typically exported
//! by a tool that has already substituted secrets).

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use parking_lot::Mutex;
use serde_json::Value;
use tracing::{debug, error};

use super::base::{ConfigDict, ConfigStorage};
use crate::core::exceptions::OperonError;

/// JSON-file–backed config store.
///
/// File layout:
/// ```json
/// {
///   "llm:gpt-4o":       { "api_type": "openai", "model": "gpt-4o", "api_key": "sk-xxx" },
///   "embedding:bge-m3": { "api_type": "vllm",   "base_url": "http://localhost:8000/v1" }
/// }
/// ```
pub struct JsonConfigStorage {
    file_path: PathBuf,
    /// Mutex serializes concurrent writes — reads go via `load_file` each call
    /// (tiny file, not a hot path).
    write_lock: Mutex<()>,
}

impl JsonConfigStorage {
    /// Create a storage handle for `file_path`. Parent directory is created if missing.
    pub fn new(file_path: impl Into<PathBuf>) -> Result<Self, OperonError> {
        let file_path = file_path.into();
        if let Some(parent) = file_path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)?;
            }
        }
        Ok(Self {
            file_path,
            write_lock: Mutex::new(()),
        })
    }

    fn load_file(&self) -> Result<HashMap<String, Value>, OperonError> {
        if !self.file_path.exists() {
            return Ok(HashMap::new());
        }
        let text = fs::read_to_string(&self.file_path)?;
        if text.trim().is_empty() {
            return Ok(HashMap::new());
        }
        let parsed: HashMap<String, Value> = serde_json::from_str(&text)?;
        Ok(parsed)
    }

    fn save_file(&self, data: &HashMap<String, Value>) -> Result<(), OperonError> {
        let text = serde_json::to_string_pretty(data)?;
        fs::write(&self.file_path, text)?;
        Ok(())
    }

    fn file_path(&self) -> &Path {
        &self.file_path
    }
}

impl ConfigStorage for JsonConfigStorage {
    fn load_one(&self, key: &str) -> Result<Option<ConfigDict>, OperonError> {
        match self.load_file() {
            Ok(data) => Ok(data.get(key).and_then(|v| match v {
                Value::Object(m) => Some(m.clone()),
                _ => None,
            })),
            Err(e) => {
                error!("cannot load JSON config '{}': {}", key, e);
                Err(e)
            }
        }
    }

    fn load_all(&self) -> Result<HashMap<String, ConfigDict>, OperonError> {
        let data = match self.load_file() {
            Ok(d) => d,
            Err(e) => {
                error!("invalid JSON file {:?}: {}", self.file_path(), e);
                return Ok(HashMap::new());
            }
        };

        let mut out = HashMap::with_capacity(data.len());
        for (k, v) in data {
            if let Value::Object(m) = v {
                out.insert(k, m);
            }
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
