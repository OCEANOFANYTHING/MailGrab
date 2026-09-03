"""
Integration tests for mailgrab_mcp_server.py: launches the real server as a
subprocess (exactly how Claude/Copilot/Cursor/Codex would) and drives it through
the official MCP client SDK -- no protocol details hand-rolled.

Each MailGrab crawl inside the server is itself a real subprocess hitting a local
mock HTTP server, so these tests need no network access for the crawl target, but
MailGrab.py's own startup connectivity check still does a real ping/HTTPS request
to oceanofanything.github.io (unrelated to and unmocked by these tests).

Run: python test_mailgrab_mcp.py
"""
import asyncio
import http.server
import json
import os
import threading
import time
import unittest

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import mailgrab_mcp_server

SERVER_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "mailgrab_mcp_server.py"))


class RecordingHandler(http.server.BaseHTTPRequestHandler):
    pages = {}

    def do_GET(self):
        body = self.pages.get(self.path, b"<html></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_server():
    httpServer = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
    threading.Thread(target=httpServer.serve_forever, daemon=True).start()
    return httpServer


async def call_tool(toolName, arguments):
    """Launches the real MCP server subprocess, calls one tool, returns its result."""
    params = StdioServerParameters(command="python", args=[SERVER_PATH])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(toolName, arguments)


async def list_tools():
    params = StdioServerParameters(command="python", args=[SERVER_PATH])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.list_tools()


def toolResultToDict(result):
    """MCP tool results carry their payload as a list of content blocks; the
    structured dict FastMCP returns shows up as a single text block of JSON."""
    text = result.content[0].text
    return json.loads(text)


class MCPServerTest(unittest.TestCase):
    def setUp(self):
        self.server = start_server()
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        RecordingHandler.pages = {}

    def test_tool_is_registered_with_expected_schema(self):
        result = asyncio.run(list_tools())
        names = [t.name for t in result.tools]
        self.assertIn("crawl_website", names)
        tool = next(t for t in result.tools if t.name == "crawl_website")
        self.assertIn("url", tool.inputSchema["properties"])
        self.assertIn("url", tool.inputSchema.get("required", []))
        # depth/same_domain etc. must NOT be required -- callers should be able to
        # invoke this with just a url and get sane defaults
        self.assertNotIn("depth", tool.inputSchema.get("required", []))

    def test_crawl_returns_structured_results(self):
        RecordingHandler.pages = {
            "/": b'<html><body>a@acme.test<a href="/contact">c</a></body></html>',
            "/contact": b"<html><body>contact@acme.test</body></html>",
        }
        result = asyncio.run(call_tool("crawl_website", {"url": self.base, "depth": 5}))
        self.assertFalse(result.isError, f"tool call reported an error: {result}")
        data = toolResultToDict(result)
        self.assertEqual(set(data["emails"]), {"a@acme.test", "contact@acme.test"})
        self.assertEqual(data["email_count"], 2)
        self.assertGreaterEqual(data["url_count"], 2)
        self.assertIn(self.base, data["sources"]["a@acme.test"])

    def test_same_domain_defaults_true_for_agent_safety(self):
        RecordingHandler.pages = {
            "/": (
                '<html><body>a@acme.test<a href="http://offsite.invalid/x">off</a></body></html>'
            ).encode(),
        }
        # No same_domain argument passed -- the MCP tool's own default (True) should apply
        result = asyncio.run(call_tool("crawl_website", {"url": self.base, "depth": 5}))
        self.assertFalse(result.isError)
        data = toolResultToDict(result)
        self.assertFalse(any("offsite.invalid" in u for u in data["scrapped_urls"]))

    def test_invalid_input_raises_a_clear_error(self):
        result = asyncio.run(call_tool(
            "crawl_website", {"url": self.base, "depth": 1, "proxy": "not-a-proxy-url"}))
        self.assertTrue(result.isError)
        message = result.content[0].text
        self.assertIn("proxy", message.lower())

    def test_concurrent_calls_do_not_collide(self):
        # Two different mock pages served from the SAME test server (different paths),
        # crawled concurrently -- verifies the per-call temp-directory isolation, since
        # both invocations would otherwise race on shared _results.json/_emails.txt files.
        RecordingHandler.pages = {
            "/site-a": b"<html><body>alpha@acme.test</body></html>",
            "/site-b": b"<html><body>beta@acme.test</body></html>",
        }

        async def both():
            return await asyncio.gather(
                call_tool("crawl_website", {"url": self.base + "/site-a", "depth": 1}),
                call_tool("crawl_website", {"url": self.base + "/site-b", "depth": 1}),
            )

        resultA, resultB = asyncio.run(both())
        self.assertFalse(resultA.isError)
        self.assertFalse(resultB.isError)
        dataA, dataB = toolResultToDict(resultA), toolResultToDict(resultB)
        self.assertEqual(dataA["emails"], ["alpha@acme.test"])
        self.assertEqual(dataB["emails"], ["beta@acme.test"])


class SubprocessTimeoutFormulaTest(unittest.TestCase):
    """A prior version computed this from `depth` alone (min(600, max(60, depth*2))),
    which ignored `concurrency`/`timeout` entirely -- confirmed via a real adversarial
    review to spuriously kill perfectly healthy crawls using a low concurrency and a
    higher per-request timeout (both legitimate, e.g. being polite to a slow site).
    These are fast unit tests of the pure formula, not a real 60s+ crawl, specifically
    so this regression stays cheap to check."""

    def test_scales_with_concurrency_and_timeout_not_just_depth(self):
        # The reviewer's exact repro: depth=5, concurrency=1, timeout=20 -- 5 sequential
        # rounds at up to 20s each need up to ~100s, which the old flat "depth*2=10s"
        # (floored to 60s) estimate did not accommodate.
        result = mailgrab_mcp_server._computeSubprocessTimeout(depth=5, concurrency=1, timeout=20)
        self.assertGreater(result, 100, "timeout budget does not scale with concurrency/timeout")

    def test_high_concurrency_keeps_timeout_small(self):
        # depth=100 with concurrency=100 is a single round -- should not balloon just
        # because depth is large. (1*(10+5)+30=45, floored up to the 60s minimum.)
        result = mailgrab_mcp_server._computeSubprocessTimeout(depth=100, concurrency=100, timeout=10)
        self.assertEqual(result, 60)

    def test_clamped_to_a_sane_floor_and_ceiling(self):
        self.assertEqual(
            mailgrab_mcp_server._computeSubprocessTimeout(depth=1, concurrency=10, timeout=1), 60)
        self.assertEqual(
            mailgrab_mcp_server._computeSubprocessTimeout(depth=500, concurrency=1, timeout=100), 1800)


if __name__ == "__main__":
    unittest.main()
