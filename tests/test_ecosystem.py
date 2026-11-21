"""Catalog, MCP client, OpenAPI importer and trust controls."""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_MCP = os.path.join(HERE, "fixtures", "fake_mcp_server.py")

PETSTORE_YAML = """openapi: 3.0.3
info:
  title: Pet Store
  version: "1.0"
paths:
  /pets:
    get:
      operationId: listPets
      summary: List all pets
      parameters:
        - name: limit
          in: query
          schema: {type: integer}
    post:
      operationId: createPet
      summary: Create a pet
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/NewPet'
  /pets/{petId}:
    parameters:
      - name: petId
        in: path
        required: true
        schema:
          type: string
    get:
      operationId: getPetById
      summary: Info for a specific pet
    delete:
      operationId: deletePet
      summary: Delete a pet
components:
  schemas:
    NewPet:
      type: object
      required: [name]
      properties:
        name: {type: string}
        tag: {type: string}
"""

SWAGGER2 = {"swagger": "2.0", "info": {"title": "Old API"}, "host": "old.example.com", "basePath": "/v2",
            "schemes": ["https"],
            "paths": {"/orders": {"post": {"operationId": "placeOrder", "parameters": [
                {"in": "body", "name": "body", "required": True, "schema": {"$ref": "#/definitions/Order"}}]}}},
            "definitions": {"Order": {"type": "object", "properties": {"qty": {"type": "integer"}}}}}


class Echo(BaseHTTPRequestHandler):
    """Replies with what it received, as JSON; also a minimal Streamable-HTTP MCP endpoint at /mcp."""

    def log_message(self, *a):
        pass

    def _reply(self, status, obj, headers=None, sse=False):
        data = json.dumps(obj).encode()
        if sse:
            data = b"event: message\ndata: " + data + b"\n\n"
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if self.path == "/mcp":
            msg = json.loads(body)
            if "id" not in msg:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if msg["method"] == "initialize":
                return self._reply(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "http-fake"}}},
                    {"Mcp-Session-Id": "s123"})
            assert self.headers.get("Mcp-Session-Id") == "s123", "session id not sent"
            if msg["method"] == "tools/list":
                return self._reply(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                    {"name": "whoami", "description": "Show the caller", "inputSchema": {"type": "object"}}]}})
            return self._reply(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": "auth=" + self.headers.get("Authorization", "")}]}}, sse=True)
        status = 404 if "missing" in self.path else 200
        self._reply(status, {"method": self.command, "path": self.path, "body": json.loads(body) if body else None,
                             "auth": self.headers.get("Authorization"), "key": self.headers.get("X-Key")})

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = handle_any


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: os.environ.get(k) for k in ("LIBRA_HOME", "LIBRA_ALLOW_PRIVATE", "PET_TOKEN", "MCP_TOKEN")}
        os.environ["LIBRA_HOME"] = self.tmp.name
        os.environ["LIBRA_ALLOW_PRIVATE"] = "1"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        from libra import mcp

        mcp.close_all()
        self.server.shutdown()
        self.server.server_close()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def cli(self, *argv):
        from libra.cli import main

        out = io.StringIO()
        with redirect_stdout(out):
            main(list(argv))
        return out.getvalue()


class CatalogTest(Base):
    def test_search_replace_remove(self):
        from libra.catalog import Catalog, Entry, coerce_by_schema

        c = Catalog()
        c.add_many([Entry("api.shop.create_order", "Create an order for a customer", "openapi", "api.shop", "write"),
                    Entry("api.shop.list_orders", "List orders", "openapi", "api.shop"),
                    Entry("api.weather.get_forecast", "Weather forecast for a city", "openapi", "api.weather")])
        self.assertEqual(c.count(), 3)
        self.assertEqual(c.search("weather in paris")[0][1].name, "api.weather.get_forecast")
        self.assertEqual(c.search("createOrder")[0][1].name, "api.shop.create_order")
        self.assertEqual(c.search("api.shop.list_orders")[0][1].name, "api.shop.list_orders")
        self.assertEqual([e.name for _, e in c.search("order", source="api.weather")], [])
        c.add_many([Entry("api.shop.refund", "Refund an order", "openapi", "api.shop", "danger")],
                   replace_source="api.shop")
        self.assertEqual(c.count(), 2)
        self.assertEqual(c.search("refund")[0][1].risk, "danger")
        self.assertEqual(c.remove_source("api"), 2)
        self.assertEqual((c.count(), c.search("weather")), (0, []))
        schema = {"type": "object", "properties": {"n": {"type": "integer"}, "f": {"type": "boolean"},
                                                   "tags": {"type": "array"}}, "required": ["n"]}
        self.assertEqual(coerce_by_schema({"n": "3", "f": "yes", "tags": '["a"]'}, schema),
                         {"n": 3, "f": True, "tags": ["a"]})
        with self.assertRaises(TypeError):
            coerce_by_schema({}, schema)
        with self.assertRaises(TypeError):
            coerce_by_schema({"n": "1", "bogus": "x"}, schema)


