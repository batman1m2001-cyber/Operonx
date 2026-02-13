"""Background worker loop and subprocess entry point.

This module provides:
- worker_loop(): The main loop shared by both multiprocessing and subprocess modes
- _background_worker(): Thin wrapper for multiprocessing.Process target
- main(): CLI entry point for subprocess.Popen mode (reads JSON from stdin)

Usage as subprocess:
    python -m hush.core.background.worker --db-path /path/to/traces.db [--config-path ...] [--dotenv-path ...]
"""

import argparse
import os
import queue
import signal
import sqlite3
import sys
import threading
import traceback
from pathlib import Path
from time import sleep, time
from typing import Optional

import orjson

from hush.core.background.db import fetch_pending, init_db, mark_complete, write_traces_batch
from hush.core.background.flush import dispatch_flush, rebuild_flush_data


def worker_loop(
    task_queue,
    conn: sqlite3.Connection,
    poll_interval: float = 2.0,
    batch_size: int = 10,
    max_retries: int = 3,
) -> None:
    """Main worker loop that processes tasks and flushes traces.

    This loop is shared by both multiprocessing.Process and subprocess.Popen modes.
    The only difference is the queue type:
    - multiprocessing mode: multiprocessing.Queue
    - subprocess mode: queue.Queue (fed by stdin reader thread)

    Args:
        task_queue: Queue to read tasks from (multiprocessing.Queue or queue.Queue)
        conn: SQLite database connection
        poll_interval: Seconds between flush polls
        batch_size: Max requests per flush poll
        max_retries: Max retry attempts for failed traces
    """
    last_flush_time = time()
    running = True
    pending_traces = []  # Batch trace writes for better performance

    while running:
        try:
            # Process tasks from queue (non-blocking with timeout)
            try:
                while True:
                    try:
                        task_data = task_queue.get(timeout=0.1)
                    except Exception:
                        break

                    task_type = task_data.get("task_type")
                    data = task_data.get("data", {})

                    if task_type == "shutdown":
                        print("[BackgroundWorker] Received shutdown signal")
                        running = False
                        break
                    elif task_type == "trace_write":
                        pending_traces.append(data)
                        # Flush batch when it reaches 10 items
                        if len(pending_traces) >= 10:
                            write_traces_batch(conn, pending_traces)
                            pending_traces.clear()
                    elif task_type == "trace_complete":
                        # Flush any pending traces before marking complete
                        if pending_traces:
                            write_traces_batch(conn, pending_traces)
                            pending_traces.clear()
                        mark_complete(conn, data)

            except Exception as e:
                print(f"[BackgroundWorker] Error processing queue: {e}")

            # Flush remaining batched traces after draining queue
            if pending_traces:
                write_traces_batch(conn, pending_traces)
                pending_traces.clear()

            if not running:
                break

            # Periodic flush check
            now = time()
            if now - last_flush_time >= poll_interval:
                last_flush_time = now

                # Retry failed traces
                try:
                    conn.execute(
                        """
                        UPDATE traces SET status = 'pending', error = NULL
                        WHERE status = 'failed' AND retry_count < ?
                    """,
                        (max_retries,),
                    )
                    conn.commit()
                except Exception:
                    pass

                # Fetch and flush pending traces
                try:
                    pending = fetch_pending(conn, limit=batch_size)

                    for request_id, rows in pending.items():
                        try:
                            conn.execute(
                                """
                                UPDATE traces SET status = 'flushing'
                                WHERE request_id = ? AND status = 'pending'
                            """,
                                (request_id,),
                            )
                            conn.commit()

                            flush_data = rebuild_flush_data(rows)
                            dispatch_flush(flush_data)

                            conn.execute(
                                """
                                UPDATE traces SET status = 'flushed', flushed_at = ?
                                WHERE request_id = ?
                            """,
                                (time(), request_id),
                            )
                            conn.commit()

                        except Exception as e:
                            error_msg = traceback.format_exc()
                            print(f"[BackgroundWorker] Error flushing {request_id}: {e}")
                            conn.execute(
                                """
                                UPDATE traces
                                SET status = 'failed', error = ?, retry_count = retry_count + 1
                                WHERE request_id = ?
                            """,
                                (error_msg, request_id),
                            )
                            conn.commit()

                except Exception as e:
                    print(f"[BackgroundWorker] Error in flush loop: {e}")

            # Small sleep to prevent busy loop
            sleep(0.05)

        except Exception as e:
            print(f"[BackgroundWorker] Unexpected error: {e}\n{traceback.format_exc()}")
            sleep(1)

    # Final flush before shutdown — ensure pending traces get sent
    try:
        pending = fetch_pending(conn, limit=batch_size)
        for request_id, rows in pending.items():
            try:
                conn.execute(
                    "UPDATE traces SET status = 'flushing' WHERE request_id = ? AND status = 'pending'",
                    (request_id,),
                )
                conn.commit()
                flush_data = rebuild_flush_data(rows)
                dispatch_flush(flush_data)
                conn.execute(
                    "UPDATE traces SET status = 'flushed', flushed_at = ? WHERE request_id = ?",
                    (time(), request_id),
                )
                conn.commit()
            except Exception as e:
                print(f"[BackgroundWorker] Error in final flush {request_id}: {e}")
    except Exception as e:
        print(f"[BackgroundWorker] Error in final flush: {e}")

    # Cleanup
    conn.close()
    print("[BackgroundWorker] Stopped")


