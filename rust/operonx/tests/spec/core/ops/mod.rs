//! Op-type parity fixtures (prompt, code, etc.).

use crate::common::run_fixture;

#[tokio::test]
async fn prompt_basic() {
    run_fixture("core/ops/prompt_basic").await;
}

#[tokio::test]
async fn prompt_list_passthrough() {
    run_fixture("core/ops/prompt_list_passthrough").await;
}

#[tokio::test]
async fn classify_branch() {
    run_fixture("core/ops/classify_branch").await;
}

#[tokio::test]
async fn wrap_user_message() {
    run_fixture("core/ops/wrap_user_message").await;
}
