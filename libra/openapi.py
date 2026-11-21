"""Turn OpenAPI 3.x / Swagger 2.0 specs (JSON or YAML) into skills, one per endpoint.

``add_api("petstore", "petstore.yaml")`` registers ``api.petstore.<operation>``
skills in the catalog. Each skill's arguments are the operation's path,
query and header parameters plus the JSON (or form) body fields.

Credentials are never stored. The API's settings in ``~/.libra/apis.json``
only name an environment variable (``auth: {"type": "bearer", "env":
"GITHUB_TOKEN"}``), which is read at call time.

Risk levels follow the HTTP method: GET/HEAD/OPTIONS are ``read``, DELETE
is ``danger`` and everything else is ``write`` (see :mod:`libra.trust`).
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse

from . import home
from .catalog import Catalog, Entry, safe_name

CONFIG = "apis.json"
METHODS = ("get", "put", "post", "delete", "patch", "head", "options")
AUTH_TYPES = ("bearer", "header", "query", "basic")


class OpenAPIError(ValueError):
    pass


def load_configs() -> dict:
    return home.read_json(CONFIG, {})


def save_configs(cfg: dict) -> None:
    home.write_json(CONFIG, cfg)


# ------------------------------------------------------------------ loading
def load_spec(source: str) -> tuple[dict, str]:
    """``(spec, location)`` from a file path or http(s) URL; JSON or YAML."""
    if source.startswith(("http://", "https://")):
        from .web import Fetcher

        allow = os.environ.get("LIBRA_ALLOW_PRIVATE") == "1"
        text = Fetcher(allow_private=allow, respect_robots=False).request("GET", source).text
    else:
        with open(os.path.expanduser(source), encoding="utf-8") as f:
            text = f.read()
    stripped = text.lstrip()
    if stripped.startswith("{"):
        spec = json.loads(text)
    else:
        from .yamlish import loads

        spec = loads(text)
    if not isinstance(spec, dict) or "paths" not in spec:
        raise OpenAPIError(f"{source} is not an OpenAPI or Swagger document (no 'paths')")
    return spec, source


class _Resolver:
    def __init__(self, spec: dict):
        self.spec = spec

    def ref(self, pointer: str):
        if not pointer.startswith("#/"):
            raise OpenAPIError(f"only local $refs are supported: {pointer}")
        node = self.spec
        for part in pointer[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            node = node[part]
        return node

    def resolve(self, node, depth: int = 0, seen=()):
        """Inline $refs, with a depth limit so huge or recursive schemas stay small."""
        if depth > 8:
            return {"type": "object"} if isinstance(node, dict) else node
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                if ref in seen:
                    return {"type": "object", "description": f"(recursive {ref.rsplit('/', 1)[-1]})"}
                try:
                    target = self.ref(ref)
                except (KeyError, TypeError):
                    return {"type": "object"}
                return self.resolve(target, depth + 1, seen + (ref,))
            return {k: self.resolve(v, depth + 1, seen) for k, v in node.items() if k not in ("example", "examples")}
        if isinstance(node, list):
            return [self.resolve(v, depth + 1, seen) for v in node]
        return node


def base_url(spec: dict, location: str) -> str:
    if spec.get("servers"):
        server = spec["servers"][0]
        url = server.get("url", "")
        for var, info in (server.get("variables") or {}).items():
            url = url.replace("{" + var + "}", str(info.get("default", "")))
    elif spec.get("host"):  # Swagger 2.0
        scheme = (spec.get("schemes") or ["https"])[0]
        url = f"{scheme}://{spec['host']}{spec.get('basePath', '')}"
    else:
        url = ""
    if location.startswith(("http://", "https://")):
        url = urllib.parse.urljoin(location, url or "/")
    return url.rstrip("/")


def _short(text: str, n: int = 300) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _param_schema(p: dict) -> dict:
    schema = dict(p.get("schema") or {k: p[k] for k in ("type", "enum", "items", "format", "default") if k in p})
    if p.get("description"):
        schema["description"] = _short(p["description"], 200)
    return schema or {"type": "string"}


def operations(spec: dict) -> list[dict]:
    """Every operation as a plain dict: method, path, parameters, body handling and input schema."""
    r = _Resolver(spec)
    swagger2 = str(spec.get("swagger", "")).startswith("2")
    ops, used = [], set()
    for path, item in (spec.get("paths") or {}).items():
        item = r.resolve(item, 0) if isinstance(item, dict) else {}
        shared = item.get("parameters") or []
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = {}
            for p in shared + (op.get("parameters") or []):
                p = r.resolve(p)
                if isinstance(p, dict) and p.get("name"):
                    params[(p["name"], p.get("in"))] = p
            props, required, plist = {}, [], []
            body_schema, content_type, body_required = None, "application/json", False
            form_fields = []
            for (pname, where), p in params.items():
                if where == "body":  # Swagger 2.0 body parameter
                    body_schema, body_required = r.resolve(p.get("schema") or {}), bool(p.get("required"))
                    continue
                if where == "formData":
                    content_type = "application/x-www-form-urlencoded"
                    form_fields.append(pname)
                elif where not in ("path", "query", "header"):
                    continue
                key = safe_name(pname) if pname not in props else safe_name(f"{where}_{pname}")
                props[key] = _param_schema(p)
                plist.append({"name": pname, "in": where, "arg": key})
                if p.get("required") or where == "path":
                    required.append(key)
            if not swagger2 and isinstance(op.get("requestBody"), dict):
                rb = r.resolve(op["requestBody"])
                content = rb.get("content") or {}
                for ctype in ("application/json", "application/x-www-form-urlencoded", "text/plain"):
                    if ctype in content:
                        content_type = ctype
                        break
                else:
                    content_type = next(iter(content), "application/json")
                body_schema = (content.get(content_type) or {}).get("schema") or {}
                body_required = bool(rb.get("required"))
            body = None
            if body_schema is not None:
                bprops = body_schema.get("properties") if isinstance(body_schema, dict) else None
                if bprops and not set(map(safe_name, bprops)) & set(props):
                    fields = []
                    for fname, fschema in bprops.items():
                        key = safe_name(fname)
                        props[key] = fschema if isinstance(fschema, dict) else {}
                        fields.append({"name": fname, "arg": key})
                        if body_required and fname in (body_schema.get("required") or []):
                            required.append(key)
                    body = {"mode": "fields", "fields": fields, "content_type": content_type}
                else:
                    props["body"] = body_schema or {"type": "object"}
                    if body_required:
                        required.append("body")
                    body = {"mode": "whole", "content_type": content_type}
            elif form_fields:
                body = {"mode": "form_params", "content_type": content_type}
            op_id = op.get("operationId") or f"{method}_{path}"
            name = safe_name(op_id)
            if name in used:
                name = safe_name(f"{method}_{path}")
            used.add(name)
            ops.append({
                "name": name, "method": method.upper(), "path": path,
                "description": _short(op.get("summary") or op.get("description") or f"{method.upper()} {path}"),
                "params": plist, "body": body,
                "input_schema": {"type": "object", "properties": props, "required": sorted(set(required))},
            })
    return ops


def method_risk(method: str) -> str:
    return "read" if method in ("GET", "HEAD", "OPTIONS") else "danger" if method == "DELETE" else "write"


# ----------------------------------------------------------------- register
def add_api(name: str, source: str, base: str | None = None, auth: dict | None = None,
            catalog: Catalog | None = None) -> int:
    """Import a spec into the catalog as ``api.<name>.*`` and remember its settings."""
    api = safe_name(name)
    spec, location = load_spec(source)
    url = base or base_url(spec, location)
    if not url.startswith(("http://", "https://")):
        raise OpenAPIError(f"{name}: the spec has no absolute server URL; pass --base-url")
    if auth:
        if auth.get("type") not in AUTH_TYPES:
            raise OpenAPIError(f"auth type must be one of {', '.join(AUTH_TYPES)}")
        if any(re.search(r"(token|key|secret|password)$", str(k), re.I) and k not in ("env", "env_user", "env_pass")
               for k in auth):
            raise OpenAPIError("store the environment variable's name in auth, never the secret itself")
    source_name = f"api.{api}"
    entries = []
    for op in operations(spec):
        entries.append(Entry(f"{source_name}.{op['name']}", op["description"], "openapi", source_name,
                             method_risk(op["method"]),
                             {"api": api, "method": op["method"], "path": op["path"], "params": op["params"],
                              "body": op["body"], "input_schema": op["input_schema"]}))
    catalog = catalog or Catalog()
    catalog.add_many(entries, replace_source=source_name)
    cfg = load_configs()
    cfg[api] = {"source": os.path.abspath(source) if os.path.exists(source) else source, "base_url": url,
                "auth": auth or {}, "title": (spec.get("info") or {}).get("title", name), "operations": len(entries)}
    save_configs(cfg)
    return len(entries)


def add_dir(folder: str, catalog: Catalog | None = None, log=print) -> dict:
    """Import every spec file under ``folder`` (e.g. a mirror of a public API directory)."""
    catalog = catalog or Catalog()
    done, failed = {}, {}
    for root, _, files in os.walk(folder):
        for fname in sorted(files):
            if not fname.lower().endswith((".json", ".yaml", ".yml")):
                continue
            path = os.path.join(root, fname)
            try:
                spec, _ = load_spec(path)
                title = (spec.get("info") or {}).get("title") or os.path.splitext(fname)[0]
                name = safe_name(title)
                n = 2
                while name in done:
                    name = safe_name(f"{title}_{n}")
                    n += 1
                done[name] = add_api(name, path, catalog=catalog)
                if log:
                    log(f"  {name}: {done[name]} operation(s)")
            except Exception as exc:
                failed[path] = str(exc)
                if log:
                    log(f"  skipped {path}: {exc}")
    return {"imported": done, "failed": failed}


def remove_api(name: str, catalog: Catalog | None = None) -> int:
    api = safe_name(name)
    cfg = load_configs()
    cfg.pop(api, None)
    save_configs(cfg)
    return (catalog or Catalog()).remove_source(f"api.{api}")


# --------------------------------------------------------------------- call
def _auth(cfg: dict, headers: dict, query: list) -> None:
    auth = cfg.get("auth") or {}
    kind = auth.get("type")
    if not kind:
        return
    if kind == "basic":
        user, pw = os.environ.get(auth.get("env_user", ""), ""), os.environ.get(auth.get("env_pass", ""), "")
        if not user:
            raise OpenAPIError(f"set the environment variable {auth.get('env_user')} (and {auth.get('env_pass')})")
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        return
    secret = os.environ.get(auth.get("env", ""), "")
    if not secret:
        raise OpenAPIError(f"set the environment variable {auth.get('env')} with your API key")
    if kind == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif kind == "header":
        headers[auth.get("name", "X-API-Key")] = secret
    elif kind == "query":
        query.append((auth.get("name", "api_key"), secret))


def build_request(spec: dict, args: dict, cfg: dict):
    """``(method, url, headers, body bytes)`` for one call. Secrets come from the environment."""
    path, headers, query = spec["path"], {"Accept": "application/json"}, []
    for p in spec["params"]:
        if p["arg"] not in args:
            continue
        value = args[p["arg"]]
        if p["in"] == "path":
            path = path.replace("{" + p["name"] + "}", urllib.parse.quote(str(value), safe=""))
        elif p["in"] == "query":
            for v in (value if isinstance(value, list) else [value]):
                query.append((p["name"], str(v).lower() if isinstance(v, bool) else str(v)))
        elif p["in"] == "header":
            headers[p["name"]] = str(value)
    missing = re.findall(r"\{([^}]+)\}", path)
    if missing:
        raise OpenAPIError(f"missing path parameter(s): {', '.join(missing)}")
    _auth(cfg, headers, query)
    data = None
    body = spec.get("body")
    if body:
        if body["mode"] == "fields":
            payload = {f["name"]: args[f["arg"]] for f in body["fields"] if f["arg"] in args}
            payload = payload if payload else None
        elif body["mode"] == "whole":
            payload = args.get("body")
        else:
            payload = {p["name"]: args[p["arg"]] for p in spec["params"] if p["in"] == "formData" and p["arg"] in args}
        if payload is not None:
            if body["content_type"] == "application/x-www-form-urlencoded":
                data = urllib.parse.urlencode(payload, doseq=True).encode()
            elif body["content_type"].startswith("text/"):
                data = str(payload).encode()
            else:
                data = json.dumps(payload).encode()
            headers["Content-Type"] = body["content_type"]
    url = cfg["base_url"] + path + ("?" + urllib.parse.urlencode(query) if query else "")
    return spec["method"], url, headers, data


def call_operation(spec: dict, args: dict):
    from .skills import SkillResult
    from .web import Fetcher

    cfg = load_configs().get(spec["api"])
    if cfg is None:
        raise OpenAPIError(f"API {spec['api']!r} is not configured any more (libra api list)")
    method, url, headers, data = build_request(spec, args, cfg)
    allow = os.environ.get("LIBRA_ALLOW_PRIVATE") == "1"
    resp = Fetcher(allow_private=allow, respect_robots=False, timeout=30).request(
        method, url, headers, data, raise_for_status=False)
    text = resp.text
    parsed = None
    if "json" in resp.content_type:
        try:
            parsed = json.loads(text)
            text = json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    if len(text) > 8000:
        text = text[:8000] + f"\n… ({len(resp.text):,} characters in total)"
    name = f"api.{spec['api']}.{safe_name(spec['path'])}"
    ok = resp.status < 400
    return SkillResult(name, ok, f"HTTP {resp.status}\n{text}" if not ok else text,
                       {"status": resp.status, "body": parsed})
