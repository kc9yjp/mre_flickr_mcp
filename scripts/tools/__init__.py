"""MCP tool modules, grouped by domain (photos, albums, groups, contacts, galleries, sync).

Each module exports a ``TOOLS`` list (``mcp.types.Tool`` definitions) and a
``HANDLERS`` dict (name -> async handler). ``mcp_tools.py`` aggregates both
across all modules in this package.
"""
