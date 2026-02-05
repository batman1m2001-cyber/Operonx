# Hush Observability Examples

This directory contains examples demonstrating how to use the hush-observability package.

## Examples

### langfuse_basic.py

Basic example showing:
- Auto-registration of TracerPlugin with ResourceHub
- Creating and configuring Langfuse tracer
- Adding traces with hierarchical relationships (workflow → generation → event)
- Flushing traces to Langfuse backend

**Setup:**

```bash
# Install hush-observability with Langfuse support
pip install -e ".[langfuse]"

# Set environment variables
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_HOST="https://cloud.langfuse.com"

# Run example
python examples/langfuse_basic.py
```

## Adding New Backend Examples

To add examples for other backends (Phoenix, Opik, LangSmith):

1. Implement the backend tracer in `hush/observability/tracers/`
2. Create corresponding example in this directory
3. Update this README

See the implementation plan in the architecture docs for details.
