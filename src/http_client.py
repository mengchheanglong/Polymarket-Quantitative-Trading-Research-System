from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


class HttpError(RuntimeError):
    pass


class JsonHttpClient:
    def __init__(self, timeout: float = 10.0, user_agent: str = "polymarket-paper-agent/0.1"):
        self.timeout = timeout
        self.user_agent = user_agent

    def get_json(self, base_url: str, path: str = "", params: dict[str, Any] | None = None) -> Any:
        url = self._url(base_url, path, params)
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - exact urllib failure class varies.
            raise HttpError(f"GET {url} failed: {exc}") from exc

    @staticmethod
    def _url(base_url: str, path: str, params: dict[str, Any] | None) -> str:
        url = base_url.rstrip("/")
        if path:
            url += "/" + path.lstrip("/")
        if params:
            filtered = {key: value for key, value in params.items() if value is not None}
            url += "?" + urllib.parse.urlencode(filtered, doseq=True)
        return url

