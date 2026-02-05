import * as path from 'path';
import * as os from 'os';
import * as fs from 'fs';
import { execSync } from 'child_process';
import initSqlJs, { Database as SqlJsDatabase } from 'sql.js';

/**
 * Read HUSH_TRACES_DB from .env file in the hush project
 */
function getTracesDbFromEnv(): string | null {
    // Look for .env file in parent directory (hush project root)
    const envPath = path.join(__dirname, '..', '..', '..', '.env');

    if (!fs.existsSync(envPath)) {
        return null;
    }

    const content = fs.readFileSync(envPath, 'utf8');
    const lines = content.split('\n');

    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('HUSH_TRACES_DB=')) {
            let value = trimmed.substring('HUSH_TRACES_DB='.length).trim();
            // Remove quotes if present
            if ((value.startsWith('"') && value.endsWith('"')) ||
                (value.startsWith("'") && value.endsWith("'"))) {
                value = value.slice(1, -1);
            }
            return value;
        }
    }
    return null;
}

export interface TraceRow {
    id: number;
    request_id: string;
    workflow_name: string;
    node_name: string | null;
    node_type: string | null;
    parent_name: string | null;
    context_id: string | null;
    execution_order: number | null;
    start_time: string | null;
    end_time: string | null;
    duration_ms: number | null;
    model: string | null;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    cost_usd: number | null;
    input: string | null;
    output: string | null;
    user_id: string | null;
    session_id: string | null;
    contain_generation: number;
    metadata: string | null;
    tags: string | null;
    status: string;
    created_at: number;
}

export interface TraceSummary {
    request_id: string;
    workflow_name: string;
    start_time: string;
    total_duration_ms: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    total_cost: number;
    node_count: number;
    tags: string[];
    input_preview: string;
    output_preview: string;
}

export interface TraceNode {
    id: number;
    request_id: string;
    session_id: string | null;
    node_name: string;
    node_type: string | null;
    parent_name: string | null;
    context_id: string | null;
    execution_order: number;
    start_time: string | null;
    end_time: string | null;
    duration_ms: number | null;
    model: string | null;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    cost_usd: number | null;
    input: any;
    output: any;
    contain_generation: boolean;
    metadata: any;
    tags: string[] | null;
    children: TraceNode[];
}

let SQL: initSqlJs.SqlJsStatic | null = null;

async function getSqlJs(): Promise<initSqlJs.SqlJsStatic> {
    if (!SQL) {
        // Locate the WASM file in the extension's output directory
        const wasmPath = path.join(__dirname, 'sql-wasm.wasm');
        SQL = await initSqlJs({
            locateFile: () => wasmPath
        });
    }
    return SQL;
}

export class TraceDatabase {
    private dbPath: string;
    private db: SqlJsDatabase | null = null;

    constructor(dbPath?: string) {
        this.dbPath = dbPath || this.getDefaultDbPath();
    }

    private getDefaultDbPath(): string {
        // Check environment variable first
        const envPath = process.env.HUSH_TRACES_DB;
        if (envPath) {
            return envPath;
        }

        // Check .env file from hush project
        const envFileDbPath = getTracesDbFromEnv();
        if (envFileDbPath) {
            return envFileDbPath;
        }

        // Default to ~/.hush/traces.db
        return path.join(os.homedir(), '.hush', 'traces.db');
    }

    public getDbPath(): string {
        return this.dbPath;
    }

    public exists(): boolean {
        return fs.existsSync(this.dbPath);
    }

    public getSize(): number {
        if (!this.exists()) {
            return 0;
        }
        const stats = fs.statSync(this.dbPath);
        return stats.size;
    }

    private checkpointWal(): void {
        // sql.js reads the .db file as a raw buffer and ignores the WAL file.
        // Force a WAL checkpoint so pending writes are flushed to the main file.
        const walPath = this.dbPath + '-wal';
        if (!fs.existsSync(walPath)) {
            return;
        }
        try {
            execSync(
                `python -c "import sqlite3; c=sqlite3.connect(r'${this.dbPath}'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"`,
                { timeout: 5000, stdio: 'ignore' }
            );
        } catch {
            // If python is not available, ignore — manual refresh still works
        }
    }

    private async open(): Promise<SqlJsDatabase> {
        if (!this.db) {
            if (!this.exists()) {
                throw new Error(`Database not found: ${this.dbPath}`);
            }
            this.checkpointWal();
            const sqlJs = await getSqlJs();
            const fileBuffer = fs.readFileSync(this.dbPath);
            this.db = new sqlJs.Database(fileBuffer);
        }
        return this.db;
    }

    public close(): void {
        if (this.db) {
            this.db.close();
            this.db = null;
        }
    }

