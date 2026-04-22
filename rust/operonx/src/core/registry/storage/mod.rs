//! Storage backends for the config registry.
//!
//! Mirrors Python `operon/core/registry/storage/`.

pub mod base;
pub mod json;
pub mod yaml;

pub use base::{ConfigDict, ConfigStorage};
pub use json::JsonConfigStorage;
pub use yaml::YamlConfigStorage;
