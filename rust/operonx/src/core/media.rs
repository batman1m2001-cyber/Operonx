//! `Media` / `MediaRef` — trace-extractable media wrappers.
//!
//! Mirrors Python [`operon/core/media.py`](../../../../operon/core/media.py).
//!
//! Producer ops wrap media values at the source:
//!
//! ```rust,ignore
//! #[op]
//! fn tts(text: String) -> Map<String, Value> {
//!     Map::from_iter([(
//!         "audio".into(),
//!         Media::bytes(synthesize(&text), "audio/mp3").into_value(),
//!     )])
//! }
//! ```
//!
//! Downstream consumers read the raw value — [`BaseOp::get_inputs`] auto-unwraps
//! `Media` instances so schemas stay plain. The collector walks state directly
//! (not via input binding), so it sees the wrapper intact and extracts blobs
//! into a parallel `node.media` list before flushing to a tracer backend.
//!
//! # JSON representation
//! Rust represents `Media` as a plain JSON object with a reserved marker
//! field `"__media__": true`. This lets it round-trip through [`serde_json::Value`]
//! without a custom deserializer on every downstream struct.
//!
//! [`BaseOp::get_inputs`]: crate::core::ops::base::BaseOp

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// Marker key identifying a [`Media`] payload encoded inside a `Value::Object`.
pub const MEDIA_MARKER: &str = "__media__";

/// Media payload — raw bytes or a URL/path reference + MIME type.
///
/// Matches Python's `Media(data, mime_type)` dataclass; `data` may be either
/// a `bytes` payload (base64 on the wire) or a URL/path `String`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Media {
    /// Raw bytes, a `data:` URL, an http(s) URL, or a file path.
    pub data: MediaData,
    /// MIME type like `image/png`, `audio/mp3`, `video/webm`.
    pub mime_type: String,
}

/// Two-variant payload matching Python's `Union[bytes, str]`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum MediaData {
    /// Raw bytes.
    Bytes(#[serde(with = "base64_bytes")] Vec<u8>),
    /// URL (http/https, data:), file path, or any string reference.
    String(String),
}

impl Media {
    /// Build a `Media` wrapping raw bytes.
    pub fn bytes(data: impl Into<Vec<u8>>, mime_type: impl Into<String>) -> Self {
        Self {
            data: MediaData::Bytes(data.into()),
            mime_type: mime_type.into(),
        }
    }

    /// Build a `Media` wrapping a URL or file-path reference.
    pub fn reference(data: impl Into<String>, mime_type: impl Into<String>) -> Self {
        Self {
            data: MediaData::String(data.into()),
            mime_type: mime_type.into(),
        }
    }

    /// Encode as a JSON object with the `__media__` marker.
    pub fn into_value(self) -> Value {
        let mut obj = Map::new();
        obj.insert(MEDIA_MARKER.into(), Value::Bool(true));
        match self.data {
            MediaData::Bytes(b) => {
                obj.insert("data".into(), Value::String(base64_encode(&b)));
                obj.insert("encoding".into(), Value::String("base64".into()));
            }
            MediaData::String(s) => {
                obj.insert("data".into(), Value::String(s));
            }
        }
        obj.insert("mime_type".into(), Value::String(self.mime_type));
        Value::Object(obj)
    }

    /// Decode a `Value` back into a `Media`, if it carries the marker.
    pub fn from_value(v: &Value) -> Option<Self> {
        let Value::Object(obj) = v else { return None };
        if obj.get(MEDIA_MARKER) != Some(&Value::Bool(true)) {
            return None;
        }
        let mime_type = obj.get("mime_type")?.as_str()?.to_string();
        let data_val = obj.get("data")?.as_str()?;
        let data = match obj.get("encoding").and_then(|v| v.as_str()) {
            Some("base64") => MediaData::Bytes(base64_decode(data_val).ok()?),
            _ => MediaData::String(data_val.to_string()),
        };
        Some(Self { data, mime_type })
    }

    /// Size in bytes — `data.len()` for both variants.
    pub fn size_bytes(&self) -> usize {
        match &self.data {
            MediaData::Bytes(b) => b.len(),
            MediaData::String(s) => s.len(),
        }
    }
}

