//! Iteration parity fixtures — loops, generators, map/reduce.

use crate::common::run_fixture;

#[tokio::test]
async fn loop_counter_until() {
    run_fixture("core/iteration/loop_counter_until").await;
}
