//! Proc-macros for the Operon workflow engine.
//!
//! Provides three attributes, re-exported from the main `operonx` crate so
//! users can `use operonx::{op, resource, model};`:
//!
//! - **`#[op]`** — register a Rust fn as an op, auto-discovered via the
//!   [`inventory`] crate. Supports both the legacy untyped shape
//!   (`fn(inputs: &Value) -> Value`) and a typed signature whose params and
//!   return type are serde-(de)serialized for you.
//! - **`#[resource(name = "...")]`** — register a resource factory.
//! - **`#[model]`** — derive `Serialize + Deserialize + Debug + Clone` on a
//!   struct for config-model DTOs.
//!
//! # Usage
//!
//! ```rust,ignore
//! use operonx::{op, model};
//!
//! #[model]
//! struct Conversation { vads: Vec<i32> }
//!
//! // Legacy style (untyped):
//! #[op]
//! fn double(inputs: &serde_json::Value) -> serde_json::Value {
//!     let x = inputs["x"].as_i64().unwrap();
//!     serde_json::json!({"result": x * 2})
//! }
//!
//! // Typed style (auto-generates a serde wrapper):
//! #[op]
//! fn classify(conversation: Conversation, threshold: f64) -> Conversation {
//!     // Pure business logic — no JSON parsing.
//!     conversation
//! }
//!
//! #[op(generator)]
//! fn each_item(inputs: &serde_json::Value) -> serde_json::Value {
//!     let items = inputs["items"].as_array().unwrap();
//!     serde_json::Value::Array(
//!         items.iter().map(|i| serde_json::json!({"value": i})).collect()
//!     )
//! }
//! ```

use proc_macro::TokenStream;
use quote::quote;
use syn::{
    parse::{Parse, ParseStream},
    parse_macro_input, ItemFn, ItemStruct, LitStr, Meta, Token,
};

// ── #[op] ────────────────────────────────────────────────────────────────

