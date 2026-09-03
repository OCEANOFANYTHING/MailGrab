# TODO

Feature ideas for MailGrab, tracked as a checklist. PRs welcome — pick an unchecked item, open an issue to claim it, and reference this list in your PR description.

## MCP server

- [x] Expose MailGrab as an MCP (Model Context Protocol) server so AI coding agents can call it as a tool — `mailgrab_mcp_server.py`, one tool (`crawl_website`), works with Claude Code, Claude Desktop, Cursor, VS Code/Copilot, GitHub Copilot CLI, and Codex CLI (any MCP-speaking client, really). Runs each crawl as a subprocess in an isolated temp directory rather than importing the crawl engine in-process — see `CLAUDE.md`'s "MCP server" section for exactly why. See the wiki's MCP Server page for setup instructions per client.
- [x] Adversarial security review of the MCP server found and fixed: a subprocess timeout formula that ignored `concurrency`/`timeout` and could kill legitimate slow/low-concurrency crawls (now scales with both, see `_computeSubprocessTimeout`); `SyntaxWarning` noise able to bury real error messages (fixed at the root by de-`SyntaxWarning`-ing `MailGrab.py`'s banner strings, plus defensive stdout/stderr filtering in the MCP server); and a rare (~1-3%) transient "clean exit, no results file" race under heavy concurrent process-launch load (mitigated with one automatic retry)
- [ ] `--append`/`--resume`/`--config` are not exposed on the MCP tool (deliberately stateless, one-shot design) — a `work_dir` parameter for callers that want persistence across calls is a reasonable future addition if that need comes up
- [ ] No streaming progress for long crawls — an MCP tool call is request/response; the agent just waits for the full result. MCP does support progress notifications for long-running tools if this becomes worth the added complexity

## Performance

- [x] Concurrent fetching (`ThreadPoolExecutor` or `aiohttp`) instead of one request at a time — BFS batches now fetch concurrently via `ThreadPoolExecutor`, tunable with `MAILGRAB_MAX_WORKERS` (default 10)
- [x] Configurable rate limit / delay between requests — `MAILGRAB_DELAY` (seconds, default 0) spaces out request submissions
- [x] Reuse a `requests.Session()` for connection pooling — one shared `session` used by every crawl request
- [x] Add a request `timeout=` (currently unset, a hung server stalls the whole crawl) — `MAILGRAB_TIMEOUT` (seconds, default 10) passed to every request

## Crawl correctness

- [x] Same-domain scoping option (currently any absolute link found gets queued, so a crawl can wander off-site) — `--same-domain` / `MAILGRAB_SAME_DOMAIN=1`
- [x] URL normalization / dedupe (strip fragments, sort query params) — fragments stripped via `urldefrag`; query-param sorting skipped as unnecessary complexity, add if duplicate-by-query-order urls become a real problem
- [x] Skip non-HTML links before fetching (images, PDFs, CSS/JS, `mailto:`/`tel:`) — extension allowlist plus `mailto:`/`tel:`/`javascript:`/`#` scheme filtering
- [x] Parse `mailto:` hrefs directly as a second, more reliable email source
- [x] De-obfuscation support (`name [at] domain [dot] com`, Cloudflare email-protection spans) — both implemented; the `[at]`/`[dot]` pattern is a heuristic and can rarely false-positive on ordinary prose containing those words
- [x] `robots.txt` awareness (respect by default, `--ignore-robots` to override) — fetched through the same session/timeout so a missing robots.txt can't hang the crawl

## Output

- [x] Append + dedupe across runs instead of overwriting every run — `--append` / `MAILGRAB_APPEND=1`, merges with `_results.json` from the prior run
- [x] CSV/JSON export alongside plain `.txt` — `_emails.csv` and `_results.json` written alongside the existing `.txt` files
- [x] Track which URL each email was found on — in `_results.json`'s `sources` map and `_emails.csv`'s `found_on_url` column

## CLI / config

- [x] `argparse` flags (`--url`, `--depth`, `--input`, `--output`, `--concurrency`) for non-interactive/scriptable use — plus `--delay`, `--timeout`, `--same-domain`, `--ignore-robots`, `--append`, `--user-agent`, `--proxy`; `--url`/`--depth` also skip the final "Press Any Key To Exit" prompt so a fully-flagged run never blocks on stdin (no separate `--output`, since `--input`'s CSV/JSON sibling files aren't independently renameable — say if that's needed)
- [x] Config file support (YAML/JSON) for repeated setups — JSON only via `--config`, pre-filling the same `MAILGRAB_*` env vars (real env vars/CLI flags still win); skipped YAML since JSON needs no extra dependency and covers the same need
- [x] Progress bar tied to actual crawl progress (current one only animates the file-save step) — the old fake one (used only for animating file writes) is gone; `crawlUrls` now prints real `Progress: X/depth Pages Crawled` after each batch

## Robustness / anti-blocking

- [x] Custom `User-Agent` header (default `python-requests` UA gets blocked by many sites) — `--user-agent` / `MAILGRAB_USER_AGENT`
- [x] Retry with backoff on transient errors — `urllib3.Retry` mounted on the session, restricted to 5xx status codes only (retrying on connection/read timeouts too would multiply the wait on a hung server and defeat `MAILGRAB_TIMEOUT`)
- [x] Proxy support (`PySocks` is already a dependency but unused) — `--proxy` / `MAILGRAB_PROXY`, supports `socks5://` via the existing `PySocks` dependency
- [x] Hard cap on total requests regardless of depth, as a safety valve — skipped: `depth` (already capped at 200/500 by the existing input validation) already *is* this cap; a second one would just duplicate it. Say if an independent, non-depth-linked ceiling is wanted

## Code health

- [x] Collapse the duplicated batch-mode/interactive-mode crawl logic in `MailGrab.py` into one function — both modes now call the shared `crawlUrls()`/`_fetchAndExtract()`
- [x] Extract email-regex/link-extraction into standalone, testable functions — done as part of the above; covered by `test_mailgrab.py`

## Known minor limitations (found by adversarial review, accepted rather than fixed)

- [x] A request that times out gets logged as "Connection Error" instead of "Timeout Error" — fixed: `_isDisguisedTimeout()` unwraps the retry adapter's `MaxRetryError` to detect a disguised `ReadTimeoutError`/`ConnectTimeoutError`; explicitly excludes `NewConnectionError`/`NameResolutionError` (connection-refused/DNS-failure are themselves `ConnectTimeoutError` subclasses in urllib3 but are not timeouts — verified empirically, an earlier version of this fix mislabeled a dead/typo'd domain as a timeout)
- [x] `--proxy`/`MAILGRAB_PROXY` isn't validated at startup — fixed: scheme (`http`/`https`/`socks4`/`socks4a`/`socks5`/`socks5h`) and netloc checked upfront, `sys.exit(1)` with a clear message (also printed to stderr so it's visible under `--quiet`) on a malformed value
- [x] `robots.txt` is re-fetched per seed URL in batch mode instead of being cached across seeds on the same domain — fixed: `crawlUrls()` takes a shared `robotsCache` dict, threaded through both call sites the same way as `visited`/`emails`
- [x] The seed URL itself isn't `urldefrag`-ed before being used as the dedup key — fixed: `crawlUrls()` defrags the seed on entry
- [x] 5xx retry `backoff_factor` is a fixed 0.5s, not scaled to `--timeout` — fixed: `max(0.1, min(REQUEST_TIMEOUT / 20, 2.0))`

