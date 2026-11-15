# Render only the JavaScript-heavy pages in a Scrapy crawl

A browser is not the first tool to reach for when a Scrapy selector returns nothing. First inspect the original response and the browser's Network panel. The data may already be in the HTML, embedded in JavaScript, or available from a JSON endpoint. Scrapy's own guide recommends reproducing that data request when practical; a headless browser is an alternative when the request is hard to reproduce or the rendered DOM is what the task needs. [Scrapy: Selecting dynamically-loaded content](https://docs.scrapy.org/en/latest/topics/dynamic-content.html)

This tutorial uses the public [Quotes to Scrape JavaScript demo](https://quotes.toscrape.com/js/) to show a selective pattern: leave ordinary pages on Scrapy's normal downloader, and opt in only the pages that need browser-rendered HTML. It also shows why the demo's sibling `/scroll` page is better scraped through its JSON API when that API is suitable.

The runnable spider is [`examples/quotes_js_spider.py`](../../examples/quotes_js_spider.py). It is covered by offline fixture tests; those tests do not call AdsCrawl or visit the demo site.

## 1. Decide whether rendering is warranted

Start by inspecting what Scrapy receives:

```bash
scrapy fetch --nolog https://quotes.toscrape.com/js/ > response.html
```

If the data is missing, inspect the browser's Network panel for a JSON or other text response that contains it. The Scrapy documentation uses the Quotes to Scrape `/scroll` page to demonstrate this: its quotes are available from a JSON endpoint, so reproducing that endpoint is usually simpler than rendering a browser. [Scrapy developer tools tutorial](https://docs.scrapy.org/en/2.12/topics/developer-tools.html) [Zyte JavaScript tutorial](https://docs.zyte.com/web-scraping/tutorials/main/js.html)

Use rendering when the data is only available after browser-side interactions or when maintaining a site-specific request sequence would cost more than rendering. Rendering is not a promise that a site will allow access, and it does not replace checking the site's terms, robots policy, or your own authorization.

## 2. Install and configure the adapter

The current community adapter requires Python 3.11+ and Scrapy 2.20+. It is an independent AdsCrawl-maintained integration, not an official Scrapy extension or a PyPI release.

```bash
git clone https://github.com/AdsCrawl/scrapy-adscrawl.git
cd scrapy-adscrawl
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Set the API key in the process environment. Use your own key locally; do not put it in the spider, project settings, a checked-in `.env` file, or a workflow:

```bash
export ADSCRAWL_API_KEY="YOUR_ADSCRAWL_API_KEY"
```

The spider enables the middleware and keeps Scrapy's robots check on:

```python
custom_settings = {
    "ROBOTSTXT_OBEY": True,
    "DOWNLOADER_MIDDLEWARES": {
        "scrapy_adscrawl.middleware.AdsCrawlDownloaderMiddleware": 543,
    },
}
```

Scrapy orders downloader middleware numerically; `543` places this adapter in the existing request/response chain. Review your project's other middleware at that order, especially components that log request headers, proxy traffic, or change download slots. [Scrapy downloader middleware](https://docs.scrapy.org/en/latest/topics/downloader-middleware.html)

## 3. Opt in one request at a time

The spider marks only the JavaScript demo request with `meta["adscrawl"]`. Requests without that metadata continue through Scrapy's normal downloader. `examples/quotes_js_spider.py` contains the full runnable implementation; the core request is:

```python
scrapy.Request(
    "https://quotes.toscrape.com/js/",
    callback=self.parse,
    meta={
        "adscrawl": {
            "wait_until": "networkidle",
            "timeout_ms": 60_000,
        },
        # Give Scrapy's internal AdsCrawl API request more time than the
        # browser task timeout.
        "download_timeout": 90,
    },
)
```

This adapter sends a `POST https://api.adscrawl.net/html` with `contentMode: "html"`, the target URL and the supported render options. It does not forward the original Scrapy request's headers, cookies, authorization, or body. See the [AdsCrawl HTML API contract](https://docs.adscrawl.net/api-reference/html).

`networkidle` waits until there have been no network connections for at least 500 ms. It can time out on pages with analytics, long polling, or lazy loading. If the target is ready at an earlier navigation event, choose `domcontentloaded` or `load` instead. [AdsCrawl HTML API: `waitUntil`](https://docs.adscrawl.net/api-reference/html)

The adapter's `selector` option waits for the first matching element and, in HTML mode, returns only that element's HTML. That is useful when you want one section, but a selector such as `.quote` would return a single quote card—not every card on the page. This spider omits `selector` so its callback can parse the full rendered document. [AdsCrawl HTML API: `selector`](https://docs.adscrawl.net/api-reference/html)

## 4. Parse with ordinary Scrapy selectors

Once the browser-rendered HTML arrives, the callback is still regular Scrapy code:

```python
for quote in response.css(".quote"):
    yield {
        "text": quote.css(".text::text").get(),
        "author": quote.css(".author::text").get(),
        "tags": quote.css(".tag::text").getall(),
        "source_url": response.url,
    }
```

The example follows the `li.next` link and explicitly adds the same opt-in metadata to each page. That matters: pagination should not silently switch back to the ordinary downloader or render every request in an unrelated crawl.

Run the spider and write newline-delimited JSON:

```bash
scrapy runspider examples/quotes_js_spider.py -O quotes.jsonl
```

Each item has `text`, `author`, `tags`, and `source_url` fields. The spider can make one AdsCrawl request per page it renders. The example is meant for a public demo; do not use it as permission to crawl another site.

## 5. Keep cost, request semantics, and compliance visible

Every valid AdsCrawl HTML API request consumes one credit before the task is enqueued. A timeout after acceptance can leave the outcome uncertain, so the adapter disables transparent retries rather than risk charging for an automatic duplicate. Read the current [credits and limits](https://docs.adscrawl.net/credits-and-limits) before running a larger crawl, and implement any retry policy at the application level only after deciding how to handle uncertain outcomes.

The adapter returns an `HtmlResponse` with status `200` and the original Scrapy URL when the API succeeds. It does not reproduce the target site's original HTTP status, response headers, cookies, or final redirect URL. Do not use this middleware when your spider depends on those HTTP semantics.

Keep `ROBOTSTXT_OBEY` enabled, honor the target site's terms and access rules, and only crawl pages you are authorized to access. Scrapy's per-target-domain delay and AutoThrottle are not equivalent to throttling the adapter's separate AdsCrawl API request slot. Plan API concurrency and credit use explicitly. Avoid logging the `x-api-key` header or sending credentials/cookies from the target request; the adapter intentionally does not forward them.

## 6. Run the offline checks

From the repository root:

```bash
python -m pip install -e ".[test,lint]"
python -m pytest --disable-socket --allow-unix-socket
python -m ruff check .
python -m ruff format --check .
```

The suite includes a real Scrapy crawler/engine integration test with a fake HTTP download handler. It checks the middleware flow without opening network sockets, using an AdsCrawl key, or visiting the public demo. It verifies code paths and request shape, not production service quality or a live scrape.

## Further reading

- [Scrapy: Selecting dynamically-loaded content](https://docs.scrapy.org/en/latest/topics/dynamic-content.html)
- [Scrapy: Downloader Middleware](https://docs.scrapy.org/en/latest/topics/downloader-middleware.html)
- [Quotes to Scrape JavaScript demo](https://quotes.toscrape.com/js/)
- [AdsCrawl HTML API](https://docs.adscrawl.net/api-reference/html)
- [AdsCrawl credits and limits](https://docs.adscrawl.net/credits-and-limits)