/// Register an `async fn` or sync fn as an Operon op.
///
/// Accepted forms:
///
/// - `#[op]`                        — regular op.
/// - `#[op(generator)]`             — generator op (returns a `Value::Array`).
/// - `#[op(name = "custom_name")]`  — override the registered op name.
/// - `#[op(generator, name = "…")]` — combine both.
///
/// The wrapped fn may use either the untyped shape
/// `fn(inputs: &serde_json::Value) -> serde_json::Value` or a typed
/// signature whose parameters are serde-deserialized from the input object
/// and whose return value is serde-serialized. Typed deserialization failures
/// surface as `Value::Object({"error": "<msg>"})` — matching Python's
/// "error-in-output" convention rather than panicking.
#[proc_macro_attribute]
pub fn op(attr: TokenStream, item: TokenStream) -> TokenStream {
    let input_fn = parse_macro_input!(item as ItemFn);
    let fn_name = &input_fn.sig.ident;
    let fn_name_str = fn_name.to_string();

    let mut is_generator = false;
    let mut custom_name: Option<String> = None;

    if !attr.is_empty() {
        let meta_list: syn::punctuated::Punctuated<Meta, Token![,]> =
            parse_macro_input!(attr with syn::punctuated::Punctuated::parse_terminated);

        for meta in &meta_list {
            match meta {
                Meta::Path(path) if path.is_ident("generator") => {
                    is_generator = true;
                }
                Meta::NameValue(nv) if nv.path.is_ident("name") => {
                    if let syn::Expr::Lit(syn::ExprLit {
                        lit: syn::Lit::Str(s),
                        ..
                    }) = &nv.value
                    {
                        custom_name = Some(s.value());
                    }
                }
                _ => {}
            }
        }
    }

    let op_name = custom_name.unwrap_or(fn_name_str);

    let is_typed = is_typed_signature(&input_fn);

    let (call_expr, wrapper_fn) = if is_typed {
        generate_typed_wrapper(&input_fn)
    } else {
        let call = quote! { |v| #fn_name(v) };
        (call, quote! {})
    };

    let ctor = if is_generator {
        quote! { ::operonx::OpEntry::new_gen }
    } else {
        quote! { ::operonx::OpEntry::new_op }
    };

    let submit = quote! {
        ::operonx::inventory::submit! {
            #ctor(#op_name, module_path!(), #call_expr)
        }
    };

    let output = quote! {
        #input_fn
        #wrapper_fn
        #submit
    };

    output.into()
}

/// `true` when the fn signature *isn't* the legacy untyped single-arg shape
/// `fn(_: &serde_json::Value) -> _`.
fn is_typed_signature(func: &ItemFn) -> bool {
    let params: Vec<_> = func.sig.inputs.iter().collect();
    if params.len() == 1 {
        if let syn::FnArg::Typed(pat_type) = &params[0] {
            if is_ref_to_value(&pat_type.ty) {
                return false;
            }
        }
    }
    !params.is_empty()
}

fn is_ref_to_value(ty: &syn::Type) -> bool {
    if let syn::Type::Reference(r) = ty {
        return is_value_type(&r.elem);
    }
    false
}

fn is_value_type(ty: &syn::Type) -> bool {
    match ty {
        syn::Type::Path(tp) => {
            let segments: Vec<_> = tp.path.segments.iter().collect();
            match segments.len() {
                1 => segments[0].ident == "Value",
                2 => segments[0].ident == "serde_json" && segments[1].ident == "Value",
                _ => false,
            }
        }
        _ => false,
    }
}

/// Generate a sync wrapper fn that pulls each param out of the input object
/// by name, serde-deserializes it, calls the inner fn, then serde-serializes
/// the return value. Deserialization / serialization failures are returned
/// as `{"error": "..."}` in the output `Value`.
fn generate_typed_wrapper(func: &ItemFn) -> (proc_macro2::TokenStream, proc_macro2::TokenStream) {
    let fn_name = &func.sig.ident;
    let wrapper_name = syn::Ident::new(&format!("__operonx_{}_wrapper", fn_name), fn_name.span());

    let mut deserialize_stmts = Vec::new();
    let mut call_args = Vec::new();

    for arg in &func.sig.inputs {
        if let syn::FnArg::Typed(pat_type) = arg {
            if let syn::Pat::Ident(ident) = &*pat_type.pat {
                let param_name = &ident.ident;
                let param_name_str = param_name.to_string();
                let param_type = &pat_type.ty;

                deserialize_stmts.push(quote! {
                    let #param_name: #param_type = match ::serde_json::from_value(
                        __inputs.get(#param_name_str)
                            .cloned()
                            .unwrap_or(::serde_json::Value::Null)
                    ) {
                        Ok(v) => v,
                        Err(e) => return ::serde_json::json!({
                            "error": format!(
                                "op '{}': param '{}': {}",
                                stringify!(#fn_name), #param_name_str, e
                            )
                        }),
                    };
                });
                call_args.push(quote! { #param_name });
            }
        }
    }

    let is_value_return = match &func.sig.output {
        syn::ReturnType::Default => true,
        syn::ReturnType::Type(_, ty) => is_value_type(ty),
    };

    let call_and_return = if is_value_return {
        quote! { #fn_name(#(#call_args),*) }
    } else {
        quote! {
            let __result = #fn_name(#(#call_args),*);
            ::serde_json::to_value(__result).unwrap_or_else(|e| ::serde_json::json!({
                "error": format!(
                    "op '{}': failed to serialize return: {}",
                    stringify!(#fn_name), e
                )
            }))
        }
    };

    let wrapper = quote! {
        fn #wrapper_name(__inputs: &::serde_json::Value) -> ::serde_json::Value {
            #(#deserialize_stmts)*
            #call_and_return
        }
    };

    let call_expr = quote! { #wrapper_name };

    (call_expr, wrapper)
}

// ── #[resource] ──────────────────────────────────────────────────────────

struct ResourceArgs {
    name: String,
}

impl Parse for ResourceArgs {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        let ident: syn::Ident = input.parse()?;
        if ident != "name" {
            return Err(syn::Error::new(
                ident.span(),
                "#[resource] expects `name = \"...\"`",
            ));
        }
        let _eq: Token![=] = input.parse()?;
        let lit: LitStr = input.parse()?;
        Ok(ResourceArgs { name: lit.value() })
    }
}

/// Register a fn as a resource factory under `name = "..."`.
///
/// The fn's return value is boxed as `Box<dyn Any + Send + Sync>` and
/// submitted to the global `inventory` collection for
/// `operonx::ResourceEntry`. Downcasting back to the concrete type is the
/// caller's responsibility — typically via a typed newtype wrapper.
#[proc_macro_attribute]
pub fn resource(attr: TokenStream, item: TokenStream) -> TokenStream {
    let args = parse_macro_input!(attr as ResourceArgs);
    let input_fn = parse_macro_input!(item as ItemFn);
    let fn_name = &input_fn.sig.ident;
    let resource_name = &args.name;

    let submit = quote! {
        ::operonx::inventory::submit! {
            ::operonx::ResourceEntry::new(#resource_name, |config| {
                ::std::boxed::Box::new(#fn_name(config))
                    as ::std::boxed::Box<dyn ::std::any::Any + ::std::marker::Send + ::std::marker::Sync>
            })
        }
    };

    let output = quote! {
        #input_fn
        #submit
    };

    output.into()
}

// ── #[model] ─────────────────────────────────────────────────────────────

/// Shorthand for `#[derive(Serialize, Deserialize, Debug, Clone)]` — the
/// trait combo a config-model DTO almost always needs.
#[proc_macro_attribute]
pub fn model(_attr: TokenStream, item: TokenStream) -> TokenStream {
    let input_struct = parse_macro_input!(item as ItemStruct);

    let output = quote! {
        #[derive(
            ::serde::Serialize,
            ::serde::Deserialize,
            ::std::fmt::Debug,
            ::std::clone::Clone,
        )]
        #input_struct
    };

    output.into()
}
