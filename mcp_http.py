#!/usr/bin/env python3
"""Stubs MCP over Streamable HTTP, behind Cloudflare Access: the stubs-mcp service (post-v1 #46, docs/mcp-setup.md).

The tools are mcp_server.py's, built by build_server(); mcp_remote.py (a verbatim copy of mcp-server-shared's
remote.py) adds the Access JWT check, the Host check and /healthz, which answers ok only while Stubs' own /healthz
does.

The write level is STUBS_MCP_WRITES: "none" (the default; any unknown value is "none" and logged) registers the ten
read tools and the service holds only STUBS_READ_KEY (GET-only on /admin/api); it refuses to start if the
write-capable STUBS_API_KEY is in its environment. "import" adds import_shows and list_imports and holds
STUBS_API_KEY. Neither level has a tool that applies, edits or deletes.

Settings (environment; the init script exports them from /etc/conf.d/stubs): STUBS_URL (empty: 127.0.0.1:<PORT>),
MCP_HTTP_BIND (127.0.0.1), MCP_HTTP_PORT (8774), MCP_HTTP_PATH (/stubs/mcp), MCP_PUBLIC_HOSTS, CF_TEAM_DOMAIN (the
app's; CF_ACCESS_TEAM_DOMAIN overrides it), CF_ACCESS_AUD (the MCP hostname's Access application, not CF_AUD) and
MCP_ALLOWED_EMAILS. This module never imports app: it runs in its own venv with no Flask.
"""
import argparse
import logging
import os
import sys
import urllib.request
from typing import Mapping, Optional

import anyio.to_thread

import mcp_remote
import mcp_server

DEFAULT_PORT = 8774
DEFAULT_PATH = "/stubs/mcp"


def stubs_healthy(base_url: str) -> bool:
    """Blocking GET {base_url}/healthz; True only on HTTP 200 within 2 s."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/healthz", timeout=2) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - any failure means "not healthy"
        return False


def _http_config(env: Mapping[str, str]) -> "mcp_remote.HttpConfig":
    """mcp_remote's settings from this environment. The Access team domain is the app's CF_TEAM_DOMAIN unless
    CF_ACCESS_TEAM_DOMAIN (the shared module's name for it) is set."""
    env = dict(env)
    if not (env.get("CF_ACCESS_TEAM_DOMAIN") or "").strip():
        env["CF_ACCESS_TEAM_DOMAIN"] = env.get("CF_TEAM_DOMAIN", "")
    parser = argparse.ArgumentParser()
    mcp_remote.add_http_arguments(parser, default_port=DEFAULT_PORT, default_path=DEFAULT_PATH)
    return mcp_remote.http_config(parser.parse_args([]), env)


def _health_check(base: str):
    async def health() -> bool:
        return await anyio.to_thread.run_sync(stubs_healthy, base)

    return health


def build_http_app(env: Mapping[str, str], verifier: Optional["mcp_remote.AccessVerifier"] = None):
    """The ASGI app for these settings (tools at their write level). `verifier` is injected by tests."""
    cfg = _http_config(env)
    server = mcp_server.build_server(writes=mcp_server.write_level(env))
    return mcp_remote.build_app(server, cfg, verifier, health_check=_health_check(mcp_server.stubs_url(env)))


def main(argv: Optional[list] = None, env: Optional[Mapping[str, str]] = None) -> int:
    """Run the service. Returns 2 with a one-line stderr message when the settings are unsafe."""
    env = dict(os.environ if env is None else env)
    problem = mcp_server.startup_problem(env)
    if problem:
        print(f"stubs-mcp: {problem}", file=sys.stderr)
        return 2
    try:
        cfg = _http_config(env)
        cfg.check()
    except mcp_remote.ConfigError as exc:
        print(f"stubs-mcp: {exc}", file=sys.stderr)
        return 2
    level = mcp_server.write_level(env)
    mcp_server._default_client = mcp_server.client_for(env)
    server = mcp_server.build_server(writes=level)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, force=True,
                        format="%(asctime)s %(levelname)s %(message)s")
    mcp_log = logging.getLogger("stubs.mcp")
    for note in mcp_server.write_level_warnings(env):
        mcp_log.warning("%s", note)
    mcp_log.info("stubs-mcp starting: %s on %s:%s (%s)", cfg.path, cfg.bind, cfg.port,
                 "read-only" if level == "none" else f"writes: {level}")
    mcp_remote.serve_http(server, cfg, health_check=_health_check(mcp_server.stubs_url(env)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
