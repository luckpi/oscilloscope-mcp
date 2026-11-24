"""通过真实 MCP SDK stdio 子进程做握手/工具枚举/权限拦截测试（不接触仪器）。"""

import asyncio
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "scope_list_devices",
    "scope_info",
    "scope_get_setup",
    "scope_get_screen",
    "scope_read_waveform",
    "scope_configure_channel",
    "scope_configure_timebase",
    "scope_configure_edge_trigger",
    "scope_configure_acquisition",
    "scope_control_acquisition",
}
SCREEN_TOOL = "scope_get_screen"


def server_params(*extra):
    return StdioServerParameters(
        command=sys.executable, args=["-m", "oscilloscope_mcp", *extra]
    )


async def run_session(params, body):
    async def inner():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await body(session)

    return await asyncio.wait_for(inner(), timeout=15)


class StdioServerTest(unittest.TestCase):
    def test_handshake_and_tool_listing(self):
        async def body(session):
            tools = await session.list_tools()
            return {tool.name: tool for tool in tools.tools}

        tools = asyncio.run(run_session(server_params(), body))
        self.assertTrue(EXPECTED_TOOLS <= set(tools))
        # dict[str, Any] 返回类型让 SDK 生成结构化 outputSchema；Image 除外。
        for name in EXPECTED_TOOLS - {SCREEN_TOOL}:
            with self.subTest(tool=name):
                self.assertIsNotNone(tools[name].outputSchema)
        self.assertIsNone(tools[SCREEN_TOOL].outputSchema)

    def test_control_blocked_before_visa(self):
        async def body(session):
            return await session.call_tool(
                "scope_control_acquisition", {"action": "stop"}
            )

        result = asyncio.run(run_session(server_params(), body))
        self.assertTrue(result.isError)
        text = "".join(
            getattr(item, "text", "") for item in result.content
        )
        self.assertIn("allow-control", text)


if __name__ == "__main__":
    unittest.main()