    private queryToObjects<T>(db: SqlJsDatabase, sql: string, params: any[] = []): T[] {
        const stmt = db.prepare(sql);
        stmt.bind(params);

        const results: T[] = [];
        while (stmt.step()) {
            const row = stmt.getAsObject();
            results.push(row as T);
        }
        stmt.free();
        return results;
    }

    public async getTraceList(options: { limit?: number; offset?: number; timeFilter?: number } = {}): Promise<{ traces: TraceSummary[]; total: number }> {
        const db = await this.open();
        const limit = options.limit || 50;
        const offset = options.offset || 0;
        const timeFilter = options.timeFilter; // seconds ago, undefined = all

        // Build time condition
        let timeCondition = '';
        const params: any[] = [];
        if (timeFilter) {
            const cutoff = Date.now() / 1000 - timeFilter;
            timeCondition = 'AND t.created_at >= ?';
            params.push(cutoff);
        }

        // Get total count
        const countRows = this.queryToObjects<any>(db, `
            SELECT COUNT(DISTINCT t.request_id) as total
            FROM traces t
            WHERE t.status IN ('flushed', 'pending') ${timeCondition}
        `, [...params]);
        const total = countRows[0]?.total || 0;

        // Get root node's duration (parent_name IS NULL) instead of SUM
        // because parent durations already include child durations
        const rows = this.queryToObjects<any>(db, `
            SELECT
                t.request_id,
                t.workflow_name,
                MIN(t.start_time) as start_time,
                MAX(CASE WHEN t.parent_name IS NULL THEN t.duration_ms ELSE 0 END) as total_duration_ms,
                SUM(CASE WHEN t.contain_generation = 1 THEN t.prompt_tokens ELSE 0 END) as prompt_tokens,
                SUM(CASE WHEN t.contain_generation = 1 THEN t.completion_tokens ELSE 0 END) as completion_tokens,
                SUM(CASE WHEN t.contain_generation = 1 THEN t.total_tokens ELSE 0 END) as total_tokens,
                SUM(CASE WHEN t.contain_generation = 1 THEN t.cost_usd ELSE 0 END) as total_cost,
                COUNT(*) as node_count,
                MAX(t.tags) as tags,
                MAX(CASE WHEN t.parent_name IS NULL THEN t.input ELSE NULL END) as root_input,
                MAX(CASE WHEN t.parent_name IS NULL THEN t.output ELSE NULL END) as root_output
            FROM traces t
            WHERE t.status IN ('flushed', 'pending') ${timeCondition}
            GROUP BY t.request_id
            ORDER BY MIN(t.created_at) DESC
            LIMIT ? OFFSET ?
        `, [...params, limit, offset]);

        const traces = rows.map(row => ({
            request_id: row.request_id,
            workflow_name: row.workflow_name,
            start_time: row.start_time || '',
            total_duration_ms: row.total_duration_ms || 0,
            prompt_tokens: row.prompt_tokens || 0,
            completion_tokens: row.completion_tokens || 0,
            total_tokens: row.total_tokens || 0,
            total_cost: row.total_cost || 0,
            node_count: row.node_count,
            tags: this.safeParseJson(row.tags) || [],
            input_preview: this.extractPreview(row.root_input),
            output_preview: this.extractPreview(row.root_output)
        }));

        return { traces, total };
    }

    private extractPreview(jsonStr: string | null): string {
        if (!jsonStr) return '';
        try {
            const obj = JSON.parse(jsonStr);
            // Try to get a meaningful preview from common field names
            if (typeof obj === 'string') return obj;
            if (obj.text) return obj.text;
            if (obj.message) return obj.message;
            if (obj.content) return obj.content;
            if (obj.query) return obj.query;
            if (obj.prompt) return obj.prompt;
            if (obj.input) return obj.input;
            if (obj.result) return obj.result;
            if (obj.output) return obj.output;
            if (obj.response) return obj.response;
            if (obj.answer) return obj.answer;
            // Fallback to JSON string
            return JSON.stringify(obj);
        } catch {
            return jsonStr;
        }
    }

    public async getTraceDetail(requestId: string): Promise<TraceNode[]> {
        const db = await this.open();

        const rows = this.queryToObjects<TraceRow>(db, `
            SELECT *
            FROM traces
            WHERE request_id = ?
            ORDER BY execution_order
        `, [requestId]);

        // Convert flat list to tree structure
        return this.buildTree(rows);
    }

