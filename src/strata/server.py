"""Two read tools on the official MCP SDK's stdio transport."""
from __future__ import annotations

import asyncio
import logging
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from strata.project import Project


def create_server(project: Project) -> Server:
    server = Server('strata', version='0.0.1')
    string = {'type': 'string'}
    date = {'type': 'string', 'pattern': r'^\d{4}(-\d{2}(-\d{2})?)?$'}
    annotations = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    tools = [
        types.Tool(name='search', description='Search or browse the current archive. Follow continuations to exhaust evidence; queries are relevance-limited.',
                   inputSchema={'type': 'object', 'additionalProperties': False,
                                'properties': {'query': string, 'from': date, 'to': date, 'who': string,
                                               'kind': {'enum': ['source', 'note', 'manuscript']}, 'cursor': string}},
                   annotations=annotations),
        types.Tool(name='read', description='Read exact cited source text or a live note/manuscript. Follow continuations, including metadata-only pages.',
                   inputSchema={'type': 'object', 'additionalProperties': False,
                                'properties': {'ref': string, 'cursor': string}}, annotations=annotations),
    ]

    @server.list_tools()
    async def list_tools():
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name not in ('search', 'read'):
            raise ValueError('unknown tool')
        arguments = dict(arguments)
        if name == 'search' and 'from' in arguments:
            arguments['from_'] = arguments.pop('from')
        def execute():
            reply = getattr(project, name)(**arguments)
            # Search already includes indexing status. Read needs it as well.
            return ('indexing: complete\n' if name == 'read' else '') + reply.text()
        text = await asyncio.to_thread(execute)
        return [types.TextContent(type='text', text=text)]

    return server


def run(project: Project):
    logging.basicConfig(level=logging.WARNING)
    server = create_server(project)
    async def serve():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    asyncio.run(serve())
