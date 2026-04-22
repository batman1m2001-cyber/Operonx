//! Op-level param helpers.
//!
//! Python's [`ops/_params.py`](../../../../../operon/core/ops/_params.py) holds
//! `normalize_params` / `merge_params` / `resolve_value` — all **authoring-layer**
//! (they run while building the graph in Python). At Rust runtime the serialized
//! JSON already carries fully-normalized [`Param`](super::super::utils::common::Param)
//! values, so there's nothing to port here.
//!
//! This module is reserved for any future runtime-side param helpers
//! (e.g., default-value application, coercion) if the need arises.
