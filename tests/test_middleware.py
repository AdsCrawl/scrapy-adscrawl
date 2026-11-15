from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse, Response

from scrapy_adscrawl.middleware import (
    API_ENDPOINT,
    AdsCrawlAPIError,
    AdsCrawlDownloaderMiddleware,
    AdsCrawlMiddlewareError,
    AdsCrawlTransportError,
)


class FakeEngine:
    def __init__(self, response: Response | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[Request] = []

    async def download_async(self, request: Request) -> Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def make_middleware(
    *,
    response: Response | None = None,
    error: Exception | None = None,
    api_key: str | None = "test-key-not-real",
) -> tuple[AdsCrawlDownloaderMiddleware, FakeEngine]:
    engine = FakeEngine(response=response, error=error)
    crawler = SimpleNamespace(engine=engine)
    return AdsCrawlDownloaderMiddleware(crawler, api_key), engine  # type: ignore[arg-type]


def api_response(
    status: int = 200, body: bytes = b"<html><main>rendered</main></html>"
) -> Response:
    return Response(API_ENDPOINT, status=status, body=body)


@pytest.mark.asyncio
async def test_request_is_untouched_without_explicit_opt_in() -> None:
    middleware, engine = make_middleware()
    request = Request("https://example.com/article")

    result = await middleware.process_request(request, spider=object())

    assert result is None
    assert engine.requests == []
    assert "dont_retry" not in request.meta


@pytest.mark.asyncio
async def test_internal_api_request_does_not_recurse() -> None:
    middleware, engine = make_middleware()
    request = Request(API_ENDPOINT, meta={"_scrapy_adscrawl_api_request": True})

    result = await middleware.process_request(request, spider=object())

    assert result is None
    assert engine.requests == []


@pytest.mark.asyncio
async def test_rendered_html_is_returned_as_scrapy_html_response() -> None:
    middleware, engine = make_middleware(response=api_response())
    request = Request(
        "https://example.com/article?ref=docs",
        meta={
            "adscrawl": {"selector": "main", "wait_until": "domcontentloaded", "timeout_ms": 30_000}
        },
    )

    result = await middleware.process_request(request, spider=object())

    assert isinstance(result, HtmlResponse)
    assert result.url == request.url
    assert result.status == 200
    assert result.css("main::text").get() == "rendered"
    assert result.flags == ["adscrawl-rendered"]
    assert len(engine.requests) == 1

    api_request = engine.requests[0]
    assert api_request.url == API_ENDPOINT
    assert api_request.method == "POST"
    assert api_request.headers.get(b"x-api-key") == b"test-key-not-real"
    assert api_request.headers.get(b"content-type") == b"application/json"
    assert json.loads(api_request.body) == {
        "url": "https://example.com/article?ref=docs",
        "contentMode": "html",
        "selector": "main",
        "waitUntil": "domcontentloaded",
        "timeoutMs": 30_000,
    }


@pytest.mark.asyncio
async def test_api_request_disables_redirect_retry_and_robot_fetch_and_outer_retry() -> None:
    middleware, engine = make_middleware(response=api_response())
    request = Request("https://example.com", meta={"adscrawl": {}})

    await middleware.process_request(request, spider=object())

    assert request.meta["dont_retry"] is True
    assert request.meta["dont_cache"] is True
    api_meta = engine.requests[0].meta
    assert api_meta["dont_retry"] is True
    assert api_meta["dont_redirect"] is True
    assert api_meta["dont_obey_robotstxt"] is True
    assert api_meta["_scrapy_adscrawl_api_request"] is True


@pytest.mark.asyncio
async def test_missing_key_fails_before_any_api_request() -> None:
    middleware, engine = make_middleware(api_key="  ")
    request = Request("https://example.com", meta={"adscrawl": {}})

    with pytest.raises(AdsCrawlMiddlewareError, match="ADSCRAWL_API_KEY"):
        await middleware.process_request(request, spider=object())

    assert engine.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://localhost/",
        "https://sub.localhost/",
        "http://service.internal/",
        "http://127.0.0.1/",
        "http://192.168.1.20/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
    ],
)
async def test_unsafe_or_unsupported_target_url_is_rejected_without_network(url: str) -> None:
    middleware, engine = make_middleware()

    with pytest.raises(AdsCrawlMiddlewareError):
        await middleware.process_request(Request(url, meta={"adscrawl": {}}), spider=object())

    assert engine.requests == []


