//! Typed resource injection — `.provide()` resources, `get::<T>()` in ops.
//!
//! Resources are stored in a global `OnceLock<TypeMap>` initialized by the builder
//! before the server starts. Ops retrieve them by type via `get::<T>()`.
//!
//! # Example
//!
//! ```rust,ignore
//! // In main.rs:
//! HushServer::builder()
//!     .provide(OnnxEmbedder::new("./models/bge-m3").unwrap())
//!     .provide(SentimentModels::load())
//!     .auto_register()
//!     .from_cli()
//!     .serve()
//!
//! // In any #[hush_op] function:
//! let embedder = hush_serve::get::<OnnxEmbedder>();
//! let models = hush_serve::get::<SentimentModels>();
//! ```

use std::any::{Any, TypeId};
use std::collections::HashMap;
use std::sync::{Arc, OnceLock};

/// Type-erased map of resources.
pub type ResourceMap = HashMap<TypeId, Arc<dyn Any + Send + Sync>>;

static RESOURCES: OnceLock<ResourceMap> = OnceLock::new();

/// Initialize the global resource store. Called once by the builder before serving.
///
/// Panics if called more than once.
pub fn init_resources(map: ResourceMap) {
    RESOURCES
        .set(map)
        .expect("Resources already initialized — .serve() called twice?");
}

/// Retrieve a provided resource by type.
///
/// Panics with a clear message if:
/// - Resources haven't been initialized (`.serve()` not called yet)
/// - The requested type was not `.provide()`'d
pub fn get<T: Send + Sync + 'static>() -> Arc<T> {
    let map = RESOURCES
        .get()
        .expect("Resources not initialized — call .provide() before .serve()");
    let type_id = TypeId::of::<T>();
    map.get(&type_id)
        .unwrap_or_else(|| {
            panic!(
                "Resource `{}` not provided. Add .provide({}) to your HushServer builder.",
                std::any::type_name::<T>(),
                std::any::type_name::<T>(),
            )
        })
        .clone()
        .downcast::<T>()
        .unwrap_or_else(|_| panic!("Resource type mismatch for `{}`", std::any::type_name::<T>()))
}

#[cfg(test)]
mod tests {
    use super::*;

    // Note: Tests that call init_resources() can only run once per process
    // because OnceLock is global. These tests verify the API shape.

    #[test]
    fn test_resource_map_building() {
        let mut map = ResourceMap::new();
        map.insert(TypeId::of::<String>(), Arc::new("hello".to_string()));
        map.insert(TypeId::of::<i32>(), Arc::new(42i32));

        let s = map
            .get(&TypeId::of::<String>())
            .unwrap()
            .clone()
            .downcast::<String>()
            .unwrap();
        assert_eq!(*s, "hello");

        let n = map
            .get(&TypeId::of::<i32>())
            .unwrap()
            .clone()
            .downcast::<i32>()
            .unwrap();
        assert_eq!(*n, 42);
    }
}
