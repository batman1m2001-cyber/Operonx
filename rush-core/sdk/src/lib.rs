//! rush-ops-sdk — SDK for building Rust op plugins for the Hush workflow engine.
//!
//! Users create a `cdylib` crate, write plain Rust functions that take
//! `&serde_json::Value` inputs and return `serde_json::Value` outputs,
//! then call `export_ops!` to generate the C ABI wrappers.
//!
//! # Example
//!
//! ```rust
//! use rush_ops_sdk::{export_ops, serde_json};
//! use serde_json::Value;
//!
//! fn double(inputs: &Value) -> Value {
//!     let x = inputs["x"].as_i64().unwrap();
//!     serde_json::json!({"result": x * 2})
//! }
//!
//! fn add(inputs: &Value) -> Value {
//!     let a = inputs["a"].as_f64().unwrap();
//!     let b = inputs["b"].as_f64().unwrap();
//!     serde_json::json!({"result": a + b})
//! }
//!
//! export_ops!(double, add);
//! ```
//!
//! Then in Python:
//! ```python
//! @op(rust="./my_ops::double")
//! def double(x: int):
//!     return {"result": x * 2}  # Python fallback
//! ```

// Re-export dependencies so users don't need to add them separately.
pub use paste;
pub use serde_json;

/// Generate C ABI wrappers for Rust op functions.
///
/// Each function must have signature: `fn(&serde_json::Value) -> serde_json::Value`
///
/// Generates for each function `foo`:
/// - `rush_op_foo` — C ABI entry point (JSON bytes in → JSON bytes out)
///
/// Also generates:
/// - `rush_ops_list` — returns comma-separated op names (null-terminated)
/// - `rush_ops_free` — frees output buffers allocated by op functions
#[macro_export]
macro_rules! export_ops {
    ($($fn_name:ident),* $(,)?) => {
        $(
            $crate::paste::paste! {
                #[no_mangle]
                pub unsafe extern "C" fn [<rush_op_ $fn_name>](
                    input_ptr: *const u8,
                    input_len: usize,
                    output_ptr: *mut *mut u8,
                    output_len: *mut usize,
                ) -> i32 {
                    // Parse input JSON
                    let input_bytes = unsafe { std::slice::from_raw_parts(input_ptr, input_len) };
                    let inputs: $crate::serde_json::Value = match $crate::serde_json::from_slice(input_bytes) {
                        Ok(v) => v,
                        Err(_) => return -1,
                    };

                    // Call the user's function
                    let outputs = $fn_name(&inputs);

                    // Serialize output JSON
                    let out_bytes = match $crate::serde_json::to_vec(&outputs) {
                        Ok(v) => v,
                        Err(_) => return -2,
                    };

                    // Transfer ownership to caller (caller frees via rush_ops_free)
                    let len = out_bytes.len();
                    let ptr = Box::into_raw(out_bytes.into_boxed_slice()) as *mut u8;
                    unsafe {
                        *output_ptr = ptr;
                        *output_len = len;
                    }
                    0
                }
            }
        )*

        /// Returns a comma-separated list of exported op names (null-terminated).
        #[no_mangle]
        pub extern "C" fn rush_ops_list() -> *const std::ffi::c_char {
            concat!($(stringify!($fn_name), ",",)* "\0").as_ptr() as *const std::ffi::c_char
        }

        /// Free an output buffer allocated by an op function.
        #[no_mangle]
        pub unsafe extern "C" fn rush_ops_free(ptr: *mut u8, len: usize) {
            if !ptr.is_null() && len > 0 {
                unsafe {
                    drop(Box::from_raw(std::slice::from_raw_parts_mut(ptr, len)));
                }
            }
        }
    };
}
