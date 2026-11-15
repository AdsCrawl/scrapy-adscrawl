# scrapy-adscrawl

An opt-in Scrapy downloader middleware that sends selected `GET` requests to AdsCrawl's `POST /html` API and returns the rendered HTML as a Scrapy `HtmlResponse`. This is a small adapter for pages where browser-rendered DOM is useful; it does not replace Scrapy's scheduler, selectors, pipelines, or spider logic.

This is an **independent community integration maintained by AdsCrawl**. It is not an official Scrapy project or an upstream Scrapy add-on listing.

## Install

Requires Python 3.11+ and Scrapy 2.20+.

```bash
python -m pip install "git+https://github.com/AdsCrawl/scrapy-adscrawl.git"
```

Set your API key in the process environment. Do not commit it in `settings.py`, a spider, or a workflow:

```bash
export ADSCRAWL_API_KEY="YOUR_ADSCRAWL_API_KEY"
```

Enable the middleware in your Scrapy project settings:

```python
DOWNLOADER_MIDDLEWARES = {
    "scrapy_adscrawl.middleware.AdsCrawlDownloaderMiddleware": 543,
}
```

It is **opt-in per request**. Requests without `meta["adscrawl"]` continue through Scrapy's normal downloader:

```python
import scrapy


class ExampleSpider(scrapy.Spider):
    name = "example"
    custom_settings = {"ROBOTSTXT_OBEY": True}

    async def start(self):
        yield scrapy.Request(
            "https://example.com/",
            meta={
                "adscrawl": {
                    "wait_until": "domcontentloaded",
                    "selector": "main",
                    "timeout_ms": 60_000,
                },
                # Give the AdsCrawl API call enough time to complete.
                "download_timeout": 90,
            },
            callback=self.parse,
        )

    def parse(self, response):
        yield {"title": response.css("h1::text").get(), "html": response.text}
```

Supported `meta["adscrawl"]` options are:

- `selector`: optional CSS selector to wait for and extract.
- `wait_until`: `domcontentloaded`, `load`, or `networkidle`.
- `timeout_ms`: positive integer, at most `3_600_000`.

The adapter sends only the target URL and these options to the fixed AdsCrawl API endpoint. It does not forward Scrapy request headers, authorization, cookies, or request bodies. Only HTTP/HTTPS targets on ports 80 or 443 are accepted; embedded URL credentials, obvious local/internal hostnames, and non-public IP literals are rejected. This local check is not a substitute for server-side URL policy, and the middleware should not be given untrusted target URLs without your own controls.

## Cost, retries, and response behavior

Every opted-in request makes one AdsCrawl HTML API call. AdsCrawl documents that one credit is consumed after request validation and before the task is enqueued. The adapter disables transparent retries and caching for the opted-in request and for its internal API request, to reduce accidental duplicate calls. If a request times out after AdsCrawl accepted it, the result may be uncertain; the adapter deliberately does not retry it automatically.

A successful call becomes an `HtmlResponse` with status `200` and the original Scrapy request URL. AdsCrawl's HTML endpoint returns rendered HTML, but does not provide the target's original HTTP status, response headers, cookies, or final redirect URL in that response. Therefore this adapter does **not** reproduce those HTTP semantics. Non-200 AdsCrawl API replies become `AdsCrawlAPIError` exceptions; their message includes only the status and a validated API error code, not the API body or key.

Scrapy's robots middleware runs before this adapter at the recommended priority; with `ROBOTSTXT_OBEY = True`, the usual robots.txt check remains in place. The internal request to `api.adscrawl.net` skips robots checks, cookies and caching, and uses a separate API download slot. Remote rendering also means Scrapy's per-target-domain download delay, concurrency, and AutoThrottle are not equivalent to throttling requests made directly to each target. Plan AdsCrawl credit usage and concurrency explicitly. Custom middlewares also see internal API requests; audit middleware that logs or changes headers, proxies, or download slots before enabling this adapter.

See the [AdsCrawl HTML API documentation](https://docs.adscrawl.net/api-reference/html) for the full service contract and pricing/credit details.

## Tutorial

See [Render only the JavaScript-heavy pages in a Scrapy crawl](docs/tutorials/selective-browser-rendering.md) for a step-by-step guide, a runnable [Quotes to Scrape spider](examples/quotes_js_spider.py), and offline tests. The tutorial starts by checking whether Scrapy can use the site's data source directly, then opts selected requests into rendered HTML only when that is useful.

## Tests

The suite includes isolated middleware unit tests and an integration test using Scrapy's real crawler/engine with fake HTTP download handlers. Network sockets are disabled (local Unix sockets are allowed for asyncio). It does not use a real AdsCrawl API key or visit AdsCrawl or target sites; this is not a production end-to-end service test.

```bash
python -m pip install -e ".[test,lint]"
python -m pytest --disable-socket --allow-unix-socket
python -m ruff check .
python -m ruff format --check .
python -m pip check
```

## License

MIT. See [LICENSE](LICENSE).
