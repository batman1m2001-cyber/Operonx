//! hush-plugin — Macro for building Hush workflow op plugins as cdylib crates.
//!
//! Provides the `hush_plugin!` macro that generates the C ABI exports
//! required for hush-serve to load your ops at runtime via `libloading`.
//!
//! # Usage
//!
//! ```rust,ignore
//! // In your Cargo.toml:
//! // [lib]
//! // crate-type = ["cdylib"]
//! //
//! // [dependencies]
//! // serde_json = "1"
//! // hush-plugin = { path = "../../rust/hush-plugin" }
//!
//! mod math;
//! mod text;
//!
//! hush_plugin::hush_plugin! {
//!     call: {
//!         "double" => math::double,
//!         "clean_text" => text::clean_text,
//!     },
//!     call_generator: {
//!         "range_gen" => math::range_gen,
//!     }
//! }
//! ```
//!
//! # C ABI Contract
//!
//! The macro generates three `extern "C"` functions:
//!
//! - `hush_call(name, inputs_json) -> *mut c_char` — call a regular op
//! - `hush_call_generator(name, inputs_json) -> *mut c_char` — call a generator op
//! - `hush_free_str(ptr)` — free a string returned by the above
//!
//! Data crosses the FFI boundary as null-terminated JSON strings.
//! Returns null if the op name is not found.

pub use serde_json;

/// Generate the cdylib FFI exports for a set of ops and generators.
///
/// # Syntax
///
/// ```rust,ignore
/// hush_plugin::hush_plugin! {
///     call: {
///         "op_name" => module::function,
///         ...
///     },
///     call_generator: {
///         "gen_name" => module::gen_function,
///         ...
///     }
/// }
/// ```
///
/// The `call_generator` section is optional.
#[macro_export]
macro_rules! hush_plugin {
    // With both call and call_generator
    (
        call: { $($name:literal => $func:path),* $(,)? },
        call_generator: { $($gen_name:literal => $gen_func:path),* $(,)? }
    ) => {
        $crate::_hush_plugin_impl!($($name => $func),*; $($gen_name => $gen_func),*);
    };
    // call only, no generators
    (
        call: { $($name:literal => $func:path),* $(,)? }
    ) => {
        $crate::_hush_plugin_impl!($($name => $func),*;);
    };
}

#[doc(hidden)]
#[macro_export]
macro_rules! _hush_plugin_impl {
    ($($name:literal => $func:path),*; $($gen_name:literal => $gen_func:path),*) => {
        /// Dispatch a regular op by name. Returns JSON string or null.
        ///
        /// # Safety
        /// Caller must pass valid null-terminated UTF-8 strings.
        /// Caller must free the returned pointer via `hush_free_str`.
        #[no_mangle]
        pub unsafe extern "C" fn hush_call(
            name_ptr: *const std::ffi::c_char,
            input_ptr: *const std::ffi::c_char,
        ) -> *mut std::ffi::c_char {
            let name = match unsafe { std::ffi::CStr::from_ptr(name_ptr) }.to_str() {
                Ok(s) => s,
                Err(_) => return std::ptr::null_mut(),
            };
            let input_str = match unsafe { std::ffi::CStr::from_ptr(input_ptr) }.to_str() {
                Ok(s) => s,
                Err(_) => return std::ptr::null_mut(),
            };
            let inputs: $crate::serde_json::Value = match $crate::serde_json::from_str(input_str) {
                Ok(v) => v,
                Err(_) => return std::ptr::null_mut(),
            };

            let result: Option<$crate::serde_json::Value> = match name {
                $($name => Some($func(&inputs)),)*
                _ => None,
            };

            match result {
                Some(val) => {
                    let json_str = $crate::serde_json::to_string(&val).unwrap_or_default();
                    match std::ffi::CString::new(json_str) {
                        Ok(cs) => cs.into_raw(),
                        Err(_) => std::ptr::null_mut(),
                    }
                }
                None => std::ptr::null_mut(),
            }
        }

        /// Dispatch a generator op by name. Returns JSON array string or null.
        ///
        /// # Safety
        /// Same as `hush_call`.
        #[no_mangle]
        pub unsafe extern "C" fn hush_call_generator(
            name_ptr: *const std::ffi::c_char,
            input_ptr: *const std::ffi::c_char,
        ) -> *mut std::ffi::c_char {
            let name = match unsafe { std::ffi::CStr::from_ptr(name_ptr) }.to_str() {
                Ok(s) => s,
                Err(_) => return std::ptr::null_mut(),
            };
            let input_str = match unsafe { std::ffi::CStr::from_ptr(input_ptr) }.to_str() {
                Ok(s) => s,
                Err(_) => return std::ptr::null_mut(),
            };
            let inputs: $crate::serde_json::Value = match $crate::serde_json::from_str(input_str) {
                Ok(v) => v,
                Err(_) => return std::ptr::null_mut(),
            };

            let result: Option<$crate::serde_json::Value> = match name {
                $($gen_name => Some($gen_func(&inputs)),)*
                _ => None,
            };

            match result {
                Some(val) => {
                    // Ensure it's an array — if the function returns Value::Array, serialize directly.
                    // If it returns something else, wrap in an array.
                    let arr = if val.is_array() { val } else { $crate::serde_json::Value::Array(vec![val]) };
                    let json_str = $crate::serde_json::to_string(&arr).unwrap_or_default();
                    match std::ffi::CString::new(json_str) {
                        Ok(cs) => cs.into_raw(),
                        Err(_) => std::ptr::null_mut(),
                    }
                }
                None => std::ptr::null_mut(),
            }
        }

        /// Free a string returned by `hush_call` or `hush_call_generator`.
        ///
        /// # Safety
        /// Pointer must have been returned by `hush_call` or `hush_call_generator`.
        #[no_mangle]
        pub unsafe extern "C" fn hush_free_str(ptr: *mut std::ffi::c_char) {
            if !ptr.is_null() {
                drop(unsafe { std::ffi::CString::from_raw(ptr) });
            }
        }
    };
}
