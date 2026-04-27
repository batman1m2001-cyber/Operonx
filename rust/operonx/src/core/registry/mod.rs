//! Config and resource registries.
//!
//! Mirrors Python `operonx/core/registry/`.

pub mod bootstrap_state;
pub mod config_registry;
pub mod inventory;
pub mod op_registry;
pub mod resource_hub;
pub mod shortcuts;
pub mod storage;

pub use bootstrap_state::{env_paths as bootstrap_env_paths, record_env_path};
pub use config_registry::{registry, ConfigEntry, ConfigRegistry, Factory, ResourceInstance};
pub use inventory::{OpEntry, OpKind, ResourceEntry};
pub use op_registry::{InMemoryOpRegistry, OpFunc, OpRegistry};
pub use resource_hub::{CacheEntry, ResourceHub};
pub use shortcuts::HealthCheckResult;
pub use storage::{ConfigDict, ConfigStorage, JsonConfigStorage, YamlConfigStorage};