## Email quality

- [x] Case-insensitive dedup — emails are lowercased at collection time in `_fetchAndExtract()`, and again when loading a prior `_results.json` for `--append`/`--resume` (so a legacy or hand-edited file can't reintroduce case duplicates on merge)
- [x] Filter obvious placeholder/template addresses — `_isPlaceholderEmail()`; the domain-level blocklist is deliberately narrow (`example.com`/`.org`/`.net` — RFC 2606 reserved, can never have real mail — plus `wixpress.com`, a site-builder's own internal domain), since real live domains like `domain.com`/`email.com`/`company.com` legitimately hand out real mailboxes and were wrongly wholesale-blocked in an earlier version; those only ever match as exact `PLACEHOLDER_EMAILS` addresses. Filtered addresses are now reported (`"Filtered N Placeholder Email(s)"`), not silently dropped
- [x] MX-record validation to drop undeliverable domains — `--verify-mx` / `MAILGRAB_VERIFY_MX=1`, one concurrent DNS lookup per unique domain (opt-in: adds latency and needs DNS access). Only a definitive `NXDOMAIN`/`NoAnswer` drops the email; a transient resolver failure (timeout, no nameservers) keeps it and prints a warning instead, so a flaky DNS lookup can't silently erase a real address

## Smarter discovery

- [x] Parse `sitemap.xml` as a faster, more complete alternative to link-crawling — `--use-sitemap` / `MAILGRAB_USE_SITEMAP=1` (opt-in: adds 1-2 extra requests per crawl); `_urlsFromSitemap()` checks `robots.txt`'s `Sitemap:` line(s) and the default `/sitemap.xml` path, and resolves a `<sitemapindex>` (the WordPress/Shopify/most-CMS-default layout) one level deep into its child sitemaps' actual page URLs rather than treating the index's entries as pages themselves. Not yet handled: no size/element cap on a fetched sitemap, so a malicious or huge one could add real latency/memory use before `--depth` gets a chance to bound anything (low real-world risk, not observed to hang in testing)
- [x] Respect the `Crawl-delay` directive in `robots.txt` — `_crawlDelayFor()`; note stdlib `RobotFileParser.crawl_delay()` only recognizes an **integer** delay (`Crawl-delay: 0.3` is silently ignored, `Crawl-delay: 1` works) — a stdlib limitation, not something we can fix without a custom parser
- [x] Real per-seed BFS depth (link-hops from the seed) — `--max-hops` / `MAILGRAB_MAX_HOPS`, tracked alongside each queued url, independent of `--depth`'s total-page cap
- [x] Per-domain rate limiting instead of one global delay — `crawlUrls()` now waits only if the *same domain* was hit recently, so a multi-domain crawl doesn't serialize on one shared delay

## Ops / resilience

- [x] Resume an interrupted crawl — `--resume` / `MAILGRAB_RESUME=1`. Simplified from full frontier persistence: it pre-loads `_results.json` before crawling so already-visited pages are skipped and prior emails are kept, then re-walks from the seed to discover anything new (the seed itself is always re-fetched — there's no persisted link graph, so that's the only way to find new pages at all). Add real frontier serialization if resuming deep into an interrupted crawl without re-walking any of the graph becomes worth the complexity
- [x] `--quiet` / a JSON summary on stdout — `Console(quiet=True)` (Rich's own flag) suppresses normal output; a single `print(json.dumps(...))` with counts and output file names still runs at the end

## Bonus extraction

- [x] Pull social/contact links alongside emails — `_isSocialOrContactLink()` matches LinkedIn/Twitter/X/Facebook/Instagram domains and `/contact`-ish paths, saved in `_results.json`'s `socialLinks` array