/// Collector-emitted reference to an extracted media blob.
///
/// The collector replaces each `Media` instance in a node's trace I/O with a
/// `<media:N>` placeholder string and appends a `MediaRef` to the node's
/// `media` list. Tracers walk the list, upload or drop each blob per their
/// backend's capabilities, then substitute the placeholder in the I/O dict
/// at `field_path`.
///
/// `field_path` uses dotted JSONPath-style rooted at `"inputs"` or
/// `"outputs"`, e.g. `"inputs.messages[0].content[1].image_url.url"`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MediaRef {
    pub field_path: String,
    pub data: MediaData,
    pub mime_type: String,
    pub size_bytes: usize,
}

impl MediaRef {
    /// Build a [`MediaRef`] from a [`Media`] + its path in the I/O tree.
    pub fn from_media(media: Media, field_path: impl Into<String>) -> Self {
        let size = media.size_bytes();
        Self {
            field_path: field_path.into(),
            data: media.data,
            mime_type: media.mime_type,
            size_bytes: size,
        }
    }
}

// ── Walk + extract / substitute helpers ──────────────────────────────────

/// Recursively replace `Media` payloads inside `io` with `<media:N>` strings.
///
/// `root` is the field-path prefix — pass `"inputs"` or `"outputs"`. Returns
/// the stripped dict + the list of extracted refs in discovery order.
pub fn extract_media(io: &Map<String, Value>, root: &str) -> (Map<String, Value>, Vec<MediaRef>) {
    let mut refs: Vec<MediaRef> = Vec::new();
    let stripped = io
        .iter()
        .map(|(k, v)| {
            let child_path = format!("{}.{}", root, k);
            (k.clone(), walk(v, &child_path, &mut refs))
        })
        .collect();
    (stripped, refs)
}

fn walk(value: &Value, path: &str, refs: &mut Vec<MediaRef>) -> Value {
    if let Some(media) = Media::from_value(value) {
        let idx = refs.len();
        refs.push(MediaRef::from_media(media, path));
        return Value::String(format!("<media:{}>", idx));
    }
    match value {
        Value::Object(m) => {
            let mapped = m
                .iter()
                .map(|(k, v)| (k.clone(), walk(v, &format!("{}.{}", path, k), refs)))
                .collect();
            Value::Object(mapped)
        }
        Value::Array(a) => {
            let mapped = a
                .iter()
                .enumerate()
                .map(|(i, v)| walk(v, &format!("{}[{}]", path, i), refs))
                .collect();
            Value::Array(mapped)
        }
        other => other.clone(),
    }
}

/// Walk `io` along `field_path` and replace the leaf value with `replacement`.
///
/// Returns `true` on successful substitution, `false` if the path no longer
/// exists (tracer should log and skip). Field paths include the root
/// (`"inputs"`/`"outputs"`) as the first segment — it is ignored since
/// `io` is already at that level.
pub fn substitute_placeholder(
    io: &mut Map<String, Value>,
    field_path: &str,
    replacement: Value,
) -> bool {
    let segments = split_path(field_path);
    if segments.len() < 2 {
        return false;
    }
    // Drop the `"inputs"` / `"outputs"` root.
    let mut iter = segments.into_iter();
    iter.next();
    let last = match iter.next_back() {
        Some(s) => s,
        None => return false,
    };
    let rest: Vec<PathSeg> = iter.collect();

    let mut cursor: &mut Value = match rest.first() {
        Some(PathSeg::Key(k)) => match io.get_mut(k) {
            Some(v) => v,
            None => return false,
        },
        Some(PathSeg::Index(_)) => return false, // root is a map, not a list
        None => {
            // Direct top-level write: the leaf sits in `io`.
            return set_top(io, last, replacement);
        }
    };

    for seg in rest.iter().skip(1) {
        cursor = match step_mut(cursor, seg) {
            Some(c) => c,
            None => return false,
        };
    }
    set(cursor, &last, replacement)
}

// ── Path parsing ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
enum PathSeg {
    Key(String),
    Index(usize),
}

fn split_path(path: &str) -> Vec<PathSeg> {
    let mut out = Vec::new();
    let mut buf = String::new();
    let bytes = path.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let ch = bytes[i] as char;
        if ch == '.' {
            if !buf.is_empty() {
                out.push(PathSeg::Key(std::mem::take(&mut buf)));
            }
            i += 1;
        } else if ch == '[' {
            if !buf.is_empty() {
                out.push(PathSeg::Key(std::mem::take(&mut buf)));
            }
            let end_rel = match path[i..].find(']') {
                Some(p) => p,
                None => return Vec::new(),
            };
            let end = i + end_rel;
            let idx: usize = match path[i + 1..end].parse() {
                Ok(n) => n,
                Err(_) => return Vec::new(),
            };
            out.push(PathSeg::Index(idx));
            i = end + 1;
        } else {
            buf.push(ch);
            i += 1;
        }
    }
    if !buf.is_empty() {
        out.push(PathSeg::Key(buf));
    }
    out
}

