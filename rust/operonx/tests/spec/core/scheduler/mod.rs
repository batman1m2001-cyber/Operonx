//! Scheduler / dispatch parity fixtures.

use crate::common::run_fixture;

#[tokio::test]
async fn single_code_op() {
    run_fixture("core/scheduler/single_code_op").await;
}

#[tokio::test]
async fn two_op_chain() {
    run_fixture("core/scheduler/two_op_chain").await;
}

#[tokio::test]
async fn parallel_branches() {
    run_fixture("core/scheduler/parallel_branches").await;
}
