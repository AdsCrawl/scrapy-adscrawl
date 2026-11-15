from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from scrapy import Request
from scrapy.crawler import Crawler
from scrapy.http import HtmlResponse, Response
from scrapy.http.request import NO_CALLBACK

API_ENDPOINT = "https://api.adscrawl.net/html"
_INTERNAL_REQUEST_META = "_scrapy_adscrawl_api_request"
_ALLOWED_OPTIONS = {"selector", "wait_until", "timeout_ms"}
_WAIT_UNTIL_VALUES = {"domcontentloaded", "load", "networkidle"}
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z0-9_]{1,80}$")


class AdsCrawlMiddlewareError(RuntimeError):
    """Raised when an opted-in request cannot be safely sent through AdsCrawl."""


class AdsCrawlAPIError(AdsCrawlMiddlewareError):
    """Raised when the AdsCrawl API returns a non-success status."""

    def __init__(self, status_code: int, code: str | None = None) -> None:
        self.status_code = status_code
        self.code = code
        message = f"AdsCrawl API returned HTTP {status_code}"
        if code:
            message += f" ({code})"
        super().__init__(message)


class AdsCrawlTransportError(AdsCrawlMiddlewareError):
    """Raised when Scrapy cannot complete the API request to AdsCrawl."""


class AdsCrawlDownloaderMiddleware:
    """Render opted-in GET requests through AdsCrawl and return an HTML response.

    Set ``ADSCRAWL_API_KEY`` in the process environment. To opt a request in,
    set ``request.meta['adscrawl']`` to a mapping of supported render options.
    """

    def __init__(self, crawler: Crawler, api_key: str | None) -> None:
        self.crawler = crawler
        self.api_key = api_key.strip() if api_key else ""
        self._api_slot = f"scrapy-adscrawl-api-{id(self)}"

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> AdsCrawlDownloaderMiddleware:
        return cls(crawler, os.environ.get("ADSCRAWL_API_KEY"))

    async def process_request(self, request: Request, spider: Any = None) -> Response | None:
        # Internal API requests pass through the same downloader middleware chain.
        if request.meta.get(_INTERNAL_REQUEST_META):
            return None

        options = request.meta.get("adscrawl")
        if options is None:
            return None
        if not isinstance(options, Mapping):
            raise AdsCrawlMiddlewareError("request.meta['adscrawl'] must be a mapping")
        if not self.api_key:
            raise AdsCrawlMiddlewareError(
                "Set ADSCRAWL_API_KEY in the process environment before using AdsCrawl requests"
            )
        if request.method != "GET" or request.body:
            raise AdsCrawlMiddlewareError(
                "AdsCrawl middleware supports GET requests without a body"
            )

        self._validate_target_url(request.url)
        payload = self._build_payload(request.url, options)
        if request.meta.get("download_slot") == self._api_slot:
            raise AdsCrawlMiddlewareError(
                "Target request cannot use the reserved AdsCrawl API slot"
            )

        # A validated request may consume a credit even if the response is lost.
        # Prevent Scrapy from transparently repeating either the outer request or
        # the internal POST and unexpectedly charging for the same page again.
        request.meta["dont_retry"] = True
        request.meta["dont_cache"] = True

        api_meta: dict[str, Any] = {
            _INTERNAL_REQUEST_META: True,
            "dont_obey_robotstxt": True,
            "dont_retry": True,
            "dont_redirect": True,
            "dont_cache": True,
            "dont_merge_cookies": True,
            "download_slot": self._api_slot,
        }
        if "download_timeout" in request.meta:
            api_meta["download_timeout"] = request.meta["download_timeout"]

        api_request = Request(
            API_ENDPOINT,
            method="POST",
            headers={
                "Accept": "text/html",
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
            },
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            callback=NO_CALLBACK,
            dont_filter=True,
            meta=api_meta,
        )

        try:
            api_response = await self.crawler.engine.download_async(api_request)
        except Exception as exc:
            # Do not echo exception text: it can contain request details or credentials.
            raise AdsCrawlTransportError(
                f"AdsCrawl API request failed ({type(exc).__name__})"
            ) from None

        if not isinstance(api_response, Response):
            raise AdsCrawlMiddlewareError("Scrapy did not return an API response")
        if api_response.status != 200:
            raise AdsCrawlAPIError(api_response.status, self._read_error_code(api_response))

        return HtmlResponse(
            url=request.url,
            status=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=api_response.body,
            encoding="utf-8",
            request=request,
            flags=["adscrawl-rendered"],
        )

    @staticmethod
    def _build_payload(url: str, options: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(options) - _ALLOWED_OPTIONS
        if unknown:
            raise AdsCrawlMiddlewareError("Unsupported AdsCrawl render option")

        payload: dict[str, Any] = {"url": url, "contentMode": "html"}

        selector = options.get("selector")
        if selector is not None:
            if not isinstance(selector, str) or not selector.strip():
                raise AdsCrawlMiddlewareError("selector must be a non-empty string")
            payload["selector"] = selector

        wait_until = options.get("wait_until")
        if wait_until is not None:
            if not isinstance(wait_until, str) or wait_until not in _WAIT_UNTIL_VALUES:
                raise AdsCrawlMiddlewareError(
                    "wait_until must be domcontentloaded, load, or networkidle"
                )
            payload["waitUntil"] = wait_until

        timeout_ms = options.get("timeout_ms")
        if timeout_ms is not None:
            if type(timeout_ms) is not int or not 1 <= timeout_ms <= 3_600_000:
                raise AdsCrawlMiddlewareError("timeout_ms must be an integer from 1 to 3600000")
            payload["timeoutMs"] = timeout_ms

        return payload

    @staticmethod
    def _validate_target_url(url: str) -> None:
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            raise AdsCrawlMiddlewareError("Invalid target URL") from None

        if parsed.scheme not in {"http", "https"} or not hostname:
            raise AdsCrawlMiddlewareError("Target URL must use http or https and include a host")
        if parsed.username is not None or parsed.password is not None:
            raise AdsCrawlMiddlewareError("Target URL must not contain embedded credentials")
        if port not in {None, 80, 443}:
            raise AdsCrawlMiddlewareError("AdsCrawl supports target ports 80 and 443 only")

        normalized_host = hostname.rstrip(".").lower()
        if normalized_host == "localhost" or normalized_host.endswith(
            (".localhost", ".local", ".internal")
        ):
            raise AdsCrawlMiddlewareError("Local or internal target hosts are not supported")
        try:
            address = ipaddress.ip_address(normalized_host)
        except ValueError:
            return
        if not address.is_global:
            raise AdsCrawlMiddlewareError("Non-public IP target addresses are not supported")

    @staticmethod
    def _read_error_code(response: Response) -> str | None:
        try:
            body = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return None
        if not isinstance(body, dict):
            return None
        code = body.get("code")
        if isinstance(code, str) and _ERROR_CODE_PATTERN.fullmatch(code):
            return code
        return None
