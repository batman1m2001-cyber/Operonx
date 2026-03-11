//! Multimodal image support — encode local images to base64 data URLs.
//!
//! Mirrors Python's `hush.providers.llms.base.encode_image()` and
//! `resolve_image_paths()` for handling local image files in messages.

use base64::Engine;
use serde_json::Value;
use std::path::Path;

/// Encode a local image file to a base64 data URL.
///
/// Returns `data:{mime};base64,{encoded}` or None if the file can't be read.
pub fn encode_image(path: &str) -> Option<String> {
    let bytes = std::fs::read(path).ok()?;
    let mime = mime_from_extension(path)?;
    let encoded = base64::engine::general_purpose::STANDARD.encode(&bytes);
    Some(format!("data:{};base64,{}", mime, encoded))
}

/// Detect MIME type from file extension.
fn mime_from_extension(path: &str) -> Option<&'static str> {
    let ext = Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_lowercase());

    match ext.as_deref() {
        Some("jpg" | "jpeg") => Some("image/jpeg"),
        Some("png") => Some("image/png"),
        Some("gif") => Some("image/gif"),
        Some("webp") => Some("image/webp"),
        Some("bmp") => Some("image/bmp"),
        Some("svg") => Some("image/svg+xml"),
        Some("tiff" | "tif") => Some("image/tiff"),
        Some("ico") => Some("image/x-icon"),
        _ => None,
    }
}