    private buildTree(rows: TraceRow[]): TraceNode[] {
        // node_name + context_id uniquely identifies a node
        // parent_name is the full path of the parent node
        //
        // Key insight:
        // - Iteration nodes have unique names (include indices like iteration[0][0])
        // - Non-iteration nodes (like multiply, validate) share names but differ by context_id
        // - When child has context like "[0].[1]", parent context is prefix "[0]"

        const nodeByKey = new Map<string, TraceNode>();  // node_name:context_id -> node
        const nodesByName = new Map<string, TraceNode[]>(); // node_name -> all nodes with that name
        const allNodes: TraceNode[] = [];
        const roots: TraceNode[] = [];

        // First pass: create all nodes and index them
        for (const row of rows) {
            if (!row.node_name) continue;

            const node: TraceNode = {
                id: row.id,
                request_id: row.request_id,
                session_id: row.session_id,
                node_name: row.node_name,
                node_type: row.node_type,
                parent_name: row.parent_name,
                context_id: row.context_id,
                execution_order: row.execution_order || 0,
                start_time: row.start_time,
                end_time: row.end_time,
                duration_ms: row.duration_ms,
                model: row.model,
                prompt_tokens: row.prompt_tokens,
                completion_tokens: row.completion_tokens,
                total_tokens: row.total_tokens,
                cost_usd: row.cost_usd,
                input: this.safeParseJson(row.input),
                output: this.safeParseJson(row.output),
                contain_generation: row.contain_generation === 1,
                metadata: this.safeParseJson(row.metadata),
                tags: this.safeParseJson(row.tags),
                children: []
            };

            allNodes.push(node);

            // Index by node_name:context_id for unique lookup
            const key = row.context_id
                ? `${row.node_name}:${row.context_id}`
                : row.node_name;
            nodeByKey.set(key, node);

            // Also index by node_name -> list of nodes (for context matching)
            if (!nodesByName.has(row.node_name)) {
                nodesByName.set(row.node_name, []);
            }
            nodesByName.get(row.node_name)!.push(node);
        }

        // Second pass: link parents and children
        for (const node of allNodes) {
            if (!node.parent_name) {
                roots.push(node);
                continue;
            }

            let parent: TraceNode | undefined;

            // Strategy 1: Try exact match with parent's context
            // Child context "[0].[1]" -> parent context "[0]"
            if (node.context_id) {
                const contextParts = node.context_id.split('.');
                if (contextParts.length > 1) {
                    const parentContext = contextParts.slice(0, -1).join('.');
                    parent = nodeByKey.get(`${node.parent_name}:${parentContext}`);
                }
                // Also try same context (for siblings under same iteration)
                if (!parent) {
                    parent = nodeByKey.get(`${node.parent_name}:${node.context_id}`);
                }
            }

            // Strategy 2: Try parent_name without context (for unique iteration node names)
            if (!parent) {
                parent = nodeByKey.get(node.parent_name);
            }

            // Strategy 3: If parent_name has multiple matches, pick by context matching
            if (!parent) {
                const candidates = nodesByName.get(node.parent_name);
                if (candidates && candidates.length > 0) {
                    if (candidates.length === 1) {
                        parent = candidates[0];
                    } else if (node.context_id) {
                        // Find candidate whose context is a prefix of child's context
                        const childContextParts = node.context_id.split('.');
                        for (const candidate of candidates) {
                            if (!candidate.context_id) continue;
                            const parentContextParts = candidate.context_id.split('.');
                            // Check if parent context is prefix of child context
                            if (childContextParts.length > parentContextParts.length) {
                                const prefix = childContextParts.slice(0, parentContextParts.length).join('.');
                                if (prefix === candidate.context_id) {
                                    parent = candidate;
                                    break;
                                }
                            }
                            // Or same context
                            if (candidate.context_id === node.context_id) {
                                parent = candidate;
                                break;
                            }
                        }
                        // Fallback: first candidate
                        if (!parent) {
                            parent = candidates[0];
                        }
                    } else {
                        // No context, pick candidate without context or first
                        parent = candidates.find(c => !c.context_id) || candidates[0];
                    }
                }
            }

            if (parent) {
                parent.children.push(node);
            } else {
                roots.push(node);
            }
        }

        // Sort children by execution order
        const sortChildren = (nodes: TraceNode[]) => {
            nodes.sort((a, b) => a.execution_order - b.execution_order);
            for (const node of nodes) {
                sortChildren(node.children);
            }
        };
        sortChildren(roots);

        return roots;
    }

    private safeParseJson(str: string | null): any {
        if (!str) return null;
        try {
            return JSON.parse(str);
        } catch {
            return str;
        }
    }

    public async clearTraces(): Promise<void> {
        // Close current connection
        this.close();

        if (!this.exists()) {
            return;
        }

        // Open in write mode
        const sqlJs = await getSqlJs();
        const fileBuffer = fs.readFileSync(this.dbPath);
        const db = new sqlJs.Database(fileBuffer);

        db.run('DELETE FROM traces');

        // Save back to file
        const data = db.export();
        const buffer = Buffer.from(data);
        fs.writeFileSync(this.dbPath, buffer);

        db.close();
    }
}
