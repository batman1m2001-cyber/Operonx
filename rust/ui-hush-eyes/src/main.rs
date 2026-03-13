mod api;
mod config;
mod db;

use clap::Parser;
use config::Config;
use std::net::SocketAddr;

#[tokio::main]
async fn main() {
    let config = Config::parse();
    let db_path = config.db_path();

    // Initialize database
    let pool = db::init_db(&db_path).expect("Failed to initialize database");

    // Build router
    let app = api::router(pool, &db_path);

    // Start server
    let addr: SocketAddr = format!("{}:{}", config.host, config.port)
        .parse()
        .expect("Invalid address");

    println!("Hush Eyes server running at http://{}", addr);
    println!("Database: {}", db_path.display());

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("Failed to bind");
    axum::serve(listener, app).await.expect("Server error");
}
