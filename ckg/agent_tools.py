"""Dependency-free tool adapter for AI agents.

The functions here intentionally use plain dictionaries so they can be wired
to MCP, OpenAI function calling, a local agent, or an IDE extension without
coupling the graph project to one provider SDK.
"""

from __future__ import annotations

from .retriever import GraphRetriever
from .store import GraphStore


TOOL_DEFINITIONS = [
    {
        "name": "search_code_symbols",
        "description": "Find C/C++ functions, types, variables, macros, or files by name.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "entity_types": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_code_context",
        "description": "Return bounded graph context and exact source ranges around a symbol.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "depth": {"type": "integer", "minimum": 0, "maximum": 4},
                "max_files": {"type": "integer", "minimum": 1, "maximum": 30},
                "max_lines": {"type": "integer", "minimum": 20, "maximum": 3000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "analyze_change_impact",
        "description": "Estimate affected symbols, files, callers, callees, and change risk.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 4},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["symbol"],
        },
    },
]


def dispatch_tool(name: str, arguments: dict, *, graph_dir: str) -> dict:
    """Execute one provider-neutral AI tool against a graph output directory."""
    store = GraphStore(graph_dir)
    retriever = GraphRetriever(store)
    if name == "search_code_symbols":
        return {
            "query": arguments["query"],
            "matches": retriever.resolve(
                arguments["query"],
                entity_types=arguments.get("entity_types"),
                limit=int(arguments.get("limit", 10)),
            ),
        }
    if name == "get_code_context":
        return retriever.context(
            arguments["symbol"],
            depth=int(arguments.get("depth", 1)),
            max_files=int(arguments.get("max_files", 12)),
            max_lines=int(arguments.get("max_lines", 500)),
            limit=int(arguments.get("limit", 5)),
        )
    if name == "analyze_change_impact":
        return retriever.impact(
            arguments["symbol"],
            depth=int(arguments.get("depth", 2)),
            limit=int(arguments.get("limit", 5)),
        )
    raise ValueError(f"unknown code-kg tool: {name}")

