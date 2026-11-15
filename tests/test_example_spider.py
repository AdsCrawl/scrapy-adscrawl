from __future__ import annotations

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse

from examples.quotes_js_spider import QuotesJSSpider


@pytest.mark.asyncio
async def test_start_marks_only_demo_request_for_rendering() -> None:
    spider = QuotesJSSpider()
    request = await anext(spider.start())

    assert isinstance(request, Request)
    assert request.url == "https://quotes.toscrape.com/js/"
    assert request.meta["adscrawl"] == {
        "wait_until": "networkidle",
        "timeout_ms": 60_000,
    }
    assert request.meta["download_timeout"] == 90
    assert spider.custom_settings["ROBOTSTXT_OBEY"] is True


@pytest.mark.asyncio
async def test_parse_extracts_quotes_and_opts_in_next_page() -> None:
    spider = QuotesJSSpider()
    html = b"""
    <html><body>
      <div class="quote">
        <span class="text">A useful quote.</span>
        <small class="author">Ada Lovelace</small>
        <div class="tags"><a class="tag">science</a><a class="tag">code</a></div>
      </div>
      <li class="next"><a href="/js/page/2/">Next</a></li>
    </body></html>
    """
    response = HtmlResponse(
        url="https://quotes.toscrape.com/js/",
        body=html,
        encoding="utf-8",
    )

    results = list(spider.parse(response))
    item, next_request = results

    assert item == {
        "text": "A useful quote.",
        "author": "Ada Lovelace",
        "tags": ["science", "code"],
        "source_url": "https://quotes.toscrape.com/js/",
    }
    assert isinstance(next_request, Request)
    assert next_request.url == "https://quotes.toscrape.com/js/page/2/"
    assert next_request.meta["adscrawl"] == spider.render_meta()["adscrawl"]
    assert next_request.meta["download_timeout"] == 90


@pytest.mark.asyncio
async def test_parse_skips_malformed_quote_but_can_follow_next_page() -> None:
    spider = QuotesJSSpider()
    html = b"""
    <html><body>
      <div class="quote"><span class="text">No author</span></div>
      <li class="next"><a href="/js/page/2/">Next</a></li>
    </body></html>
    """
    response = HtmlResponse(
        url="https://quotes.toscrape.com/js/",
        body=html,
        encoding="utf-8",
    )

    results = list(spider.parse(response))
    assert len(results) == 1
    assert isinstance(results[0], Request)


def test_parse_stops_at_page_budget_even_when_next_link_exists() -> None:
    spider = QuotesJSSpider()
    response = HtmlResponse(
        url="https://quotes.toscrape.com/js/page/2/",
        body=b'<li class="next"><a href="/js/page/3/">Next</a></li>',
        encoding="utf-8",
    )
    assert list(spider.parse(response, page=spider.max_pages)) == []


def test_page_counter_is_explicitly_propagated_to_callback() -> None:
    spider = QuotesJSSpider()
    response = HtmlResponse(
        url="https://quotes.toscrape.com/js/",
        body=b'<li class="next"><a href="/js/page/2/">Next</a></li>',
        encoding="utf-8",
    )
    next_request = list(spider.parse(response, page=1))[0]
    assert isinstance(next_request, Request)
    assert next_request.cb_kwargs == {"page": 2}
