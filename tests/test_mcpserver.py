"""Libra's MCP server (libra/mcpserver.py): protocol handling, schema exposure,
running a skill, and that the trust gate is enforced over MCP."""
import io
import json
import os
import tempfile
import unittest


def _registry(policy=None, confirm=None):
    from libra.skills import default_registry

    return default_registry(log=None, load_default_dir=False, policy=policy, confirm=confirm)


class HandleTest(unittest.TestCase):
    def setUp(self):
        self.reg = _registry()

    def test_initialize_reports_server_info(self):
        from libra import mcpserver

        r = mcpserver.handle(self.reg, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "libra")
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertEqual(r["result"]["protocolVersion"], mcpserver.PROTOCOL_VERSION)

    def test_tools_list_has_schemas(self):
        from libra import mcpserver

        tools = {t["name"]: t for t in mcpserver.tool_list(self.reg)}
        self.assertIn("calc", tools)
        self.assertEqual(tools["calc"]["inputSchema"]["type"], "object")
        self.assertIn("expression", tools["calc"]["inputSchema"]["properties"])
        self.assertTrue(tools["calc"]["annotations"].get("readOnlyHint"))  # read skill -> hint

    def test_tools_call_runs_a_skill(self):
        from libra import mcpserver

        r = mcpserver.handle(self.reg, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                        "params": {"name": "calc", "arguments": {"expression": "6*7"}}})
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(r["result"]["content"][0]["text"], "6*7 = 42")

    def test_unknown_tool_is_method_not_found(self):
        from libra import mcpserver

        r = mcpserver.handle(self.reg, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                        "params": {"name": "nope", "arguments": {}}})
        self.assertEqual(r["error"]["code"], mcpserver.METHOD_NOT_FOUND)

    def test_unknown_method(self):
        from libra import mcpserver

        r = mcpserver.handle(self.reg, {"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
        self.assertEqual(r["error"]["code"], mcpserver.METHOD_NOT_FOUND)

    def test_notification_gets_no_reply(self):
        from libra import mcpserver

        self.assertIsNone(mcpserver.handle(self.reg, {"jsonrpc": "2.0", "method": "notifications/initialized"}))


class TrustGateTest(unittest.TestCase):
    def test_write_skill_is_refused_over_mcp(self):
        from libra import mcpserver
        from libra.skills import SkillResult
        from libra.trust import Policy

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("LIBRA_HOME", old) if old else os.environ.pop("LIBRA_HOME", None))

        reg = _registry(policy=Policy(), confirm=None)  # confirm=None -> "ask" means no

        @reg.skill("danger_tool", "Delete things", risk="danger")
        def danger_tool():
            return SkillResult("danger_tool", True, "done")

        r = mcpserver.handle(reg, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "danger_tool", "arguments": {}}})
        self.assertTrue(r["result"]["isError"])                        # not run
        self.assertIn("libra trust allow", r["result"]["content"][0]["text"])


class ServeLoopTest(unittest.TestCase):
    def test_serve_reads_lines_and_skips_blanks(self):
        from libra import mcpserver

        reg = _registry()
        inp = io.StringIO("\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            "",  # blank line is skipped, not an error
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),  # no reply
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "calc", "arguments": {"expression": "2+2"}}}),
        ]) + "\n")
        out = io.StringIO()
        mcpserver.serve(reg, inp=inp, out=out, log=lambda m: None)
        lines = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual([m.get("id") for m in lines], [1, 2])          # notification produced no line
        self.assertEqual(lines[1]["result"]["content"][0]["text"], "2+2 = 4")

    def test_bad_json_is_a_parse_error(self):
        from libra import mcpserver

        out = io.StringIO()
        mcpserver.serve(_registry(), inp=io.StringIO("not json\n"), out=out, log=lambda m: None)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], mcpserver.PARSE_ERROR)


class CliAddParsingTest(unittest.TestCase):
    """`libra mcp add NAME --url …` must parse: a REMAINDER command arg used to swallow --url/--header."""

    def _parse(self, line):
        import shlex

        from libra.cli import build_parser

        return build_parser().parse_args(shlex.split(line))

    def test_url_flag_after_name(self):
        a = self._parse("mcp add composio --url https://connect.composio.dev/mcp --header 'x-consumer-api-key: ${K}'")
        self.assertEqual(a.action, "add")
        self.assertEqual(a.name, "composio")
        self.assertEqual(a.url, "https://connect.composio.dev/mcp")
        self.assertEqual(a.header, ["x-consumer-api-key: ${K}"])
        self.assertEqual(a.command, [])

    def test_url_flag_before_name(self):
        a = self._parse("mcp add --url https://x/mcp remote")
        self.assertEqual((a.action, a.name, a.url), ("add", "remote", "https://x/mcp"))

    def test_stdio_command_after_dashdash(self):
        a = self._parse("mcp add files -- npx -y @modelcontextprotocol/server-filesystem /home/me")
        self.assertEqual(a.name, "files")
        self.assertIsNone(a.url)
        self.assertEqual(a.command, ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/home/me"])

    def test_serve_action(self):
        self.assertEqual(self._parse("mcp serve").action, "serve")


if __name__ == "__main__":
    unittest.main()
