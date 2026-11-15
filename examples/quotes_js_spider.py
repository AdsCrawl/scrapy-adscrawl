from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import scrapy
from scrapy import Request
from scrapy.http import HtmlResponse


class QuotesJSSpider(scrapy.Spider):
    name = "quotes_js"
    allowed_domains = ["quotes.toscrape.com"]
    start_url = "https://quotes.toscrape.com/js/"
    max_pages = 2
    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOADER_MIDDLEWARES": {
            "scrapy_adscrawl.middleware.AdsCrawlDownloaderMiddleware": 543,
        },
    }

    @staticmethod
    def render_meta() -> dict[str, Any]:
        return {
            "adscrawl": {
                "wait_until": "networkidle",
                "timeout_ms": 60_000,
            },
            "download_timeout": 90,
        }

    async def start(self) -> AsyncIterator[Request]:
        yield Request(
            self.start_url,
            callback=self.parse,
            meta=self.render_meta(),
            cb_kwargs={"page": 1},
        )

    def parse(self, response: HtmlResponse, page: int = 1) -> Iterator[dict[str, Any] | Request]:
        for quote in response.css(".quote"):
            text = quote.css(".text::text").get()
            author = quote.css(".author::text").get()
            if not text or not author:
                self.logger.warning(
                    "Skipping a quote card without text or author: %s", response.url
                )
                continue
            yield {
                "text": text,
                "author": author,
                "tags": quote.css(".tag::text").getall(),
                "source_url": response.url,
            }

        next_page = response.css("li.next a::attr(href)").get()
        if next_page and page < self.max_pages:
            yield response.follow(
                next_page,
                callback=self.parse,
                meta=self.render_meta(),
                cb_kwargs={"page": page + 1},
            )
