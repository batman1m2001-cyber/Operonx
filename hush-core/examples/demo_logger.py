#!/usr/bin/env python3
"""Demo script to showcase all logging patterns used in Hush Core.

This file contains examples of every LOGGER call pattern used across the codebase,
organized by module for easy testing and verification.
"""

from hush.core.loggings import LOGGER, LogConfig, setup_logger, format_log_data


def demo_all_project_logs():
    """Demo all actual logging patterns from the project."""
    config = LogConfig(
        name="demo.all",
        level="DEBUG",
        handlers=[
            {"type": "console", "level": "DEBUG"},
        ]
    )
    logger = setup_logger(config)

    # Sample data for demos
    request_id = "abc12345"
    session_id = "session-001"
    channel_name = "output"
    context_id = "main"
    workflow_name = "my-workflow"
    node_name = "processor"
    full_name = "my-workflow.processor"

    print("\n" + "="*60)
    print("ENGINE.PY - Workflow Execution")
    print("="*60 + "\n")

    # engine.py:87 - Engine initialization (DEBUG)
    logger.debug("Hush engine initialized for workflow [highlight]%s[/highlight]", workflow_name)

    # engine.py:124 - Workflow start (INFO)
    logger.info("[title]\\[%s][/title] Running workflow [highlight]%s[/highlight]", request_id, workflow_name)

    # engine.py:144 - Workflow complete (INFO)
    logger.info("[title]\\[%s][/title] Workflow [highlight]%s[/highlight] completed", request_id, workflow_name)

    print("\n" + "="*60)
    print("NODES/BASE.PY - Base Node Execution")
    print("="*60 + "\n")

    # base.py:457-461 - Node execution log (INFO)
    inputs = {"query": "hello", "max_tokens": 100}
    outputs = {"response": "Hi there!", "tokens_used": 15}
    duration_ms = 123.45
    logger.info(
        "[title]\\[%s][/title] [bold]%s[/bold]: [highlight]%s[/highlight] [muted]\\[%s][/muted] [muted](%.1fms)[/muted] %s -> %s",
        request_id, "CODE", full_name, context_id,
        duration_ms, format_log_data(inputs), format_log_data(outputs)
    )

    # base.py:490-491 - Node error (ERROR)
    error_str = "ValueError: Invalid input"
    logger.error("[title]\\[%s][/title] Error in node [highlight]%s[/highlight]: %s", request_id, node_name, error_str)
    logger.error("Traceback (most recent call last):\n  File \"base.py\", line 482\n    raise ValueError(\"Invalid input\")")

    print("\n" + "="*60)
    print("NODES/GRAPH/GRAPH_NODE.PY - Graph Execution")
    print("="*60 + "\n")

    # graph_node.py:80 - Endpoint setup (DEBUG)
    logger.debug("Graph [highlight]%s[/highlight]: đang khởi tạo endpoints...", workflow_name)

    # graph_node.py:89 - No entry node (ERROR)
    logger.error("Graph [highlight]%s[/highlight]: không tìm thấy entry node. Kiểm tra kết nối START >> node.", workflow_name)

    # graph_node.py:92 - No exit node (ERROR)
    logger.error("Graph [highlight]%s[/highlight]: không tìm thấy exit node. Kiểm tra kết nối node >> END.", workflow_name)

    # graph_node.py:101 - Schema creation (DEBUG)
    logger.debug("Graph [highlight]%s[/highlight]: đang tạo schema...", workflow_name)

    # graph_node.py:136 - Flow type detection (DEBUG)
    logger.debug("Graph [highlight]%s[/highlight]: đang xác định flow type của các node...", workflow_name)

    # graph_node.py:171-174 - Orphan node warning (WARNING)
    orphan_nodes = ["unused_node_1", "unused_node_2"]
    logger.warning(
        "Graph [highlight]%s[/highlight]: phát hiện orphan node [muted](không có edge)[/muted]: %s. Các node này sẽ không bao giờ được thực thi.",
        full_name, orphan_nodes
    )

    # graph_node.py:230-233 - Node override warning (WARNING)
    logger.warning(
        "Graph [highlight]%s[/highlight]: node [highlight]%s[/highlight] đã tồn tại và sẽ bị ghi đè",
        workflow_name, node_name
    )

    # graph_node.py:405-406 - Graph error (ERROR)
    logger.error("[title]\\[%s][/title] Error in node [highlight]%s[/highlight]: %s", request_id, workflow_name, "Execution failed")

    print("\n" + "="*60)
    print("NODES/ITERATION/ASYNC_ITER_NODE.PY - Async Iteration")
    print("="*60 + "\n")

    # async_iter_node.py:228 - Chunk processing error (ERROR)
    chunk_id = 5
    logger.error("[title]\\[%s][/title] Error processing chunk [value]%s[/value]: %s", request_id, chunk_id, "Connection timeout")

    # async_iter_node.py:262 - Source is None (WARNING)
    logger.warning("[title]\\[%s][/title] AsyncIterNode [highlight]%s[/highlight]: source is None.", request_id, full_name)

    # async_iter_node.py:357 - Callback error (ERROR)
    logger.error("[title]\\[%s][/title] Callback error: %s", request_id, "Handler raised exception")

    # async_iter_node.py:380-383 - High error rate (WARNING)
    error_count, total_chunks = 15, 100
    logger.warning(
        "[title]\\[%s][/title] AsyncIterNode [highlight]%s[/highlight]: high error rate [muted](%s)[/muted]. %s/%s failed.",
        request_id, full_name, f"{error_count / total_chunks:.1%}", error_count, total_chunks
    )

    # async_iter_node.py:394-397 - High callback error rate (WARNING)
    handler_error_count, handler_count = 3, 50
    logger.warning(
        "[title]\\[%s][/title] AsyncIterNode [highlight]%s[/highlight]: high callback error rate [muted](%s)[/muted].",
        request_id, full_name, f"{handler_error_count / handler_count:.1%}"
    )

    # async_iter_node.py:411-412 - Node error (ERROR)
    logger.error("[title]\\[%s][/title] Error in node [highlight]%s[/highlight]: %s", request_id, full_name, "Async iteration failed")

    print("\n" + "="*60)
    print("NODES/ITERATION/FOR_LOOP_NODE.PY - Sequential Loop")
    print("="*60 + "\n")

    # for_loop_node.py:191-194 - Length mismatch (ERROR)
    lengths = {"x": 5, "y": 3}
    logger.error(
        "ForLoopNode [highlight]%s[/highlight]: 'each' variables have different lengths: %s. All 'each' variables must have the same length.",
        full_name, lengths
    )

    # for_loop_node.py:237-240 - No iteration data (WARNING)
    logger.warning(
        "[title]\\[%s][/title] ForLoopNode [highlight]%s[/highlight]: no iteration data. No iterations will be executed.",
        request_id, full_name
    )

    # for_loop_node.py:273-276 - High error rate (WARNING)
    iteration_count = 100
    logger.warning(
        "[title]\\[%s][/title] ForLoopNode [highlight]%s[/highlight]: high error rate [muted](%s)[/muted]. %s/%s iterations failed.",
        request_id, full_name, f"{error_count / iteration_count:.1%}", error_count, iteration_count
    )

    # for_loop_node.py:290-291 - Node error (ERROR)
    logger.error("[title]\\[%s][/title] Error in node [highlight]%s[/highlight]: %s", request_id, full_name, "Loop execution failed")

    print("\n" + "="*60)
    print("NODES/ITERATION/WHILE_LOOP_NODE.PY - While Loop")
    print("="*60 + "\n")

    # while_loop_node.py:65 - Invalid condition syntax (ERROR)
    condition = "counter >== 5"
    logger.error("Invalid stop_condition syntax [str]'%s'[/str]: %s", condition, "invalid syntax")

    # while_loop_node.py:81 - Condition evaluation error (ERROR)
    logger.error("Error evaluating stop_condition [str]'%s'[/str]: %s", "counter >= max", "name 'max' is not defined")

    # while_loop_node.py:168-172 - Max iterations reached (WARNING)
    max_iterations = 100
    stop_condition = "done == True"
    logger.warning(
        "[title]\\[%s][/title] WhileLoopNode [highlight]%s[/highlight]: max_iterations [muted](%s)[/muted] reached. "
        "Condition [str]'%s'[/str] never evaluated to True. This may indicate an infinite loop or incorrect stop condition.",
        request_id, full_name, max_iterations, stop_condition
    )

    # while_loop_node.py:191-192 - Node error (ERROR)
    logger.error("[title]\\[%s][/title] Error in node [highlight]%s[/highlight]: %s", request_id, full_name, "While loop failed")

    print("\n" + "="*60)
    print("NODES/ITERATION/MAP_NODE.PY - Parallel Map")
    print("="*60 + "\n")

    # map_node.py:197-200 - Length mismatch (ERROR)
    logger.error(
        "MapNode [highlight]%s[/highlight]: 'each' variables have different lengths: %s",
        full_name, lengths
    )

    # map_node.py:243-246 - No iteration data (WARNING)
    logger.warning(
        "[title]\\[%s][/title] MapNode [highlight]%s[/highlight]: no iteration data. No iterations will be executed.",
        request_id, full_name
    )

    # map_node.py:288-291 - High error rate (WARNING)
    logger.warning(
        "[title]\\[%s][/title] MapNode [highlight]%s[/highlight]: high error rate [muted](%s)[/muted]. %s/%s iterations failed.",
        request_id, full_name, f"{error_count / iteration_count:.1%}", error_count, iteration_count
    )

    # map_node.py:305-306 - Node error (ERROR)
    logger.error("[title]\\[%s][/title] Error in node [highlight]%s[/highlight]: %s", request_id, full_name, "Map execution failed")

    print("\n" + "="*60)
    print("NODES/FLOW/BRANCH_NODE.PY - Branching")
    print("="*60 + "\n")

    # branch_node.py:126 - Invalid condition syntax (ERROR)
    logger.error("Cú pháp điều kiện không hợp lệ [str]'%s'[/str]: %s", "score >> 90", "invalid syntax")

    # branch_node.py:153 - Condition matched (DEBUG)
    logger.debug("Điều kiện [str]'%s'[/str] khớp, định tuyến đến [highlight]%s[/highlight]", "score >= 90", "excellent_path")

    # branch_node.py:157 - Condition evaluation error (ERROR)
    logger.error("Lỗi khi đánh giá điều kiện [str]'%s'[/str]: %s", "score >= threshold", "name 'threshold' is not defined")

    # branch_node.py:170 - Ref condition matched (DEBUG)
    logger.debug("Điều kiện [str]'%s'[/str] khớp, định tuyến đến [highlight]%s[/highlight]", "ref:is_valid", "success_path")

    # branch_node.py:174 - Ref condition error (ERROR)
    logger.error("Lỗi khi đánh giá ref condition cho [str]'%s'[/str]: %s", "is_valid", "TypeError: unsupported operand")

    # branch_node.py:178 - Using default target (DEBUG)
    logger.debug("Không có điều kiện khớp, sử dụng target mặc định [highlight]%s[/highlight]", "fallback_path")

    # branch_node.py:181 - No condition matched, no default (WARNING)
    logger.warning("Không có điều kiện khớp và không có target mặc định")

    print("\n" + "="*60)
    print("STREAMS/MEMORY.PY - In-Memory Streaming")
    print("="*60 + "\n")

    # memory.py:25 - Service initialized (DEBUG)
    logger.debug("[highlight]InMemoryStreamService[/highlight] initialized")

    # memory.py:57 - Push error (ERROR)
    logger.error("[title]\\[%s][/title] Error pushing data to [muted]%s/%s[/muted]: %s", request_id, session_id, channel_name, "Queue full")

    # memory.py:80 - END signal pushed (DEBUG)
    logger.debug("[title]\\[%s][/title] Pushed END signal to [muted]%s/%s[/muted]", request_id, session_id, channel_name)

    # memory.py:83 - END signal error (ERROR)
    logger.error("[title]\\[%s][/title] Error pushing END signal to [muted]%s/%s[/muted]: %s", request_id, session_id, channel_name, "Connection lost")

    # memory.py:139-142 - Max idle time exceeded (DEBUG)
    max_idle_time = 30.0
    logger.debug(
        "[title]\\[%s][/title] Max idle time [muted](%ss)[/muted] exceeded for [muted]%s/%s[/muted], stopping",
        request_id, max_idle_time, session_id, channel_name
    )

    # memory.py:147 - Failed to process data (WARNING)
    logger.warning("[title]\\[%s][/title] Failed to process data from [muted]%s/%s[/muted]: %s", request_id, session_id, channel_name, "Deserialization error")

    # memory.py:151 - Consume error (ERROR)
    logger.error("[title]\\[%s][/title] Error consuming from [muted]%s/%s[/muted]: %s", request_id, session_id, channel_name, "Queue closed")

    # memory.py:187 - END signal to all channels (DEBUG)
    logger.debug("[title]\\[%s][/title] Sent END signal to all channels for session [muted]%s[/muted]", request_id, session_id)

    # memory.py:190 - End request error (ERROR)
    logger.error("[title]\\[%s][/title] Error ending request for session [muted]%s[/muted]: %s", request_id, session_id, "Session not found")

    # memory.py:218 - Service closed (DEBUG)
    logger.debug("[highlight]InMemoryStreamService[/highlight] closed")

    # memory.py:220 - Close error (ERROR)
    logger.error("Error closing [highlight]InMemoryStreamService[/highlight]: %s", "Resources in use")

    print("\n" + "="*60)
    print("TRACERS/BASE.PY - Tracing")
    print("="*60 + "\n")

    # tracers/base.py:255-260 - Flush task submission failed (ERROR)
    logger.error(
        "[title]\\[%s][/title] Workflow [highlight]%s[/highlight]: Failed to submit flush task: %s",
        request_id, workflow_name, "Queue is full"
    )

    # tracers/base.py:333 - Unknown tracer type (ERROR)
    logger.error("Unknown tracer type: [highlight]%s[/highlight]", "CustomTracer")

    print("\n" + "="*60)
    print("REGISTRY/RESOURCE_HUB.PY - Resource Management")
    print("="*60 + "\n")

    key = "llm:gpt-4"

    # resource_hub.py:60 - Hub initialization error (ERROR)
    logger.error(f"Không thể khởi tạo global hub: FileNotFoundError")

    # resource_hub.py:210 - Missing _class field (WARNING)
    logger.warning(f"Thiếu field '_class' cho key: {key}")

    # resource_hub.py:215 - Unknown config class (WARNING)
    logger.warning(f"Config class không xác định: UnknownConfig")

    # resource_hub.py:225 - Config parse error (ERROR)
    logger.error(f"Không thể parse config '{key}': ValidationError")

    # resource_hub.py:265 - Config parse error in load_all (ERROR)
    logger.error(f"Không thể parse config '{key}': Invalid field 'model'")

    # resource_hub.py:303 - Lazy load (DEBUG)
    logger.debug(f"Đã lazy load resource: {key}")

    # resource_hub.py:350 - Register (DEBUG)
    logger.debug(f"Đã đăng ký: {key}")

    # resource_hub.py:373 - Remove (DEBUG)
    logger.debug(f"Đã xóa: {key}")

    # resource_hub.py:383 - Clear all (DEBUG)
    logger.debug("Đã xóa tất cả resource")

    print("\n" + "="*60)
    print("REGISTRY/RESOURCE_FACTORY.PY - Factory")
    print("="*60 + "\n")

    # resource_factory.py:31 - Config class registered (DEBUG)
    logger.debug(f"Đã đăng ký config class: OpenAIConfig")

    # resource_factory.py:67 - Factory handler registered (DEBUG)
    logger.debug(f"Đã đăng ký factory handler cho: LLMConfig")

    # resource_factory.py:131 - Resource creation error (ERROR)
    logger.error(f"Không thể tạo resource cho OpenAIConfig: API key not found")

    print("\n" + "="*60)
    print("REGISTRY/STORAGE/JSON.PY & YAML.PY - Storage")
    print("="*60 + "\n")

    # json.py:63,87 / yaml.py:61,85 - Missing _class (WARNING)
    logger.warning(f"Thiếu field '_class' cho key: {key}")

    # json.py:69 / yaml.py:67 - Load error (ERROR)
    logger.error(f"Không thể load config '{key}': FileNotFoundError")

    # json.py:79 - Invalid JSON (ERROR)
    logger.error(f"File JSON không hợp lệ: Expecting property name")

    # yaml.py:77 - Invalid YAML (ERROR)
    logger.error(f"File YAML không hợp lệ: mapping values are not allowed here")

    # json.py:100 / yaml.py:98 - Config saved (DEBUG)
    logger.debug(f"Đã lưu config: {key}")

    # json.py:103 / yaml.py:101 - Save error (ERROR)
    logger.error(f"Không thể lưu config '{key}': PermissionError")

    # json.py:113 / yaml.py:111 - Config deleted (DEBUG)
    logger.debug(f"Đã xóa config: {key}")

    # json.py:117 / yaml.py:115 - Delete error (ERROR)
    logger.error(f"Không thể xóa config '{key}': IOError")