/// Walk messages and resolve local image paths to base64 data URLs.
///
/// For each message with a content array (multimodal format),
/// finds items with `type: "image_url"` where the URL is a local path
/// (not starting with http://, https://, or data:) and replaces it
/// with a base64 data URL.
pub fn resolve_image_paths(messages: &mut Value) {
    let arr = match messages.as_array_mut() {
        Some(a) => a,
        None => return,
    };

    for msg in arr.iter_mut() {
        let content = match msg.get_mut("content") {
            Some(c) => c,
            None => continue,
        };

        // Only process array-format content (multimodal messages)
        let content_arr = match content.as_array_mut() {
            Some(a) => a,
            None => continue,
        };

        for item in content_arr.iter_mut() {
            if item.get("type").and_then(|t| t.as_str()) != Some("image_url") {
                continue;
            }

            let url = match item
                .get("image_url")
                .and_then(|iu| iu.get("url"))
                .and_then(|u| u.as_str())
            {
                Some(u) => u.to_string(),
                None => continue,
            };

            // Skip URLs that are already remote or data URIs
            if url.starts_with("http://")
                || url.starts_with("https://")
                || url.starts_with("data:")
            {
                continue;
            }

            // Try to encode the local file
            if let Some(data_url) = encode_image(&url) {
                if let Some(image_url) = item.get_mut("image_url") {
                    image_url["url"] = Value::String(data_url);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::Write;

    // =========================================================================
    // mime_from_extension tests
    // =========================================================================

    #[test]
    fn test_mime_jpeg() {
        assert_eq!(mime_from_extension("photo.jpg"), Some("image/jpeg"));
        assert_eq!(mime_from_extension("photo.jpeg"), Some("image/jpeg"));
        assert_eq!(mime_from_extension("photo.JPG"), Some("image/jpeg"));
    }

    #[test]
    fn test_mime_png() {
        assert_eq!(mime_from_extension("image.png"), Some("image/png"));
        assert_eq!(mime_from_extension("image.PNG"), Some("image/png"));
    }

    #[test]
    fn test_mime_gif() {
        assert_eq!(mime_from_extension("anim.gif"), Some("image/gif"));
    }

    #[test]
    fn test_mime_webp() {
        assert_eq!(mime_from_extension("photo.webp"), Some("image/webp"));
    }

    #[test]
    fn test_mime_bmp() {
        assert_eq!(mime_from_extension("img.bmp"), Some("image/bmp"));
    }

    #[test]
    fn test_mime_svg() {
        assert_eq!(mime_from_extension("icon.svg"), Some("image/svg+xml"));
    }

    #[test]
    fn test_mime_tiff() {
        assert_eq!(mime_from_extension("scan.tiff"), Some("image/tiff"));
        assert_eq!(mime_from_extension("scan.tif"), Some("image/tiff"));
    }

    #[test]
    fn test_mime_ico() {
        assert_eq!(mime_from_extension("favicon.ico"), Some("image/x-icon"));
    }

    #[test]
    fn test_mime_unknown() {
        assert_eq!(mime_from_extension("doc.pdf"), None);
        assert_eq!(mime_from_extension("script.py"), None);
        assert_eq!(mime_from_extension("noext"), None);
    }

    #[test]
    fn test_mime_with_path() {
        assert_eq!(
            mime_from_extension("/home/user/images/photo.png"),
            Some("image/png")
        );
        assert_eq!(
            mime_from_extension("C:\\Users\\img.jpg"),
            Some("image/jpeg")
        );
    }

    // =========================================================================
    // encode_image tests
    // =========================================================================

    #[test]
    fn test_encode_image_creates_data_url() {
        // Create a temp file with some content
        let mut tmp = tempfile::NamedTempFile::with_suffix(".png").unwrap();
        tmp.write_all(b"fake png data").unwrap();
        tmp.flush().unwrap();

        let result = encode_image(tmp.path().to_str().unwrap());
        assert!(result.is_some());
        let data_url = result.unwrap();
        assert!(data_url.starts_with("data:image/png;base64,"));
    }

    #[test]
    fn test_encode_image_jpeg() {
        let mut tmp = tempfile::NamedTempFile::with_suffix(".jpg").unwrap();
        tmp.write_all(b"fake jpeg data").unwrap();
        tmp.flush().unwrap();

        let result = encode_image(tmp.path().to_str().unwrap());
        assert!(result.is_some());
        assert!(result.unwrap().starts_with("data:image/jpeg;base64,"));
    }

    #[test]
    fn test_encode_image_nonexistent() {
        let result = encode_image("/nonexistent/path/image.png");
        assert!(result.is_none());
    }

    #[test]
    fn test_encode_image_unknown_extension() {
        let mut tmp = tempfile::NamedTempFile::with_suffix(".xyz").unwrap();
        tmp.write_all(b"some data").unwrap();
        tmp.flush().unwrap();

        let result = encode_image(tmp.path().to_str().unwrap());
        assert!(result.is_none()); // Unknown extension → None
    }

    #[test]
    fn test_encode_image_base64_roundtrip() {
        let original_data = b"test image content 123";
        let mut tmp = tempfile::NamedTempFile::with_suffix(".png").unwrap();
        tmp.write_all(original_data).unwrap();
        tmp.flush().unwrap();

        let data_url = encode_image(tmp.path().to_str().unwrap()).unwrap();
        // Extract base64 part
        let b64 = data_url.strip_prefix("data:image/png;base64,").unwrap();
        let decoded = base64::engine::general_purpose::STANDARD.decode(b64).unwrap();
        assert_eq!(decoded, original_data);
    }

    // =========================================================================
    // resolve_image_paths tests
    // =========================================================================

    #[test]
    fn test_resolve_image_paths_local_file() {
        // Create a temp image file
        let mut tmp = tempfile::NamedTempFile::with_suffix(".png").unwrap();
        tmp.write_all(b"png bytes").unwrap();
        tmp.flush().unwrap();
        let path = tmp.path().to_str().unwrap().to_string();

        let mut messages = json!([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": path}
                    }
                ]
            }
        ]);

        resolve_image_paths(&mut messages);

        let url = messages[0]["content"][1]["image_url"]["url"]
            .as_str()
            .unwrap();
        assert!(url.starts_with("data:image/png;base64,"));
    }

    #[test]
    fn test_resolve_image_paths_skips_http() {
        let mut messages = json!([
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/img.png"}
                    }
                ]
            }
        ]);

        resolve_image_paths(&mut messages);

        // Should be unchanged
        assert_eq!(
            messages[0]["content"][0]["image_url"]["url"],
            "https://example.com/img.png"
        );
    }

    #[test]
    fn test_resolve_image_paths_skips_data_uri() {
        let mut messages = json!([
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc123"}
                    }
                ]
            }
        ]);

        resolve_image_paths(&mut messages);

        assert_eq!(
            messages[0]["content"][0]["image_url"]["url"],
            "data:image/png;base64,abc123"
        );
    }

    #[test]
    fn test_resolve_image_paths_string_content_unchanged() {
        let mut messages = json!([
            {"role": "user", "content": "Just a text message"}
        ]);

        resolve_image_paths(&mut messages);

        // String content should be left alone
        assert_eq!(messages[0]["content"], "Just a text message");
    }

    #[test]
    fn test_resolve_image_paths_non_array_noop() {
        let mut messages = json!("not an array");
        resolve_image_paths(&mut messages);
        assert_eq!(messages, json!("not an array"));
    }

    #[test]
    fn test_resolve_image_paths_text_type_unchanged() {
        let mut messages = json!([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello"}
                ]
            }
        ]);

        resolve_image_paths(&mut messages);

        // Text items should be completely unchanged
        assert_eq!(messages[0]["content"][0]["type"], "text");
        assert_eq!(messages[0]["content"][0]["text"], "Hello");
    }
}
