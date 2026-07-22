"""Keep Playwright browser binaries out of the frozen core package.

The upstream hook collects every Playwright data file, including an optional
``.local-browsers`` directory. The driver is added explicitly by openakita.spec;
Chromium is installed into the managed user runtime only after user confirmation.
"""

datas = []
