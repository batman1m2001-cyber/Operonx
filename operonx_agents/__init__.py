"""operonx-agents: typed LLM steps and agents on the operonx workflow engine.

Two front doors over one model layer:

- :func:`llm_step` — a typed, deadline-bounded model call as a graph op,
  for workflows where the graph owns control flow.
- :class:`Agent` + :class:`Runner` — a model-driven tool loop, as plain
  async code inside one op, with every turn, model call and tool call a
  child execution in the trace.
"""

from operonx_agents.agent import Agent
from operonx_agents.compose import AgentOp, agent_op, agent_tool
from operonx_agents.context import (
    ContextPolicy,
    InMemorySession,
    RedisSession,
    Session,
    SQLiteSession,
)
from operonx_agents.errors import (
    AgentsError,
    Interrupted,
    ModelError,
    ModelRefused,
    ModelRetry,
    ModelTimeout,
    OutputInvalid,
    ToolDefinitionError,
    Tripwire,
)
from operonx_agents.model import (
    Choice,
    Model,
    ModelResponse,
    ModelSettings,
    OutputResult,
    Reasoning,
    Usage,
    ask,
)
from operonx_agents.run import (
    ApprovalRequired,
    Approve,
    Compacted,
    Deny,
    Event,
    InMemoryStateStore,
    Interruption,
    ReasoningDelta,
    RedisStateStore,
    RunContext,
    RunFinished,
    RunResult,
    RunStarted,
    RunState,
    SQLiteStateStore,
    StateStore,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
    UsageLimits,
)
from operonx_agents.run.runner import Runner
from operonx_agents.safety import (
    Ask,
    Hooks,
    ModelRequest,
    Redactor,
    RedactToolOutput,
    ToolCall,
)
from operonx_agents.step import LLMStepOp, llm_step
from operonx_agents.tools import (
    DEFAULT_POLICY,
    MCPClient,
    MCPError,
    MCPServer,
    MCPToolset,
    Tool,
    ToolPolicy,
    Toolset,
    ToolSpec,
    dispatch,
    tool,
    tool_message,
)

__version__ = "0.1.0"


def __getattr__(name: str):
    """``agent_service`` and the evaluators load on first use: an agent
    that is never served or evaluated does not import operonx's serve and
    eval layers (track3 §4.1, cheap when unused)."""
    if name == "agent_service":
        from operonx_agents.serve import agent_service

        return agent_service
    if name in ("evals",):
        import importlib

        return importlib.import_module(f"operonx_agents.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Agent",
    "AgentOp",
    "AgentsError",
    "ApprovalRequired",
    "Approve",
    "Ask",
    "Choice",
    "Compacted",
    "ContextPolicy",
    "DEFAULT_POLICY",
    "Deny",
    "Event",
    "Hooks",
    "InMemorySession",
    "InMemoryStateStore",
    "Interrupted",
    "Interruption",
    "LLMStepOp",
    "MCPClient",
    "MCPError",
    "MCPServer",
    "MCPToolset",
    "Model",
    "ModelError",
    "ModelRefused",
    "ModelRequest",
    "ModelResponse",
    "ModelRetry",
    "ModelSettings",
    "ModelTimeout",
    "OutputInvalid",
    "OutputResult",
    "Reasoning",
    "ReasoningDelta",
    "RedactToolOutput",
    "Redactor",
    "RedisSession",
    "RedisStateStore",
    "RunContext",
    "RunFinished",
    "RunResult",
    "RunStarted",
    "RunState",
    "Runner",
    "SQLiteSession",
    "SQLiteStateStore",
    "Session",
    "StateStore",
    "TextDelta",
    "Tool",
    "ToolCall",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolDefinitionError",
    "ToolPolicy",
    "ToolSpec",
    "Toolset",
    "Tripwire",
    "TurnFinished",
    "TurnStarted",
    "Usage",
    "UsageLimits",
    "agent_op",
    "agent_service",
    "agent_tool",
    "ask",
    "dispatch",
    "llm_step",
    "tool",
    "tool_message",
]