def demo_log_levels():
    """Demo log levels hierarchy."""
    config = LogConfig(
        name="demo.levels",
        level="DEBUG",
        handlers=[
            {"type": "console", "level": "DEBUG"},
        ]
    )
    logger = setup_logger(config)

    print("\n" + "="*60)
    print("LOG LEVELS HIERARCHY")
    print("="*60 + "\n")

    logger.debug("DEBUG - Internal tracing, detailed diagnostics")
    logger.info("INFO - Normal operation events, workflow progress")
    logger.warning("WARNING - Something needs attention but not critical")
    logger.error("ERROR - Something went wrong, operation failed")
    logger.critical("CRITICAL - System failure, immediate action required")


def demo_format_log_data():
    """Demo the format_log_data utility."""
    config = LogConfig(
        name="demo.format",
        level="DEBUG",
        handlers=[
            {"type": "console", "level": "DEBUG"},
        ]
    )
    logger = setup_logger(config)

    print("\n" + "="*60)
    print("FORMAT_LOG_DATA UTILITY")
    print("="*60 + "\n")

    # Simple dict
    data1 = {"name": "John", "age": 30}
    logger.info("Simple dict: %s", format_log_data(data1))

    # Nested dict
    data2 = {"user": {"name": "John", "roles": ["admin", "user"]}, "active": True}
    logger.info("Nested dict: %s", format_log_data(data2))

    # Long string (truncated)
    data3 = {"content": "A" * 500}
    logger.info("Long string: %s", format_log_data(data3))

    # Large list (summarized)
    data4 = {"items": list(range(100))}
    logger.info("Large list: %s", format_log_data(data4))

    # Mixed types
    data5 = {"count": 42, "ratio": 3.14, "enabled": True, "name": "test"}
    logger.info("Mixed types: %s", format_log_data(data5))


if __name__ == "__main__":
    print("\n" + "="*60)
    print("HUSH CORE - LOGGING PATTERNS DEMO")
    print("="*60)

    demo_log_levels()
    demo_format_log_data()
    demo_all_project_logs()

    print("\n" + "="*60)
    print("DEMO COMPLETE")
    print("="*60 + "\n")
