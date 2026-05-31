//! Built-in exporters for the trace pipeline.
//!
//! Mirrors Python `core/tracing/exporters/`. Each exporter implements
//! `Exporter::export` and is given the post-processor event batch.

pub mod local_file;

pub use local_file::JsonFileExporter;
