"""Resolve golden-query Explore URLs to Looker query objects (``generate resolve-golden``).

Confirmed (Looker API reference): ``GET /queries/slug/{slug}`` returns a Query with ``model``,
``view`` (the explore), ``fields``, ``pivots``, ``filters`` (field -> expression), ``sorts``,
``limit``; ``POST /login`` exchanges API credentials for an access token.

TODO(verify-api): the Authorization header scheme for the access token (we send ``Bearer``) and
the login form fields (``client_id`` / ``client_secret``).

Credentials: LOOKER_CLIENT_ID / LOOKER_CLIENT_SECRET env vars; base URL from the env var named by
``looker.base_url_env`` (default LOOKER_BASE_URL). Results are cached in build/golden_cache.json
so compiling never needs Looker access.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from lookml_agentops._util.hashing import canonical_json
from lookml_agentops._util.io import write_text
from lookml_agentops.config import LkagentConfig
from lookml_agentops.generate.bind import load_golden_cache
from lookml_agentops.spec.discover import discover_specs
from lookml_agentops.spec.model import LookerFilter, LookerQuery
from lookml_agentops.spec.resolve import resolve_spec


class GoldenResolveError(Exception):
    pass


def slug_from_url(url: str) -> str:
    """Explore share URLs carry the slug as ``?qid=<slug>`` or as ``/x/<slug>``."""
    parsed = urlparse(url)
    qid = parse_qs(parsed.query).get("qid")
    if qid:
        return qid[0]
    m = re.search(r"/x/([A-Za-z0-9]+)", parsed.path)
    if m:
        return m.group(1)
    raise GoldenResolveError(
        f"cannot find a query slug in {url!r} (expected ?qid=<slug> or /x/<slug>)"
    )


def query_to_looker_query(q: dict[str, Any]) -> LookerQuery:
    filters = q.get("filters") or {}
    return LookerQuery(
        model=str(q["model"]),
        explore=str(q["view"]),
        fields=[str(f) for f in q.get("fields") or []],
        filters=[LookerFilter(field=str(k), value=str(v)) for k, v in sorted(filters.items())],
        sorts=[str(s) for s in q.get("sorts") or []],
        limit=None if q.get("limit") in (None, "") else str(q["limit"]),
        pivots=[str(p) for p in q.get("pivots") or []],
    )


class LookerClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        api_version: str = "4.0",
        transport: Any = None,
    ) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional extra
            raise GoldenResolveError("install the [ca] extra for Looker API access") from exc
        self._api = f"{base_url.rstrip('/')}/api/{api_version}"
        self._client = httpx.Client(timeout=60.0, transport=transport)
        resp = self._client.post(
            f"{self._api}/login", data={"client_id": client_id, "client_secret": client_secret}
        )
        if resp.status_code >= 400:
            raise GoldenResolveError(f"Looker login failed (HTTP {resp.status_code})")
        self._client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"

    def query_for_slug(self, slug: str) -> dict[str, Any]:
        resp = self._client.get(f"{self._api}/queries/slug/{slug}")
        if resp.status_code >= 400:
            raise GoldenResolveError(f"query slug {slug!r}: HTTP {resp.status_code}")
        data = resp.json()
        if not isinstance(data, dict):
            raise GoldenResolveError(f"query slug {slug!r}: unexpected response")
        return data

    def close(self) -> None:
        self._client.close()


def client_from_env(cfg: LkagentConfig) -> LookerClient:
    env = os.environ
    names = [cfg.looker.base_url_env, "LOOKER_CLIENT_ID", "LOOKER_CLIENT_SECRET"]
    missing = [n for n in names if not env.get(n)]
    if missing:
        raise GoldenResolveError(
            f"resolve-golden needs environment variables: {', '.join(missing)}"
        )
    return LookerClient(
        env[names[0]], env["LOOKER_CLIENT_ID"], env["LOOKER_CLIENT_SECRET"], cfg.looker.api_version
    )


def resolve_golden(cfg: LkagentConfig, client: LookerClient | None = None) -> dict[str, list[str]]:
    """Resolve every Explore URL in every spec; returns {"resolved": [...], "cached": [...]}."""
    cache_path = cfg.path(cfg.build.out_dir) / "golden_cache.json"
    cache = load_golden_cache(cache_path)
    urls = sorted(
        {
            q.explore_url
            for sf in discover_specs(cfg)
            for q in resolve_spec(sf.path, cfg.root).golden_queries
            if q.explore_url
        }
    )
    todo = [u for u in urls if u not in cache]
    report: dict[str, list[str]] = {"resolved": [], "cached": [u for u in urls if u in cache]}
    if todo:
        own = client is None
        client = client or client_from_env(cfg)
        try:
            for url in todo:
                cache[url] = query_to_looker_query(client.query_for_slug(slug_from_url(url)))
                report["resolved"].append(url)
        finally:
            if own:
                client.close()
    write_text(
        cache_path,
        canonical_json(
            {
                "queries": {u: q.model_dump(mode="json") for u, q in sorted(cache.items())},
            }
        ),
    )
    return report
