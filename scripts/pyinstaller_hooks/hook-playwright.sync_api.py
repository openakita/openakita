"""Keep Playwright browser binaries out of the frozen core package.

See hook-playwright.async_api.py. BrowserManager uses the same explicitly
bundled driver for both APIs.
"""

datas = []
