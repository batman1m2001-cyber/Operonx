fn main() {
    // glibc < 2.38 compat shim for pre-built ONNX Runtime (ort-sys)
    // Only needed when the "onnx" feature is enabled
    #[cfg(feature = "onnx")]
    {
        cc::Build::new().file("compat.c").compile("compat");
    }
}
