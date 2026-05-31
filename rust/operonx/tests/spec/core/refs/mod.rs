//! Ref transform parity fixtures.

use crate::common::run_fixture;

#[tokio::test]
async fn getattr_dict() {
    run_fixture("core/refs/getattr_dict").await;
}
