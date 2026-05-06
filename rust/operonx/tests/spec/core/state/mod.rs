//! State / Ref / SCRATCH parity fixtures.

use crate::common::run_fixture;

#[tokio::test]
async fn parent_input_forward() {
    run_fixture("core/state/parent_input_forward").await;
}

#[tokio::test]
async fn explicit_output_mapping() {
    run_fixture("core/state/explicit_output_mapping").await;
}

#[tokio::test]
async fn sum_list_input() {
    run_fixture("core/state/sum_list_input").await;
}

/// Declarative `SCRATCH["k"]` in `inputs={...}` resolves at op-execute time
/// from the per-call scratch space, pre-seeded via
/// `engine.run(scratch=...)`.
#[tokio::test]
async fn scratch_ref_input() {
    run_fixture("core/state/scratch_ref_input").await;
}
