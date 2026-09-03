# MailGrab as an MCP server: lets AI coding agents (Claude Code, Claude Desktop,
# GitHub Copilot, Cursor, Codex CLI, or any other MCP-speaking client) harvest email
# addresses from a website as a tool call, instead of shelling out to MailGrab.py by hand.
#
# Design: each crawl runs MailGrab.py as a *subprocess*, in its own temp working
# directory, rather than importing MailGrab.py's crawl engine in-process. Two reasons:
#   1. MCP's stdio transport reserves this process's stdout exclusively for JSON-RPC
#      messages. MailGrab.py's crawl engine prints styled status lines directly to
#      stdout; running it in-process would corrupt the protocol stream. A subprocess
#      has its own stdout, captured separately, so this can never happen.
#   2. MailGrab.py writes its results to fixed filenames (_results.json, etc.) in the
#      current directory. Running each crawl in its own temp directory means concurrent
#      tool calls can never clobber each other's output.
#
# Run directly: python mailgrab_mcp_server.py
# See the wiki's "MCP Server" page for how to point Claude/Copilot/Cursor/Codex at it.

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

MAILGRAB_PATH = Path(__file__).resolve().parent / "MailGrab.py"

server = FastMCP(
    "mailgrab",
    instructions=(
        "Harvests email addresses (and related LinkedIn/Twitter/contact-page links) "
        "from a website by crawling it. Use the crawl_website tool whenever you need "
        "to find contact emails for a company or site given its URL."
    ),
)


def _buildCommand(url, depth, sameDomain, maxHops, useSitemap, ignoreRobots,
                   verifyMx, delay, timeout, concurrency, userAgent, proxy):
    cmd = [
        sys.executable, str(MAILGRAB_PATH),
        "--url", url,
        "--depth", str(depth),
        "--concurrency", str(concurrency),
        "--delay", str(delay),
        "--timeout", str(timeout),
        "--quiet",
    ]
    if sameDomain:
        cmd.append("--same-domain")
    if maxHops is not None:
        cmd += ["--max-hops", str(maxHops)]
    if useSitemap:
        cmd.append("--use-sitemap")
    if ignoreRobots:
        cmd.append("--ignore-robots")
    if verifyMx:
        cmd.append("--verify-mx")
    if userAgent:
        cmd += ["--user-agent", userAgent]
    if proxy:
        cmd += ["--proxy", proxy]
    return cmd


_WARNING_LINE_RE = re.compile(r"^\s*(\S.*Warning: .*|\s+\S.*)$")


def _cleanSubprocessMessage(result):
    # MailGrab.py's own errors are fixed at the source (its banner strings no longer
    # trigger SyntaxWarnings) -- this is a second, defensive layer in case some other
    # Python/library warning ever gets emitted on stderr and would otherwise bury the
    # one actionable line underneath it.
    combined = (result.stderr or "") + "\n" + (result.stdout or "")
    lines = [
        line for line in combined.splitlines()
        if line.strip() and not _WARNING_LINE_RE.match(line)
    ]
    return "\n".join(lines).strip()


