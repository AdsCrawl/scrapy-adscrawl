from __future__ import annotations

import json
import sys
from typing import Any

import pytest
from scrapy import Request, Spider
from scrapy.crawler import AsyncCrawlerRunner
from scrapy.http import HtmlResponse, Response

from scrapy_adscrawl.middleware import API_ENDPOINT


class FakeHTTPDownloadHandler:
    """A deterministic downloader handler: no sockets, only canned responses."""

    lazy = True
    requests: list[Request] = []

    def __init__(self, settings: Any, crawler: Any | None = None) -> None:
        self.__class__.requests = []

    @classmethod
    def from_crawler(cls, crawler: Any) -> FakeHTTPDownloadHandler:
        return cls(crawler.settings, crawler)

    async def download_request(self, request: Request) -> Response:
        self.__class__.requests.append(request)
        if request.url == API_ENDPOINT:
            assert request.method == "POST"
            return HtmlResponse(
                url=request.url,
                status=200,
                body=b"<html><body><main><h1>Rendered by fake AdsCrawl</h1></main></body></html>",
                encoding="utf-8",
                request=request,
            )
        assert request.method == "GET"
        return HtmlResponse(
            url=request.url,
            status=200,
            body=b"<html><body><h1>Direct fake response</h1></body></html>",
            encoding="utf-8",
            request=request,
        )

    async def close(self) -> None:
        pass


class EngineIntegrationSpider(Spider):
    name = "engine-integration"
    rendered_items: list[dict[str, Any]] = []
    direct_items: list[dict[str, Any]] = []

    async def start(self):
        yield Request(
            "https://example.com/rendered",
            meta={
                "adscrawl": {
                    "selector": "main",
                    "wait_until": "domcontentloaded",
                    "timeout_ms": 12_345,
                },
                "download_timeout": 17,
            },
            callback=self.parse_rendered,
        )
        yield Request("https://example.org/direct", callback=self.parse_direct)

    def parse_rendered(self, response: HtmlResponse):
        self.__class__.rendered_items.append(
            {
                "url": response.url,
                "title": response.css("h1::text").get(),
                "flags": response.flags,
                "meta": dict(response.request.meta),
            }
        )

    def parse_direct(self, response: HtmlResponse):
        self.__class__.direct_items.append(
            {"url": response.url, "title": response.css("h1::text").get()}
        )


@pytest.mark.asyncio
async def test_async_runner_exercises_adscrawl_middleware_and_direct_download_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run a real crawler/middleware/engine chain against only the fake handler."""

    monkeypatch.setenv("ADSCRAWL_API_KEY", "integration-test-key-not-real")
    sys.modules.setdefault("test_engine", sys.modules[__name__])
    EngineIntegrationSpider.rendered_items = []
    EngineIntegrationSpider.direct_items = []
    settings = {
        "TWISTED_REACTOR_ENABLED": False,
        "ROBOTSTXT_OBEY": False,
        "LOG_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
        "REMOTE_CONTROL_ENABLED": False,
        "ADSCrawl_API_KEY": "unused-setting-must-not-be-used",
        "DOWNLOAD_HANDLERS": {
            "http": "test_engine.FakeHTTPDownloadHandler",
            "https": "test_engine.FakeHTTPDownloadHandler",
        },
        "DOWNLOADER_MIDDLEWARES": {
            "scrapy_adscrawl.middleware.AdsCrawlDownloaderMiddleware": 543,
        },
    }

    runner = AsyncCrawlerRunner(settings)
    await runner.crawl(EngineIntegrationSpider)

    requests = FakeHTTPDownloadHandler.requests
    assert [request.url for request in requests] == [API_ENDPOINT, "https://example.org/direct"]

    api_request = requests[0]
    assert api_request.method == "POST"
    assert api_request.headers.get(b"x-api-key") == b"integration-test-key-not-real"
    assert api_request.headers.get(b"accept") == b"text/html"
    assert api_request.headers.get(b"content-type") == b"application/json"
    assert api_request.meta["_scrapy_adscrawl_api_request"] is True
    assert api_request.meta["dont_retry"] is True
    assert api_request.meta["dont_cache"] is True
    assert api_request.meta["dont_merge_cookies"] is True
    assert api_request.meta["dont_redirect"] is True
    assert api_request.meta["dont_obey_robotstxt"] is True
    assert api_request.meta["download_timeout"] == 17
    assert api_request.meta["download_slot"] != "example.com"
    assert json.loads(api_request.body) == {
        "url": "https://example.com/rendered",
        "contentMode": "html",
        "selector": "main",
        "waitUntil": "domcontentloaded",
        "timeoutMs": 12_345,
    }

    direct_request = requests[1]
    assert direct_request.method == "GET"
    assert "adscrawl" not in direct_request.meta

    assert len(EngineIntegrationSpider.rendered_items) == 1
    rendered = EngineIntegrationSpider.rendered_items[0]
    assert rendered["url"] == "https://example.com/rendered"
    assert rendered["title"] == "Rendered by fake AdsCrawl"
    assert rendered["flags"] == ["adscrawl-rendered"]
    assert rendered["meta"]["adscrawl"] == {
        "selector": "main",
        "wait_until": "domcontentloaded",
        "timeout_ms": 12_345,
    }
    assert rendered["meta"]["download_timeout"] == 17
    assert rendered["meta"]["dont_retry"] is True
    assert rendered["meta"]["dont_cache"] is True
    assert EngineIntegrationSpider.direct_items == [
        {"url": "https://example.org/direct", "title": "Direct fake response"}
    ]
