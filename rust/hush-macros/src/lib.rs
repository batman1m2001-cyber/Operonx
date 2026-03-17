//! Proc macros for the Hush workflow engine.
//!
//! Provides:
//! - `#[hush_op]` — auto-register Rust ops via `inventory`
//! - `#[hush_model]` — shorthand for `#[derive(Serialize, Deserialize, Debug, Clone)]`
//!
//! # Usage
//!
//! ```rust,ignore
//! use hush_serve::{hush_op, hush_model};
//!
//! #[hush_model]
//! struct Conversation { vads: Vec<Vad> }
//!
//! // Legacy style (untyped):
//! #[hush_op]
//! fn double(inputs: &Value) -> Value {
//!     let x = inputs["x"].as_i64().unwrap();
//!     serde_json::json!({"result": x * 2})
//! }
//!
//! // Typed style (auto-generates serde wrapper):
//! #[hush_op]
//! fn classify(conversation: Conversation, threshold: f64) -> ClassifyResult {
//!     // Pure business logic — no JSON parsing
//! }
//!
//! #[hush_op(generator)]
//! fn each_item(inputs: &Value) -> Value {
//!     let items = inputs["items"].as_array().unwrap();
//!     Value::Array(items.iter().map(|i| serde_json::json!({"value": i})).collect())
//! }
//! ```

use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, ItemFn, ItemStruct, Meta, parse::Parse, parse::ParseStream, LitStr, Token};

/// Auto-register a function as a Hush op.
///
/// Supports both legacy `fn(inputs: &Value) -> Value` and typed signatures.
/// For typed signatures, a serde deserialize/serialize wrapper is generated.
///
/// # Attributes
///
/// - `#[hush_op]` — register as a regular op
/// - `#[hush_op(generator)]` — register as a generator op
/// - `#[hush_op(name = "custom_name")]` — override the op name
/// - `#[hush_op(generator, name = "custom_name")]` — both
#[proc_macro_attribute]
pub fn hush_op(attr: TokenStream, item: TokenStream) -> TokenStream {
    let input_fn = parse_macro_input!(item as ItemFn);
    let fn_name = &input_fn.sig.ident;
    let fn_name_str = fn_name.to_string();

    // Parse attributes
    let mut is_generator = false;
    let mut custom_name: Option<String> = None;

    if !attr.is_empty() {
        let meta_list: syn::punctuated::Punctuated<Meta, syn::Token![,]> =
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

    // Detect typed vs legacy signature
    let is_typed = is_typed_signature(&input_fn);

    let (call_expr, wrapper_fn) = if is_typed {
        generate_typed_wrapper(&input_fn, &op_name)
    } else {
        // Legacy: fn(inputs: &Value) -> Value — no wrapper
        let call = quote! { |v| #fn_name(v) };
        (call, quote! {})
    };

    let submit = if is_generator {
        quote! {
            ::inventory::submit! {
                ::hush_serve::OpEntry::new_gen(#op_name, module_path!(), #call_expr)
            }
        }
    } else {
        quote! {
            ::inventory::submit! {
                ::hush_serve::OpEntry::new_op(#op_name, module_path!(), #call_expr)
            }
        }
    };

    let output = quote! {
        #input_fn
        #wrapper_fn
        #submit
    };

    output.into()
}

/// Check if the function has a typed signature (not `fn(inputs: &Value) -> Value`).
///
/// Legacy detection: single param of type `&Value` (any param name).
/// This covers `inputs: &Value`, `_inputs: &Value`, `input: &Value`, etc.
fn is_typed_signature(func: &ItemFn) -> bool {
    let params: Vec<_> = func.sig.inputs.iter().collect();
    if params.len() == 1 {
        if let syn::FnArg::Typed(pat_type) = &params[0] {
            if is_ref_to_value(&pat_type.ty) {
                return false; // Legacy: single &Value param
            }
        }
    }
    // Multiple params or non-Value param → typed
    !params.is_empty()
}

/// Check if a type is `&Value` or `&serde_json::Value`.
fn is_ref_to_value(ty: &syn::Type) -> bool {
    if let syn::Type::Reference(r) = ty {
        return is_value_type(&r.elem);
    }
    false
}

/// Check if a type is `Value` or `serde_json::Value`.
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

/// Generate a typed wrapper function that deserializes params and serializes the return.
fn generate_typed_wrapper(func: &ItemFn, _op_name: &str) -> (proc_macro2::TokenStream, proc_macro2::TokenStream) {
    let fn_name = &func.sig.ident;
    let wrapper_name = syn::Ident::new(
        &format!("__hush_{}_wrapper", fn_name),
        fn_name.span(),
    );

    // Extract param names and types
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
                        __inputs.get(#param_name_str).cloned().unwrap_or(::serde_json::Value::Null)
                    ) {
                        Ok(v) => v,
                        Err(e) => return ::serde_json::json!({
                            "error": format!("hush_op '{}': param '{}': {}", stringify!(#fn_name), #param_name_str, e)
                        }),
                    };
                });
                call_args.push(quote! { #param_name });
            }
        }
    }

    // Check return type
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
                "error": format!("hush_op '{}': failed to serialize return: {}", stringify!(#fn_name), e)
            }))
        }
    };

    let wrapper = quote! {
        fn #wrapper_name(__inputs: &::serde_json::Value) -> ::serde_json::Value {
            #(#deserialize_stmts)*
            #call_and_return
        }
    };

    let call_expr = quote! { |v| #wrapper_name(v) };

    (call_expr, wrapper)
}

// --- #[hush_resource] ---

struct ResourceArgs {
    name: String,
}

impl Parse for ResourceArgs {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        let _ident: syn::Ident = input.parse()?;
        let _eq: Token![=] = input.parse()?;
        let name: LitStr = input.parse()?;
        Ok(ResourceArgs { name: name.value() })
    }
}

/// Auto-register a function as a resource factory.
#[proc_macro_attribute]
pub fn hush_resource(attr: TokenStream, item: TokenStream) -> TokenStream {
    let args = parse_macro_input!(attr as ResourceArgs);
    let input_fn = parse_macro_input!(item as ItemFn);
    let fn_name = &input_fn.sig.ident;
    let resource_name = &args.name;

    let submit = quote! {
        ::inventory::submit! {
            ::hush_serve::ResourceEntry::new(#resource_name, |config| {
                Box::new(#fn_name(config)) as Box<dyn ::std::any::Any + Send + Sync>
            })
        }
    };

    let output = quote! {
        #input_fn
        #submit
    };

    output.into()
}

// --- #[hush_model] ---

/// Shorthand for `#[derive(Serialize, Deserialize, Debug, Clone)]`.
///
/// ```rust,ignore
/// #[hush_model]
/// struct Conversation {
///     vads: Vec<Vad>,
/// }
/// ```
#[proc_macro_attribute]
pub fn hush_model(_attr: TokenStream, item: TokenStream) -> TokenStream {
    let input_struct = parse_macro_input!(item as ItemStruct);

    let output = quote! {
        #[derive(::serde::Serialize, ::serde::Deserialize, Debug, Clone)]
        #input_struct
    };

    output.into()
}
