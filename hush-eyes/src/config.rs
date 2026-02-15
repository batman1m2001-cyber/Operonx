use clap::Parser;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(name = "hush-eyes", about = "Hush trace visualization server")]
pub struct Config {
    /// Port to listen on
    #[arg(short, long, default_value = "8420")]
    pub port: u16,

    /// Host to bind to
    #[arg(long, default_value = "127.0.0.1")]
    pub host: String,

    /// Path to SQLite database
    #[arg(short, long, env = "HUSH_TRACES_DB")]
    pub db: Option<PathBuf>,
}

impl Config {
    pub fn db_path(&self) -> PathBuf {
        self.db.clone().unwrap_or_else(|| {
            dirs::home_dir()
                .unwrap_or_else(|| PathBuf::from("."))
                .join(".hush")
                .join("traces.db")
        })
    }
}