def _computeSubprocessTimeout(depth, concurrency, timeout):
    # A generous safety net, not a precise time model, but it must actually scale with
    # the caller's own concurrency/timeout choices: a low concurrency + a high
    # per-request timeout (both legitimate, e.g. being polite to a slow site) need more
    # wall-clock time than a flat "2s/page" estimate assumes -- an earlier version
    # ignored both and killed perfectly healthy, still-progressing crawls. Budget:
    # (batches needed) * (timeout + retry/backoff headroom) + fixed startup-check
    # overhead, clamped to [60s, 1800s].
    roundsNeeded = -(-depth // max(concurrency, 1))  # ceil(depth / concurrency)
    return min(1800, max(60, int(roundsNeeded * (timeout + 5) + 30)))


def _removeWorkDir(workDir):
    # On Windows, a just-killed (e.g. timed-out) child process can still hold its
    # _MailGrabLog.txt handle open for a brief moment after subprocess.run returns,
    # racing this cleanup; one short retry covers that without adding real complexity.
    shutil.rmtree(workDir, ignore_errors=True)
    if os.path.exists(workDir):
        time.sleep(0.5)
        shutil.rmtree(workDir, ignore_errors=True)


@server.tool()
def crawl_website(
    url: str,
    depth: int = 30,
    same_domain: bool = True,
    max_hops: Optional[int] = None,
    use_sitemap: bool = False,
    ignore_robots: bool = False,
    verify_mx: bool = False,
    delay: float = 0.0,
    timeout: float = 10.0,
    concurrency: int = 10,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
) -> dict:
    """Crawl a website starting at `url` and harvest every email address it finds.

    Follows links breadth-first, up to `depth` total pages (this bounds total
    requests, not link-hops from the seed -- use `max_hops` for that). Respects
    `robots.txt` by default. Returns a structured result even when zero emails are
    found; raises an error only when the crawl itself could not be completed
    (invalid URL, unreachable proxy, out-of-range depth, etc.) -- check the raised
    error message in that case, it's a direct, specific explanation of what failed.

    Defaults are tuned for agent use rather than matching the MailGrab CLI's own
    defaults: `same_domain` defaults to True here (stays on the seed's own site
    unless you explicitly turn it off) and `depth` defaults to 30 (enough for a
    typical company site's About/Team/Contact pages without risking an
    unexpectedly long-running call). Raise `depth` for a broader crawl, or set
    `same_domain=False` to also follow outbound links.

    Args:
        url: The URL to start crawling from. "http://" is added if no scheme is given.
        depth: Maximum total pages to fetch (1-500).
        same_domain: Only follow links on the seed's own domain. Default True.
        max_hops: Cap link-distance from the seed, independent of `depth`. None (default)
            means unlimited -- only `depth` bounds the crawl.
        use_sitemap: Also seed the crawl from the site's sitemap.xml, in addition to
            following links from the seed page. Adds a couple of extra requests.
        ignore_robots: Skip robots.txt checks. Leave this off unless you have a clear
            reason to -- it's off (robots.txt is respected) by default for a reason.
        verify_mx: Drop emails whose domain has no mail server (MX record) configured,
            filtering out obvious typos and dead domains. Requires DNS access and adds
            a little latency.
        delay: Minimum seconds between requests to the same domain (politeness).
        timeout: Per-request timeout in seconds.
        concurrency: Max pages fetched at once.
        user_agent: Custom User-Agent header for the crawl's requests.
        proxy: Proxy URL (http://, https://, socks4://, socks4a://, socks5://, socks5h://).

    Returns:
        A dict with:
          - emails: sorted list of every email address found
          - email_count: len(emails), for convenience
          - scrapped_urls: every URL actually fetched during the crawl
          - url_count: len(scrapped_urls), for convenience
          - sources: {email: [urls it was found on]}
          - social_links: LinkedIn/Twitter/X/Facebook/Instagram and contact-page
            links spotted during the crawl (not crawled themselves, just recorded)
    """
    workDir = tempfile.mkdtemp(prefix="mailgrab_mcp_")
    try:
        command = _buildCommand(
            url, depth, same_domain, max_hops, use_sitemap, ignore_robots,
            verify_mx, delay, timeout, concurrency, user_agent, proxy)
        subprocessTimeout = _computeSubprocessTimeout(depth, concurrency, timeout)
        # A clean exit (0) with no _results.json is a real but rare, purely transient
        # condition -- confirmed tied to a burst of many simultaneous crawl subprocesses
        # occasionally tripping a false negative in MailGrab.py's own startup
        # connectivity self-check, not a problem with the requested crawl itself. One
        # retry in the same (still-empty) workDir is a cheap, safe mitigation; a second
        # failure is treated as real.
        result = None
        for attemptsLeft in (1, 0):
            try:
                result = subprocess.run(
                    command, cwd=workDir, capture_output=True, text=True,
                    timeout=subprocessTimeout, stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    "MailGrab crawl of {} timed out after {}s (depth={}, concurrency={}, "
                    "timeout={}); try a smaller depth, a higher concurrency, or a lower "
                    "per-request timeout".format(
                        url, subprocessTimeout, depth, concurrency, timeout))

            if result.returncode != 0:
                message = _cleanSubprocessMessage(result) or \
                    "MailGrab exited with code {}".format(result.returncode)
                raise RuntimeError("MailGrab crawl of {} failed: {}".format(url, message))

            if os.path.exists(os.path.join(workDir, "_results.json")):
                break
            if not attemptsLeft:
                raise RuntimeError(
                    "MailGrab finished but produced no results for {} ({})".format(
                        url, _cleanSubprocessMessage(result) or "no diagnostic output"))

        with open(os.path.join(workDir, "_results.json"), "r", encoding="utf-8") as f:
            data = json.load(f)

        emails = data.get("emails", [])
        scrappedUrls = data.get("scrappedUrls", [])
        return {
            "emails": emails,
            "email_count": len(emails),
            "scrapped_urls": scrappedUrls,
            "url_count": len(scrappedUrls),
            "sources": data.get("sources", {}),
            "social_links": data.get("socialLinks", []),
        }
    finally:
        _removeWorkDir(workDir)


if __name__ == "__main__":
    server.run(transport="stdio")