fn step_mut<'a>(cursor: &'a mut Value, seg: &PathSeg) -> Option<&'a mut Value> {
    match (cursor, seg) {
        (Value::Object(m), PathSeg::Key(k)) => m.get_mut(k),
        (Value::Array(a), PathSeg::Index(i)) => a.get_mut(*i),
        _ => None,
    }
}

fn set(cursor: &mut Value, seg: &PathSeg, replacement: Value) -> bool {
    match (cursor, seg) {
        (Value::Object(m), PathSeg::Key(k)) if m.contains_key(k) => {
            m.insert(k.clone(), replacement);
            true
        }
        (Value::Array(a), PathSeg::Index(i)) if *i < a.len() => {
            a[*i] = replacement;
            true
        }
        _ => false,
    }
}

fn set_top(io: &mut Map<String, Value>, seg: PathSeg, replacement: Value) -> bool {
    match seg {
        PathSeg::Key(k) if io.contains_key(&k) => {
            io.insert(k, replacement);
            true
        }
        _ => false,
    }
}

// ── base64 helpers (zero-dep alternative uses the `base64` crate already in deps) ──

fn base64_encode(bytes: &[u8]) -> String {
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    STANDARD.encode(bytes)
}

fn base64_decode(s: &str) -> Result<Vec<u8>, base64::DecodeError> {
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    STANDARD.decode(s)
}

mod base64_bytes {
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    use serde::{Deserialize, Deserializer, Serializer};

    pub fn serialize<S: Serializer>(bytes: &Vec<u8>, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(&STANDARD.encode(bytes))
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(d: D) -> Result<Vec<u8>, D::Error> {
        let s = String::deserialize(d)?;
        STANDARD.decode(s).map_err(serde::de::Error::custom)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn media_roundtrip_via_value() {
        let m = Media::bytes(vec![0x01, 0x02, 0x03], "image/png");
        let v = m.clone().into_value();
        let back = Media::from_value(&v).unwrap();
        assert_eq!(back, m);
    }

    #[test]
    fn media_url_reference() {
        let m = Media::reference("https://example.com/a.png", "image/png");
        let v = m.clone().into_value();
        let back = Media::from_value(&v).unwrap();
        assert_eq!(back, m);
    }

    #[test]
    fn extract_media_strips_and_collects() {
        let audio = Media::reference("https://example.com/a.mp3", "audio/mp3").into_value();
        let img = Media::bytes(vec![0xaa, 0xbb], "image/png").into_value();

        let mut io = Map::new();
        io.insert(
            "payload".into(),
            json!({
                "audio": audio,
                "images": [img],
                "text": "hello",
            }),
        );

        let (stripped, refs) = extract_media(&io, "inputs");
        assert_eq!(refs.len(), 2);
        assert_eq!(
            stripped.get("payload").unwrap().get("audio"),
            Some(&Value::String("<media:0>".into()))
        );
        assert_eq!(
            stripped
                .get("payload")
                .unwrap()
                .get("images")
                .unwrap()
                .get(0),
            Some(&Value::String("<media:1>".into()))
        );
        assert_eq!(refs[0].field_path, "inputs.payload.audio");
        assert_eq!(refs[1].field_path, "inputs.payload.images[0]");
        assert_eq!(refs[1].size_bytes, 2);
    }

    #[test]
    fn substitute_replaces_placeholder() {
        let mut io = Map::new();
        io.insert(
            "payload".into(),
            json!({
                "audio": "<media:0>",
                "images": ["<media:1>"],
            }),
        );
        assert!(substitute_placeholder(
            &mut io,
            "inputs.payload.audio",
            Value::String("lf:audio-1".into())
        ));
        assert!(substitute_placeholder(
            &mut io,
            "inputs.payload.images[0]",
            Value::String("lf:img-1".into())
        ));
        assert_eq!(
            io.get("payload").unwrap().get("audio"),
            Some(&Value::String("lf:audio-1".into()))
        );
        assert_eq!(
            io.get("payload").unwrap().get("images").unwrap().get(0),
            Some(&Value::String("lf:img-1".into()))
        );
    }
}
