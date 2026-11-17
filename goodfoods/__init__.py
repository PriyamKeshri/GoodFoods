"""GoodFoods Concierge: a conversational reservation agent for a multi-location restaurant group.

Package layout
--------------
- ``goodfoods.core``        Domain logic (catalog, time parsing, inventory, recommendations, bookings).
- ``goodfoods.mcp_server``  The MCP server that exposes the domain as tools / resources / prompts.
- ``goodfoods.agent``       A from-scratch agent: MCP client, LLM client, tool loop and guardrails.
"""

__version__ = "1.0.0"
