"""Server-side chat agent: an LLM loop over the MCP tool handlers.

Talks to any OpenAI-compatible chat-completions API (Ollama, OpenAI,
Anthropic compat, OpenRouter) and dispatches tool calls in-process via
``mcp_tools._HANDLERS`` — no MCP transport involved.  Write tools pause
the stream for user confirmation.
"""