def _background_worker(
    task_queue,
    db_path: str,
    config_path: Optional[str],
    dotenv_path: Optional[str],
    poll_interval: float = 2.0,
    batch_size: int = 10,
    max_retries: int = 3,
) -> None:
    """Background worker process target for multiprocessing.Process.

    This is a thin wrapper that sets up the environment and delegates
    to worker_loop().

    Args:
        task_queue: multiprocessing.Queue for receiving tasks
        db_path: Path to SQLite database
        config_path: Absolute path to resources.yaml
        dotenv_path: Absolute path to .env file
        poll_interval: Seconds between flush polls
        batch_size: Max requests per flush poll
        max_retries: Max retry attempts for failed traces
    """
    # Ignore SIGINT - let main process handle it
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    _setup_environment(config_path, dotenv_path)

    conn = init_db(Path(db_path))
    print(f"[BackgroundWorker] Started (multiprocessing), db={db_path}")

    worker_loop(task_queue, conn, poll_interval, batch_size, max_retries)


def _setup_environment(config_path: Optional[str], dotenv_path: Optional[str]) -> None:
    """Load .env and initialize ResourceHub."""
    if dotenv_path:
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path)
        except ImportError:
            pass

    if config_path:
        os.environ["HUSH_CONFIG"] = config_path

    try:
        from hush.core.registry import get_hub

        get_hub()
    except Exception as e:
        print(f"[BackgroundWorker] Failed to initialize ResourceHub: {e}")


def _stdin_reader(pipe, task_queue: queue.Queue) -> None:
    """Read JSON lines from stdin and put them into the task queue.

    Runs in a separate thread. When stdin is closed (parent died or shutdown),
    sends a SHUTDOWN task to the queue.

    Args:
        pipe: stdin pipe to read from
        task_queue: queue.Queue to put parsed tasks into
    """
    try:
        for line in pipe:
            line = line.strip()
            if not line:
                continue
            try:
                task_data = orjson.loads(line)
                task_queue.put(task_data)
            except orjson.JSONDecodeError as e:
                print(f"[BackgroundWorker] Invalid JSON from stdin: {e}")
    except Exception as e:
        print(f"[BackgroundWorker] Stdin reader error: {e}")
    finally:
        # Parent closed stdin or pipe broke — signal shutdown
        task_queue.put({"task_type": "shutdown", "data": {}})


def main() -> None:
    """CLI entry point for subprocess mode.

    Usage:
        python -m hush.core.background.worker --db-path /path/to/traces.db
    """
    parser = argparse.ArgumentParser(description="Hush background trace worker")
    parser.add_argument("--db-path", required=True, help="Path to SQLite database")
    parser.add_argument("--config-path", default="", help="Path to resources.yaml")
    parser.add_argument("--dotenv-path", default="", help="Path to .env file")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    # Ignore SIGINT - let parent process handle it
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    _setup_environment(
        config_path=args.config_path or None,
        dotenv_path=args.dotenv_path or None,
    )

    conn = init_db(Path(args.db_path))
    print(f"[BackgroundWorker] Started (subprocess), db={args.db_path}")

    # Start stdin reader thread
    task_queue: queue.Queue = queue.Queue()
    reader_thread = threading.Thread(
        target=_stdin_reader,
        args=(sys.stdin, task_queue),
        daemon=True,
    )
    reader_thread.start()

    # Run the main worker loop
    worker_loop(task_queue, conn, args.poll_interval, args.batch_size, args.max_retries)


if __name__ == "__main__":
    main()
