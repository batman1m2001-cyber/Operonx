//! OpenAI Batch API coordinator — mirrors hush-providers batch_coordinator.py.
//!
//! Accumulates chat completion requests, flushes them as OpenAI Batch API jobs,
//! polls for completion, and resolves individual request futures.
//!
//! Benefits: 50% cheaper than real-time API calls.

pub mod coordinator;