class MCPTest(Base):
    def test_stdio_server(self):
        from libra import mcp
        from libra.skills import default_registry
        from libra.trust import Policy

        mcp.save_config({"fake": {"command": sys.executable, "args": [FAKE_MCP]}})
        self.assertEqual(mcp.sync("fake", log=None), {"fake": 3})  # both pages listed
        reg = default_registry(log=None, load_default_dir=False, policy=Policy())
        self.assertEqual(reg.get("mcp.fake.echo").risk, "read")
        self.assertEqual(reg.get("mcp.fake.delete_everything").risk, "danger")
        self.assertEqual(reg.run("mcp.fake.echo", {"text": "hello"}).text, "hello")
        name, kw = reg.parse_command("/mcp.fake.add 2 3")
        self.assertEqual(kw, {"a": "2", "b": "3"})
        res = reg.run(name, kw)
        self.assertIn("approval", res.text)  # "add" has no hints: inferred write -> ask -> refused without a prompt
        reg.confirm = lambda s, a: True
        self.assertEqual(reg.run(name, kw).text, "5")  # strings coerced to integers by the tool's schema
        self.assertFalse(reg.run("mcp.fake.delete_everything", {}).ok)
        self.assertEqual(reg.search("echo text")[0].name, "mcp.fake.echo")

    def test_http_server(self):
        from libra import mcp

        os.environ["MCP_TOKEN"] = "tok"
        mcp.save_config({"remote": {"url": self.base + "/mcp", "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}}})
        mcp.sync("remote", log=None)
        self.assertEqual(mcp.call_tool("remote", "whoami", {}).text, "auth=Bearer tok")

    def test_cli(self):
        out = self.cli("mcp", "add", "fake", "--", sys.executable, FAKE_MCP)
        self.assertIn("3 tool(s)", out)
        self.assertIn("mcp.fake.echo", self.cli("skill", "find", "echo"))
        self.assertIn("risk: danger", self.cli("skill", "info", "mcp.fake.delete_everything"))
        self.assertIn("hi", self.cli("skill", "run", "mcp.fake.echo", "hi"))
        self.assertIn("removed fake and 3 tool(s)", self.cli("mcp", "remove", "fake"))


class OpenAPITest(Base):
    def write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_import_and_call(self):
        from libra import openapi
        from libra.skills import default_registry
        from libra.trust import Policy

        spec = self.write("pets.yaml", PETSTORE_YAML)
        os.environ["PET_TOKEN"] = "secret123"
        n = openapi.add_api("pets", spec, self.base, {"type": "bearer", "env": "PET_TOKEN"})
        self.assertEqual(n, 4)
        self.assertNotIn("secret123", open(os.path.join(self.tmp.name, "apis.json")).read())
        reg = default_registry(log=None, load_default_dir=False, policy=Policy(), confirm=lambda s, a: True)
        risks = {s: reg.get(f"api.pets.{s}").risk for s in ("list_pets", "create_pet", "get_pet_by_id", "delete_pet")}
        self.assertEqual(risks, {"list_pets": "read", "create_pet": "write", "get_pet_by_id": "read",
                                 "delete_pet": "danger"})
        got = reg.run("api.pets.list_pets", {"limit": "5"}).data["body"]
        self.assertEqual((got["method"], got["path"], got["auth"]), ("GET", "/pets?limit=5", "Bearer secret123"))
        got = reg.run("api.pets.get_pet_by_id", {"pet_id": "a/b"}).data["body"]
        self.assertEqual(got["path"], "/pets/a%2Fb")  # path values are escaped
        got = reg.run("api.pets.create_pet", {"name": "Rex", "tag": "dog"}).data["body"]
        self.assertEqual((got["method"], got["body"]), ("POST", {"name": "Rex", "tag": "dog"}))
        self.assertIn("missing required", reg.run("api.pets.create_pet", {"tag": "x"}).text)
        self.assertEqual(reg.search("delete a pet")[0].name, "api.pets.delete_pet")
        del os.environ["PET_TOKEN"]
        self.assertIn("PET_TOKEN", reg.run("api.pets.list_pets", {}).text)

    def test_swagger2_and_dir(self):
        from libra import openapi

        ops = openapi.operations(SWAGGER2)
        self.assertEqual(ops[0]["name"], "place_order")
        self.assertEqual(ops[0]["input_schema"]["properties"]["qty"]["type"], "integer")
        self.assertEqual(openapi.base_url(SWAGGER2, "x.json"), "https://old.example.com/v2")
        folder = os.path.join(self.tmp.name, "specs")
        os.makedirs(folder)
        with open(os.path.join(folder, "old.json"), "w") as f:
            json.dump(SWAGGER2, f)
        with open(os.path.join(folder, "broken.yaml"), "w") as f:
            f.write("not: [an api")
        out = openapi.add_dir(folder, log=None)
        self.assertEqual(out["imported"], {"old_api": 1})
        self.assertEqual(len(out["failed"]), 1)

    def test_cli(self):
        spec = self.write("pets.yaml", PETSTORE_YAML)
        self.assertIn("imported 4", self.cli("api", "add", "pets", spec, "--base-url", self.base))
        self.assertIn("api.pets.list_pets", self.cli("skill", "find", "list", "pets"))
        out = self.cli("skill", "run", "api.pets.list_pets", "limit=2")
        self.assertIn('"/pets?limit=2"', out)
        self.assertIn("4 operation(s)", self.cli("api", "list"))


class TrustTest(Base):
    def test_policy_and_audit(self):
        from libra.skills import Skill, SkillRegistry
        from libra.trust import Policy, infer_risk, redact

        self.assertEqual([infer_risk(n) for n in ("get_user", "createIssue", "delete_repo", "send_email", "transfer_funds",
                                                  "list_settings")],
                         ["read", "write", "danger", "write", "danger", "read"])
        p = Policy()
        reg = SkillRegistry(policy=p)
        calls = []
        reg.register(Skill("look", "", lambda: "seen", risk="read", source="x"))
        reg.register(Skill("post", "", lambda token="": calls.append(token) or "posted", risk="write", source="x"))
        self.assertEqual(reg.run("look").text, "seen")
        self.assertFalse(reg.run("post").ok)  # ask without anyone to ask -> refused
        reg.confirm = lambda s, a: False
        self.assertFalse(reg.run("post").ok)
        reg.confirm = lambda s, a: True
        self.assertTrue(reg.run("post", {"token": "abc"}).ok)
        p.set("post", "deny")
        self.assertIn("blocked", reg.run("post").text)  # deny wins even with approval
        p.reset("post")
        p.set("write", "allow")
        reg.confirm = None
        self.assertTrue(reg.run("post").ok)
        self.assertEqual(Policy().defaults["write"], "allow")  # saved to trust.json
        p.set("mcp.github", "deny")
        self.assertEqual(p.decide("mcp.github.list_issues", "read")[0], "deny")  # source prefix rule
        self.assertEqual(calls, ["abc", ""])
        with open(os.path.join(self.tmp.name, "audit.jsonl")) as f:
            log = [json.loads(l) for l in f]
        self.assertEqual([e["decision"] for e in log if e["skill"] == "post"][:3], ["deny", "deny", "approved"])
        self.assertTrue(all(e["args"].get("token") in (None, "***") for e in log))
        self.assertEqual(redact({"api_key": "k", "q": "x"}), {"api_key": "***", "q": "x"})

    def test_server_refuses_unapproved(self):
        import urllib.error
        import urllib.request

        from libra.server import Engine, make_server
        from libra.skills import default_registry
        from libra.trust import Policy

        spec = os.path.join(self.tmp.name, "pets.yaml")
        with open(spec, "w") as f:
            f.write(PETSTORE_YAML)
        from libra import openapi

        openapi.add_api("pets", spec, self.base)
        reg = default_registry(log=None, load_default_dir=False, policy=Policy())
        srv = make_server(Engine(None, None, skills=reg), "127.0.0.1", 0)
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def post(name, body):
                req = urllib.request.Request(f"{url}/v1/skills/{name}", json.dumps(body).encode(),
                                             {"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        return r.status, json.load(r)
                except urllib.error.HTTPError as e:
                    return e.code, json.load(e)

            self.assertEqual(post("api.pets.get_pet_by_id", {"pet_id": "7"})[0], 200)
            status, body = post("api.pets.delete_pet", {"pet_id": "7"})
            self.assertEqual(status, 403)
            self.assertIn("libra trust allow api.pets.delete_pet", body["text"])
            with urllib.request.urlopen(url + "/v1/skills?q=delete+pet", timeout=10) as r:
                found = json.load(r)
            self.assertEqual(found["data"][0]["function"]["name"], "api.pets.delete_pet")
            self.assertEqual(found["catalog_size"], 4)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