def test_invalid_port_is_rejected_by_target_validator() -> None:
    with pytest.raises(AdsCrawlMiddlewareError, match="Invalid target URL"):
        AdsCrawlDownloaderMiddleware._validate_target_url("https://example.com:not-a-port/")


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://example.com/", "https://example.com:443/"])
async def test_supported_http_urls_are_accepted(url: str) -> None:
    middleware, engine = make_middleware(response=api_response())

    result = await middleware.process_request(Request(url, meta={"adscrawl": {}}), spider=object())

    assert isinstance(result, HtmlResponse)
    assert len(engine.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        [],
        {"unknown": "value"},
        {"selector": "  "},
        {"selector": 5},
        {"wait_until": "interactive"},
        {"timeout_ms": True},
        {"timeout_ms": 0},
        {"timeout_ms": 3_600_001},
    ],
)
async def test_invalid_options_are_rejected_before_network(options: Any) -> None:
    middleware, engine = make_middleware()

    with pytest.raises(AdsCrawlMiddlewareError):
        await middleware.process_request(
            Request("https://example.com", meta={"adscrawl": options}), spider=object()
        )

    assert engine.requests == []


@pytest.mark.asyncio
async def test_non_get_scrapy_request_is_not_translated_to_a_rendered_get() -> None:
    middleware, engine = make_middleware()
    request = Request("https://example.com", method="POST", body=b"form", meta={"adscrawl": {}})

    with pytest.raises(AdsCrawlMiddlewareError, match="GET requests"):
        await middleware.process_request(request, spider=object())

    assert engine.requests == []


@pytest.mark.asyncio
async def test_api_error_surfaces_status_and_safe_machine_code_only() -> None:
    secretish = "test-key-not-real"
    response = api_response(
        status=402,
        body=b'{"error":"Insufficient credits","code":"INSUFFICIENT_CREDITS","balance":0}',
    )
    middleware, engine = make_middleware(response=response, api_key=secretish)

    with pytest.raises(AdsCrawlAPIError) as error:
        await middleware.process_request(
            Request("https://example.com/private?token=not-for-logs", meta={"adscrawl": {}}),
            spider=object(),
        )

    assert error.value.status_code == 402
    assert error.value.code == "INSUFFICIENT_CREDITS"
    assert secretish not in str(error.value)
    assert "private" not in str(error.value)
    assert "balance" not in str(error.value)
    assert len(engine.requests) == 1


@pytest.mark.asyncio
async def test_transport_error_is_redacted_and_never_retried_by_adapter() -> None:
    middleware, engine = make_middleware(
        error=OSError("failed for https://example.com?token=do-not-log test-key-not-real")
    )
    request = Request("https://example.com", meta={"adscrawl": {}})

    with pytest.raises(AdsCrawlTransportError) as error:
        await middleware.process_request(request, spider=object())

    assert "test-key-not-real" not in str(error.value)
    assert "example.com" not in str(error.value)
    assert error.value.__cause__ is None
    assert request.meta["dont_retry"] is True
    assert len(engine.requests) == 1
    assert engine.requests[0].meta["dont_retry"] is True


def test_from_crawler_reads_only_the_documented_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADSCRAWL_API_KEY", "environment-test-key")
    crawler = SimpleNamespace(engine=FakeEngine())

    middleware = AdsCrawlDownloaderMiddleware.from_crawler(crawler)  # type: ignore[arg-type]

    assert middleware.api_key == "environment-test-key"


def test_public_ip_literal_is_not_confused_with_local_addresses() -> None:
    AdsCrawlDownloaderMiddleware._validate_target_url("https://8.8.8.8/")


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["https://api.adscrawl.net/", "https://example.com/"])
async def test_internal_api_uses_separate_slot_from_same_host_or_custom_outer_slot(
    target: str,
) -> None:
    middleware, engine = make_middleware(response=api_response())
    request = Request(target, meta={"adscrawl": {}, "download_slot": "api.adscrawl.net"})

    await middleware.process_request(request, spider=object())

    internal = engine.requests[0]
    assert internal.meta["download_slot"] != request.meta["download_slot"]
    assert internal.meta["download_slot"] != "api.adscrawl.net"
    assert internal.meta["dont_cache"] is True
    assert internal.meta["dont_merge_cookies"] is True


@pytest.mark.asyncio
async def test_reserved_slot_cannot_be_used_by_outer_request() -> None:
    middleware, engine = make_middleware(response=api_response())
    request = Request(
        "https://example.com/",
        meta={"adscrawl": {}, "download_slot": middleware._api_slot},
    )

    with pytest.raises(AdsCrawlMiddlewareError, match="reserved"):
        await middleware.process_request(request, spider=object())

    assert engine.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"code":"bad secret-containing code"}'])
async def test_arbitrary_error_body_is_never_echoed(body: bytes) -> None:
    middleware, engine = make_middleware(response=api_response(status=503, body=body))

    with pytest.raises(AdsCrawlAPIError) as error:
        await middleware.process_request(
            Request("https://example.com/", meta={"adscrawl": {}}), spider=object()
        )

    assert str(error.value) == "AdsCrawl API returned HTTP 503"
    assert len(engine.requests) == 1
