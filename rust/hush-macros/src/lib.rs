//! Proc macros for the Hush workflow engine.
//!
//! Provides `#[hush_op]` for auto-registering Rust ops via `inventory`.
//!
//! # Usage
//!
//! ```rust,ignore
//! use hush_serve::hush_op;
//! use serde_json::Value;
//!
//! #[hush_op]
//! fn double(inputs: &Value) -> Value {
//!     let x = inputs["x"].as_i64().unwrap();
//!     serde_json::json!({"result": x * 2})
//! }
//!
//! #[hush_op(generator)]
//! fn each_item(inputs: &Value) -> Value {
//!     // Return a JSON array — each element becomes a yield
//!     let items = inputs["items"].as_array().unwrap();
//!     Value::Array(items.iter().map(|i| serde_json::json!({"value": i})).collect())
//! }
//! ```
//!
//! Then in `main.rs`:
//! ```rust,ignore
//! HushServer::builder()
//!     .auto_register()  // discovers all #[hush_op] functions
//!     .from_cli()
//!     .serve()
//! ```

use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, ItemFn, Meta, parse::Parse, parse::ParseStream, LitStr, Token};

/// Auto-register a function as a Hush op.
///
/// # Attributes
///
/// - `#[hush_op]` — register as a regular op
/// - `#[hush_op(generator)]` — register as a generator op
/// - `#[hush_op(name = "custom_name")]` — override the op name (default: function name)
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

    let submit = if is_generator {
        quote! {
            ::inventory::submit! {
                ::hush_serve::OpEntry::new_gen(#op_name, |v| #fn_name(v))
            }
        }
    } else {
        quote! {
            ::inventory::submit! {
                ::hush_serve::OpEntry::new_op(#op_name, |v| #fn_name(v))
            }
        }
    };

    let output = quote! {
        #input_fn
        #submit
    };

    output.into()
}

// --- #[hush_resource] ---

struct ResourceArgs {
    name: String,
}

impl Parse for ResourceArgs {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        // Parse: name = "embedder"
        let _ident: syn::Ident = input.parse()?;
        let _eq: Token![=] = input.parse()?;
        let name: LitStr = input.parse()?;
        Ok(ResourceArgs { name: name.value() })
    }
}

/// Auto-register a function as a resource factory.
///
/// The function takes `&serde_json::Value` (config from the JSON `resources` section)
/// and returns a value of any `Send + Sync + 'static` type.
///
/// # Example
///
/// ```rust,ignore
/// #[hush_resource(name = "embedder")]
/// fn load_embedder(config: &Value) -> OnnxEmbedder {
///     OnnxEmbedder::with_cache(
///         config["model_path"].as_str().unwrap(),
///         config["pool_size"].as_u64().unwrap_or(4) as usize,
///         config["cache_path"].as_str().unwrap_or("cache/embeddings.bin"),
///     ).unwrap()
/// }
/// ```
///
/// Then `auto_register()` discovers it and calls it with the config.
/// Access in ops via `hush_serve::get::<OnnxEmbedder>()`.
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
