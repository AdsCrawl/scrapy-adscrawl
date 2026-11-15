# Contributing

This repository is an AdsCrawl-maintained community integration. Contributions should keep the adapter opt-in, preserve Scrapy's normal downloader for unmarked requests, and match the current AdsCrawl API documentation.

Before opening a pull request:

1. Install the test and lint extras with `python -m pip install -e ".[test,lint]"`.
2. Run `python -m pytest --disable-socket --allow-unix-socket` and `python -m ruff check .`.
3. Build the distributions with `python -m build`.
4. Do not place API keys, target-site cookies, user authorization headers, or live response captures in the repository.
5. Do not use a real AdsCrawl key or make production page requests in tests. Keep API behavior covered with mocked Scrapy engine responses.

A rendered request can consume an AdsCrawl credit even if the client later times out. Preserve the no-retry policy unless the AdsCrawl API documents a safe idempotency mechanism.
