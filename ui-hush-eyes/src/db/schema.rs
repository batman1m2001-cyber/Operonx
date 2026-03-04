use rusqlite::Connection;

pub fn create_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            workflow_name TEXT NOT NULL,

            op_name TEXT,
            op_type TEXT,
            parent_name TEXT,
            context_id TEXT,
            execution_order INTEGER,

            start_time TEXT,
            end_time TEXT,
            duration_ms REAL,

            model TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            cost_usd REAL,

            input TEXT,
            output TEXT,

            user_id TEXT,
            session_id TEXT,
            contain_generation INTEGER DEFAULT 0,
            metadata TEXT,
            tags TEXT,

            status TEXT DEFAULT 'flushed',
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_request ON traces(request_id);
        CREATE INDEX IF NOT EXISTS idx_status ON traces(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_created ON traces(created_at);",
    )
}
