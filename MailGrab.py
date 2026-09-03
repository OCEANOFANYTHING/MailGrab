# This Program Will Take Input(url) and Will Harvest The Email Addresses From The Website
# This Is The Main File For The Program

# importing required modules. External Modules Used In This Program Can Be Found In The requirements.txt File
import sys
from bs4 import BeautifulSoup as bs
import requests as r
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import ReadTimeoutError, ConnectTimeoutError, NewConnectionError
import urllib.parse as up
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET
import dns.resolver
from collections import deque as dq
import re
from rich.console import Console
from rich.text import Text
import time
import colorama as c
import cursor
import os
import psutil
import platform
import datetime
import warnings
import logging
import argparse
import json
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed

if __name__ == "__main__":
    # CLI flags let the whole thing run non-interactively; every flag is optional and
    # falls back to the existing interactive prompts / env vars when omitted
    argParser = argparse.ArgumentParser(description="MailGrab - a powerful email harvester")
    argParser.add_argument("--url", help="Url to scan (skips the interactive url prompt)")
    argParser.add_argument("--depth", type=int, help="Search depth / max pages to crawl")
    argParser.add_argument("--input", default="_inputUrls.txt", help="Path to a file of seed urls, one per line")
    argParser.add_argument("--concurrency", type=int, help="Max concurrent requests (overrides MAILGRAB_MAX_WORKERS)")
    argParser.add_argument("--delay", type=float, help="Seconds between request submissions (overrides MAILGRAB_DELAY)")
    argParser.add_argument("--timeout", type=float, help="Per-request timeout in seconds (overrides MAILGRAB_TIMEOUT)")
    argParser.add_argument("--same-domain", dest="same_domain", action="store_true", default=None,
                            help="Only follow links on the seed url's own domain")
    argParser.add_argument("--ignore-robots", dest="ignore_robots", action="store_true", default=None,
                            help="Do not respect robots.txt")
    argParser.add_argument("--append", action="store_true", default=None,
                            help="Merge with previous results instead of overwriting them")
    argParser.add_argument("--user-agent", dest="user_agent", help="Custom User-Agent header")
    argParser.add_argument("--proxy", help="Proxy url, e.g. http://host:port or socks5://host:port")
    argParser.add_argument("--config", help="Path to a JSON file providing any of the above as defaults")
    argParser.add_argument("--max-hops", dest="max_hops", type=int,
                            help="Max link-hops from the seed url (distinct from --depth's total-page cap)")
    argParser.add_argument("--use-sitemap", dest="use_sitemap", action="store_true", default=None,
                            help="Also seed the crawl from sitemap.xml (found via robots.txt or the default path)")
    argParser.add_argument("--verify-mx", dest="verify_mx", action="store_true", default=None,
                            help="Drop emails whose domain has no MX record before saving")
    argParser.add_argument("--resume", action="store_true", default=None,
                            help="Skip urls/keep emails already saved in _results.json from a prior run")
    argParser.add_argument("--quiet", action="store_true", default=None,
                            help="Suppress console/banner output; print a single JSON summary at the end")
    cliArgs, _unknownArgs = argParser.parse_known_args()
    NON_INTERACTIVE = cliArgs.url is not None or cliArgs.depth is not None

    # A CLI-driven run must not fall through to an input() call that will never get an
    # answer (and would otherwise hang or crash with EOFError in a script/CI job) -- if
    # one of --url/--depth is given without the other (and, for --depth alone, without a
    # usable seed file to fall back to), fail fast with a clear message instead.
    if cliArgs.url is not None and cliArgs.depth is None:
        print("Error: --depth is required when --url is given (a non-interactive run cannot wait on a prompt)")
        sys.exit(1)
    if cliArgs.depth is not None and cliArgs.url is None:
        seedFileReady = os.path.isfile(cliArgs.input) and os.path.getsize(cliArgs.input) > 0
        if not seedFileReady:
            print("Error: --depth requires --url, or a non-empty --input seed file, for a non-interactive run")
            sys.exit(1)

    # A config file just pre-fills the same env vars the settings already read from,
    # so real env vars (and CLI flags, read after this) still take priority over it
    if cliArgs.config:
        try:
            with open(cliArgs.config, "r", encoding="utf-8") as f:
                for key, value in json.load(f).items():
                    os.environ.setdefault("MAILGRAB_" + key.upper(), str(value))
        except (OSError, json.JSONDecodeError) as e:
            print("Error: Could Not Load --config File '{}': {}".format(cliArgs.config, e))
            sys.exit(1)

    if os.name in ("nt", "dos", "ce"):
        if os.system("ping -n 1 oceanofanything.github.io > nul") == 0:
            pass
        else:
            print("Internet Not Connected")
            exit()
    elif os.name == 'posix':
        if os.system("curl -s oceanofanything.github.io > /dev/null") == 0:
            pass
        else:
            print("Internet Not Connected")
            exit()
    elif os.name == 'darwin':
        if os.system("curl -s oceanofanything.github.io > /dev/null") == 0:
            pass
        else:
            print("Internet Not Connected")
            exit()
    """
    Name - MailGrab
    Description - Email Harvester Tool
    Author - OCEAN OF AMNYTHING
    """
    # Configuring Logging System
    FORMAT = "[%(lineno)d]: %(levelname)s - %(message)s"
    logging.basicConfig(
        level=logging.DEBUG, format=FORMAT, datefmt="[%X]", filename="_MailGrabLog.txt", filemode='w', encoding="utf-8"
    )
    log = logging.getLogger()
    log.removeFilter(sys.stderr)
    log.propagate = False
    date = datetime.datetime.now()
    time1 = date.strftime("%H:%M:%S")
    log.info("Starting MailGrab")
    log.info(
        "Srapped Url List By MailGrab A Powerful Email Scraper Tool Made By OCEAN OF ANYTHING")
    log.info("This File Contains The Urls Scrapped From The Url Provided By The User")
    log.info("https://oceanofanything.github.io")
    log.info("https://github.com/oceanofanything")
    log.info(f"Date Of Last Scan: {date}")
    log.info(f"Time Of Last Scan: {time1}")

    # Creating Universal Signs
    # Primarry  Sign
    sPrimary = Text("[+]")
    sPrimary.stylize("bold #0275d8")
    log.info("Primary Sign: [+]")
    # Warning Sign
    sWarning = Text("[!]")
    sWarning.stylize("bold #eed202")
    log.info("Warning Sign: [!]")
    # Danger Sign
    sDanger = Text("[!]")
    sDanger.stylize("bold #d9534f")
    log.info("Danger Sign: [!]")
    # Success Sign
    sSuccess = Text("[+]")
    sSuccess.stylize("bold #5fd700")
    log.info("Success Sign: [+]")
    # info Sign
    sInfo = Text("[+]")
    sInfo.stylize("bold #5bc0de")
    log.info("Info Sign: [+]")
    # success Input Sign
    sInput = Text("[?] ")
    sInput.stylize("bold #00ff00")
    log.info("Input Sign: [?]")

    # initializing rich console and coloroma
    QUIET_MODE = cliArgs.quiet if cliArgs.quiet is not None else (
        os.environ.get("MAILGRAB_QUIET", "0") == "1")
    console = Console(record=True, quiet=QUIET_MODE)
    c.init(autoreset=True)

    # universal Variables
    __author__ = Text("OCEAN OF ANYTHING")
    __author__.stylize("bold yellow")
    __email__ = Text("oceanofanything@gmail.com")
    __email__.stylize("bold white")

    # Crawl tuning: a CLI flag wins, then the matching env var (itself maybe set by --config), then the default
    MAX_WORKERS = cliArgs.concurrency if cliArgs.concurrency is not None else int(
        os.environ.get("MAILGRAB_MAX_WORKERS", "10"))
    MAX_WORKERS = max(1, MAX_WORKERS)
    REQUEST_DELAY = cliArgs.delay if cliArgs.delay is not None else float(
        os.environ.get("MAILGRAB_DELAY", "0"))
    REQUEST_TIMEOUT = cliArgs.timeout if cliArgs.timeout is not None else float(
        os.environ.get("MAILGRAB_TIMEOUT", "10"))
    SAME_DOMAIN_ONLY = cliArgs.same_domain if cliArgs.same_domain is not None else (
        os.environ.get("MAILGRAB_SAME_DOMAIN", "0") == "1")
    IGNORE_ROBOTS = cliArgs.ignore_robots if cliArgs.ignore_robots is not None else (
        os.environ.get("MAILGRAB_IGNORE_ROBOTS", "0") == "1")
    APPEND_RESULTS = cliArgs.append if cliArgs.append is not None else (
        os.environ.get("MAILGRAB_APPEND", "0") == "1")
    RESUME_MODE = cliArgs.resume if cliArgs.resume is not None else (
        os.environ.get("MAILGRAB_RESUME", "0") == "1")
    USE_SITEMAP = cliArgs.use_sitemap if cliArgs.use_sitemap is not None else (
        os.environ.get("MAILGRAB_USE_SITEMAP", "0") == "1")
    VERIFY_MX = cliArgs.verify_mx if cliArgs.verify_mx is not None else (
        os.environ.get("MAILGRAB_VERIFY_MX", "0") == "1")
    _maxHopsRaw = cliArgs.max_hops if cliArgs.max_hops is not None else os.environ.get("MAILGRAB_MAX_HOPS")
    MAX_HOPS = int(_maxHopsRaw) if _maxHopsRaw not in (None, "") else None
    USER_AGENT = cliArgs.user_agent or os.environ.get(
        "MAILGRAB_USER_AGENT",
        "Mozilla/5.0 (compatible; MailGrab/2.0; +https://github.com/oceanofanything/MailGrab)")
    PROXY_URL = cliArgs.proxy or os.environ.get("MAILGRAB_PROXY")
    if PROXY_URL:
        _proxyParts = up.urlsplit(PROXY_URL)
        _validProxySchemes = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
        if _proxyParts.scheme not in _validProxySchemes or not _proxyParts.netloc:
            _proxyErrorMsg = "Invalid --proxy '{}': expected e.g. http://host:port or socks5://host:port".format(PROXY_URL)
            console.print(sDanger + " " + _proxyErrorMsg)
            print("Error: " + _proxyErrorMsg, file=sys.stderr)  # still visible under --quiet
            log.error("Invalid --proxy: {}".format(PROXY_URL))
            sys.exit(1)

    # Shared HTTP session: connection pool sized to MAX_WORKERS, retries with backoff on
    # transient errors, a real User-Agent, and an optional proxy
    session = r.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    # connect=0/read=0: never retry on a timeout/connection error (that would multiply
    # the wait on a hung server, defeating REQUEST_TIMEOUT) -- only retry bad status codes.
    # backoff_factor scales with REQUEST_TIMEOUT so a low --timeout also bounds retry backoff.
    retryBackoff = max(0.1, min(REQUEST_TIMEOUT / 20, 2.0))
    retryStrategy = Retry(total=3, connect=0, read=0, status=3, backoff_factor=retryBackoff,
                           status_forcelist=[500, 502, 503, 504], allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retryStrategy,
                           pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if PROXY_URL:
        session.proxies = {"http": PROXY_URL, "https": PROXY_URL}

    # Check If The User Is Connected To The Internet

    if r.get("https://oceanofanything.github.io") == None:
        log.error("Program Closing No Internet Connection")
        console.print(sDanger + " You Are Not Connected To The Internet")
        log.error("You Are Not Connected To The Internet")
        console.print(
            sInfo + " Please Connect To The Internet To Continue Using This Program")
        log.info("Please Connect To The Internet To Continue Using This Program")
        console.print(sInfo + " Press Enter To Exit", end="")
        input('')
        log.info("Press Enter To Exit")
        sys.exit()
    elif r.get("https://oceanofanything.github.io") != None:
        pass
    else:
        pass

    # Preventing Creation Of __pycache__ Folder
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    sys.dont_write_bytecode = True
    # chexk if exists __pycache__ folder if yes delete it
    if os.path.exists("__pycache__"):
        log.info("__pycache__ Folder Exists")
        log.info("__pycache__ Folder Exists")
        log.info("Deleting __pycache__ Folder")
        os.system("rm -rf __pycache__")
        log.info("__pycache__ Folder Deleted")
        pass
    else:
        pass
    # Functions And Classes
    # Banner

    class BANNER:
        def __init__(self):
            console.print(
                Text(" _______  _______ _________ _        ", "bold green"), end=""
            )
            console.print(
                Text("                                 ", "bold red"), end="\n")
            console.print(
                Text(r"(       )(  ___  )\__   __/( \       ", "bold green"), end=""
            )
            console.print(
                Text("                                 ", "bold red"), end="\n")
            console.print(
                Text("| () () || (   ) |   ) (   | (       ", "bold green"), end=""
            )
            console.print(
                Text("                                 ", "bold red"), end="\n")
            console.print(
                Text("| || || || (___) |   | |   | |       ", "bold green"), end=""
            )
            console.print(
                Text("  ________            ___.       ", "bold red"), end="\n")
            console.print(
                Text("| |(_)| ||  ___  |   | |   | |       ", "bold green"), end=""
            )
            console.print(
                Text(r" /  _____/___________ \_ |__     ", "bold red"), end="\n")
            console.print(
                Text("| |   | || (   ) |   | |   | |       ", "bold green"), end=""
            )
            console.print(
                Text(r"/   \  __\_  __ \__  \ | __ \    ", "bold red"), end="\n")
            console.print(
                Text(r"| )   ( || )   ( |___) (___| (____/\ ", "bold green"), end=""
            )
            console.print(
                Text(r"\    \_\  \  | \// __ \| \_\ \   ", "bold red"), end="\n")
            console.print(
                Text(r"|/     \||/     \|\_______/(_______/ ", "bold green"), end=""
            )
            console.print(
                Text(r" \______  /__|  (____  /___  /   ", "bold red"), end="\n")
            console.print(
                Text("                                     ", "bold green"), end=""
            )
            console.print(
                Text(r"        \/           \/    \/    ", "bold red"), end="\n")
            console.print(Text("Mail", "bold Green"), end="")
            console.print(Text("Grab", "bold red"), end="")
            console.print(
                Text(" A Powerful Email Harvester Tool", "bold blue"), end="")
            console.print(Text(" By OCEAN OF ANYTHING",
                          "bold yellow"), end="\n")
            console.print(Text("", "bold red"))
            console.print(Text("", "bold red"))

    def ordered_set(in_list):
        out_list = []
        added = set()
        for val in in_list:
            if not val in added:
                out_list.append(val)
                added.add(val)
        return out_list
    HEADER_LINES = (
        'Srapped Url List By MailGrab A Powerful Email Scraper Tool Made By OCEAN OF ANYTHING',
        'This File Contains The Urls Scrapped From The Url Provided By The User',
        'https://oceanofanything.github.io',
        'https://github.com/oceanofanything',
    )

    SKIP_EXTENSIONS = (
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
        ".pdf", ".zip", ".rar", ".7z", ".gz", ".tar",
        ".css", ".js", ".mjs", ".map",
        ".mp3", ".mp4", ".avi", ".mov", ".wav", ".ogg",
        ".woff", ".woff2", ".ttf", ".eot", ".xml",
    )

    # Known template/placeholder addresses that show up verbatim in boilerplate markup
    # (not real contacts) -- filtered out unconditionally, same as SKIP_EXTENSIONS.
    # PLACEHOLDER_DOMAINS is intentionally narrow: only domains that can NEVER have a real
    # mailbox (RFC 2606 reserves example.com/.org/.net for documentation) or that are a site
    # builder's own internal domain (wixpress.com, used for Wix's auto-generated addresses,
    # never a business's real contact). Domains like domain.com/email.com/test.com are real,
    # live services with real mailboxes -- blocking them wholesale would silently discard
    # genuine addresses, so those only appear as exact PLACEHOLDER_EMAILS matches.
    PLACEHOLDER_EMAILS = {
        "example@example.com", "your-email@domain.com", "email@example.com",
        "test@test.com", "user@domain.com", "name@company.com", "you@example.com",
        "someone@example.com", "info@example.com", "your@email.com",
    }
    PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net", "wixpress.com"}

    SOCIAL_DOMAINS = (
        "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    )
    CONTACT_PATH_HINTS = ("contact", "contact-us", "get-in-touch")

    def _isPlaceholderEmail(email):
        email = email.lower()
        if email in PLACEHOLDER_EMAILS:
            return True
        domain = email.rsplit("@", 1)[-1]
        return domain in PLACEHOLDER_DOMAINS

    def _isSocialOrContactLink(link):
        parts = up.urlsplit(link)
        netloc = parts.netloc.lower()
        path = parts.path.lower()
        if any(netloc == d or netloc.endswith("." + d) for d in SOCIAL_DOMAINS):
            return True
        return any(hint in path for hint in CONTACT_PATH_HINTS)

    # Quantifiers are bounded ({1,64}) rather than unbounded (+): an unbounded run of
    # word characters with no "at"/"dot" marker (a base64 blob, minified JS, a long hash)
    # made this catastrophically slow (quadratic) to backtrack over on a real page.
    OBFUSCATED_EMAIL_RE = re.compile(
        r"([a-z0-9._%+-]{1,64})\s*(?:\[at\]|\(at\)|\bat\b)\s*([a-z0-9.-]{1,64})\s*(?:\[dot\]|\(dot\)|\bdot\b)\s*([a-z]{2,24})",
        re.I,
    )

    def _decodeCloudflareEmail(encodedHex):
        # Reverses Cloudflare's "Email Address Obfuscation": each byte XORed with a leading key byte
        key = int(encodedHex[:2], 16)
        return "".join(
            chr(int(encodedHex[i:i + 2], 16) ^ key)
            for i in range(2, len(encodedHex), 2)
        )

    def _findObfuscatedEmails(text):
        found = set()
        for encodedHex in re.findall(r'data-cfemail="([a-f0-9]+)"', text, re.I):
            try:
                found.add(_decodeCloudflareEmail(encodedHex))
            except Exception:
                pass
        for localPart, domain, tld in OBFUSCATED_EMAIL_RE.findall(text):
            found.add(f"{localPart}@{domain}.{tld}")
        return found

    def _isAllowedByRobots(url, robotsCache, session, timeoutSeconds, userAgent):
        parts = up.urlsplit(url)
        origin = "{}://{}".format(parts.scheme, parts.netloc)
        if origin not in robotsCache:
            parser = None
            try:
                response = session.get(origin + "/robots.txt", timeout=timeoutSeconds)
                if response.status_code < 400:
                    parser = RobotFileParser()
                    # Note: stdlib RobotFileParser.crawl_delay() only recognizes an
                    # integer Crawl-delay (it checks .isdigit()) -- a fractional value
                    # like "Crawl-delay: 0.3" is silently ignored, returning None.
                    parser.parse(response.text.splitlines())
            except Exception:
                parser = None
            robotsCache[origin] = parser
        parser = robotsCache[origin]
        return parser is None or parser.can_fetch(userAgent, url)

    def _crawlDelayFor(url, robotsCache, userAgent):
        # robots.txt's own Crawl-delay, if any, for the url's origin (already fetched/cached
        # by _isAllowedByRobots -- returns 0 if unknown/absent/robots.txt was never checked)
        parts = up.urlsplit(url)
        origin = "{}://{}".format(parts.scheme, parts.netloc)
        parser = robotsCache.get(origin)
        if parser is None:
            return 0.0
        delay = parser.crawl_delay(userAgent)
        return float(delay) if delay else 0.0

    def _urlsFromSitemap(seedUrl, session, timeoutSeconds):
        # Best-effort: the Sitemap: line(s) in robots.txt, plus the conventional default path
        parts = up.urlsplit(seedUrl)
        origin = "{}://{}".format(parts.scheme, parts.netloc)
        sitemapUrls = [origin + "/sitemap.xml"]
        try:
            response = session.get(origin + "/robots.txt", timeout=timeoutSeconds)
            if response.status_code < 400:
                for line in response.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sitemapUrls.append(line.split(":", 1)[1].strip())
        except Exception:
            pass
        # A <sitemapindex> lists OTHER sitemap files (not pages) -- the common layout for
        # WordPress/Shopify/etc -- so its <loc> entries must be fetched as sitemaps too,
        # one level deep, rather than treated as page urls directly.
        discovered = []
        toFetch = ordered_set(sitemapUrls)
        fetched = set()
        for _ in range(2):
            nextRound = []
            for sitemapUrl in toFetch:
                if sitemapUrl in fetched:
                    continue
                fetched.add(sitemapUrl)
                try:
                    response = session.get(sitemapUrl, timeout=timeoutSeconds)
                    if response.status_code >= 400:
                        continue
                    root = ET.fromstring(response.content)
                    isIndex = root.tag.lower().endswith("sitemapindex")
                    for element in root.iter():
                        if element.tag.endswith("loc") and element.text:
                            (nextRound if isIndex else discovered).append(element.text.strip())
                except Exception:
                    continue
            if not nextRound:
                break
            toFetch = nextRound
        return discovered

    def _loadPriorResults():
        # The saved state from a previous run (_results.json), used by --append and --resume.
        # Emails are re-lowercased here too: a _results.json from before case-insensitive
        # dedup existed (or hand-edited, or written by another tool) could otherwise
        # reintroduce case-duplicate entries for the same address on merge.
        if not os.path.exists("_results.json"):
            return set(), set(), {}
        with open("_results.json", "r", encoding="utf-8") as f:
            prior = json.load(f)
        priorScrapped = set(prior.get("scrappedUrls", []))
        priorEmails = {e.lower() for e in prior.get("emails", [])}
        priorSources = {}
        for email, urls in prior.get("sources", {}).items():
            priorSources.setdefault(email.lower(), set()).update(urls)
        return priorScrapped, priorEmails, priorSources

    def _domainHasMX(domain, mxCache, timeoutSeconds):
        if domain not in mxCache:
            try:
                dns.resolver.resolve(domain, "MX", lifetime=timeoutSeconds)
                mxCache[domain] = True
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                # definitive: the domain doesn't exist, or exists with no MX record
                mxCache[domain] = False
            except Exception as e:
                # a transient resolver failure (timeout, no nameservers reachable, ...) is
                # NOT proof the domain has no MX record -- keep the email rather than risk
                # silently dropping a real address because of a flaky DNS lookup
                console.print(
                    sWarning + " Could Not Verify MX For {} (Keeping It): {}".format(domain, e))
                log.warning("MX lookup failed for {}: {}".format(domain, e))
                mxCache[domain] = True
        return mxCache[domain]

    def _filterByMX(emails, emailSources, timeoutSeconds, maxWorkers):
        # Drops emails whose domain has no MX record; one DNS lookup per unique domain,
        # done concurrently since each lookup is I/O-bound like a fetch
        mxCache = {}
        domains = {email.rsplit("@", 1)[-1] for email in emails if "@" in email}
        with ThreadPoolExecutor(max_workers=maxWorkers) as executor:
            futures = {executor.submit(_domainHasMX, d, mxCache, timeoutSeconds): d for d in domains}
            for future in as_completed(futures):
                future.result()
        keep = {email for email in emails if mxCache.get(email.rsplit("@", 1)[-1], False)}
        dropped = emails - keep
        if dropped:
            console.print(sInfo + " Dropped {} Email(s) With No MX Record".format(len(dropped)))
            log.info("Dropped {} emails with no MX record: {}".format(len(dropped), sorted(dropped)))
        return keep, {email: urls for email, urls in emailSources.items() if email in keep}

    def saveResults(scrappedUrls, emails, emailSources, socialLinks, appendMode, verifyMx, timeoutSeconds, maxWorkers):
        # Writes _emails.txt/_scrappedUrls.txt (legacy plain format), _emails.csv, and
        # _results.json; in append mode, merges with _results.json from a prior run first
        emails = set(emails)
        scrappedUrls = set(scrappedUrls)
        socialLinks = set(socialLinks)
        emailSources = {email: set(urls) for email, urls in emailSources.items()}
        if appendMode and os.path.exists("_results.json"):
            try:
                priorScrapped, priorEmails, priorSources = _loadPriorResults()
                emails |= priorEmails
                scrappedUrls |= priorScrapped
                for email, urls in priorSources.items():
                    emailSources.setdefault(email, set()).update(urls)
            except Exception as e:
                console.print(
                    sWarning + " Could Not Load Prior Results For --append (starting fresh): {}".format(e))
                log.warning("Could Not Load Prior Results For Append Mode: {}".format(e))

        if verifyMx and emails:
            emails, emailSources = _filterByMX(emails, emailSources, timeoutSeconds, maxWorkers)

        date = datetime.datetime.now()
        header = list(HEADER_LINES) + [f'Date Of Last Scan: {date}', f'Time Of Last Scan: {date.strftime("%H:%M:%S")}']
        emailList = header + ['Scrapped Emails:', ' '] + sorted(emails)
        scrappedUrlList = header + ['Scrapped Urls:', ' '] + sorted(scrappedUrls)

        with open("_emails.txt", "w", encoding="utf-8") as f:
            for item in emailList:
                f.write("%s\n" % item)
        with open("_scrappedUrls.txt", "w", encoding="utf-8") as f:
            for item in scrappedUrlList:
                f.write("%s\n" % item)
        with open("_emails.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["email", "found_on_url"])
            for email in sorted(emails):
                for sourceUrl in sorted(emailSources.get(email, set())) or [""]:
                    writer.writerow([email, sourceUrl])
        with open("_results.json", "w", encoding="utf-8") as f:
            json.dump({
                "emails": sorted(emails),
                "scrappedUrls": sorted(scrappedUrls),
                "sources": {email: sorted(urls) for email, urls in emailSources.items()},
                "socialLinks": sorted(socialLinks),
            }, f, indent=2)

        return emailList, scrappedUrlList, emails, scrappedUrls

    def printQuietSummary(emails, scrappedUrls):
        # --quiet suppresses all normal console output via Console(quiet=True); this is the
        # one thing still printed, a plain stdout line meant for piping into other tools
        print(json.dumps({
            "emailCount": len(emails),
            "urlCount": len(scrappedUrls),
            "outputFiles": ["_emails.txt", "_scrappedUrls.txt", "_emails.csv", "_results.json"],
        }))

    def _isDisguisedTimeout(connectionError):
        # Mounting a Retry-based HTTPAdapter makes requests wrap a read/connect timeout's
        # MaxRetryError as ConnectionError instead of Timeout; unwrap it to log the real cause.
        # NewConnectionError (connection refused) and its NameResolutionError subclass (DNS
        # failure) are themselves ConnectTimeoutError subclasses in urllib3's hierarchy, but
        # are genuine connection failures, not timeouts -- exclude them explicitly, verified
        # empirically (a nonexistent domain raises NameResolutionError, not a bare
        # ConnectTimeoutError, which a real connect-timeout does raise).
        cause = connectionError.args[0] if connectionError.args else None
        reason = getattr(cause, "reason", None)
        if isinstance(reason, NewConnectionError):
            return False
        return isinstance(reason, (ReadTimeoutError, ConnectTimeoutError))

    def _fetchAndExtract(url, session, timeoutSeconds, count):
        # Fetches one url and returns (emails found, links discovered); errors are logged and swallowed
        bn = Text(f"[{count}]")
        bn.stylize("bold #5bc0de")
        pText = Text(" Processing: ")
        pText.stylize("bold #d700d7")
        cUrl = Text(url)
        cUrl.stylize("bold #0087d7")
        console.print(bn + pText + cUrl)
        eUrl = Text(url)
        eUrl.stylize("bold #d75f00")
        try:
            response = session.get(url, timeout=timeoutSeconds)
        except r.exceptions.HTTPError as e:
            console.print(sDanger + " HTTP Error: {}".format(e))
            log.error("HTTP Error: {}".format(e))
            log.exception("HTTP Error: {}".format(e))
            console.print(sInfo + " Skipping Url: {}".format(eUrl))
            log.info("Skipping Url: {}".format(eUrl))
            return set(), [], set()
        except r.exceptions.ConnectionError as e:
            if _isDisguisedTimeout(e):
                console.print(sDanger + " Timeout Error: {}".format(e))
                log.error("Timeout Error: {}".format(e))
                log.exception("Timeout Error: {}".format(e))
            else:
                console.print(sDanger + " Connection Error: {}".format(e))
                log.error("Connection Error: {}".format(e))
                log.exception("Connection Error: {}".format(e))
            console.print(sInfo + " Skipping Url: {}".format(eUrl))
            log.info("Skipping Url: {}".format(eUrl))
            return set(), [], set()
        except r.exceptions.Timeout as e:
            console.print(sDanger + " Timeout Error: {}".format(e))
            log.error("Timeout Error: {}".format(e))
            log.exception("Timeout Error: {}".format(e))
            console.print(sInfo + " Skipping Url: {}".format(eUrl))
            log.info("Skipping Url: {}".format(eUrl))
            return set(), [], set()
        except r.exceptions.TooManyRedirects as e:
            console.print(sDanger + " Too Many Redirects: {}".format(e))
            log.error("Too Many Redirects: {}".format(e))
            log.exception("Too Many Redirects: {}".format(e))
            console.print(sInfo + " Skipping Url: {}".format(eUrl))
            log.info("Skipping Url: {}".format(eUrl))
            return set(), [], set()
        except r.exceptions.RequestException as e:
            console.print(sDanger + " Request Exception: {}".format(e))
            log.error("Request Exception: {}".format(e))
            log.exception("Request Exception: {}".format(e))
            console.print(sInfo + " Skipping Url: {}".format(eUrl))
            log.info("Skipping Url: {}".format(eUrl))
            return set(), [], set()
        except Exception as e:
            console.print(sDanger + " Exception: {}".format(e))
            log.error("Exception: {}".format(e))
            log.exception("Exception: {}".format(e))
            console.print(sInfo + " Skipping Url: {}".format(eUrl))
            log.info("Skipping Url: {}".format(eUrl))
            return set(), [], set()
        # Quantifiers are bounded (RFC-realistic max lengths) rather than unbounded (+):
        # unbounded here caused the same catastrophic backtracking on a long run of word
        # characters with no "@" (minified JS, a base64 blob, a long hash) as the
        # de-obfuscation regex above, hanging the crawl on a single such page.
        newEmails = set(
            re.findall(
                r"[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,255}\.[a-z]{2,24}", response.text, re.I)
        )
        newEmails |= _findObfuscatedEmails(response.text)
        # Emails are practically always treated case-insensitively; normalize to lowercase
        # so "User@Example.com" and "user@example.com" dedup as the same address
        newEmails = {e.lower() for e in newEmails}
        parts = up.urlsplit(url)
        baseUrl = "{}://{}".format(parts.scheme, parts.netloc)
        path = url[: url.rfind("/") + 1] if "/" in parts.path else url
        soup = bs(response.text, features="lxml")
        discoveredLinks = []
        socialLinks = set()
        for anchor in soup.find_all("a"):
            link = anchor.attrs["href"] if "href" in anchor.attrs else ""
            linkLower = link.lower()
            if linkLower.startswith("mailto:"):
                newEmails.add(link[len("mailto:"):].split("?")[0].strip().lower())
                continue
            if not link or linkLower.startswith(("tel:", "javascript:", "#")):
                continue
            if link.startswith("/"):
                link = baseUrl + link
            elif not link.startswith("http"):
                link = path + link
            link = up.urldefrag(link)[0]
            if _isSocialOrContactLink(link):
                socialLinks.add(link)
            if up.urlsplit(link).path.lower().endswith(SKIP_EXTENSIONS):
                continue
            discoveredLinks.append(link)
        newEmails.discard("")
        placeholders = {e for e in newEmails if _isPlaceholderEmail(e)}
        if placeholders:
            console.print(sInfo + " Filtered {} Placeholder Email(s): {}".format(
                len(placeholders), ", ".join(sorted(placeholders))))
            log.info("Filtered placeholder emails: {}".format(sorted(placeholders)))
        newEmails -= placeholders
        return newEmails, discoveredLinks, socialLinks

    def crawlUrls(seedUrl, depth, session, maxWorkers, delaySeconds, timeoutSeconds,
                  sameDomainOnly=False, ignoreRobots=False, userAgent="",
                  visited=None, emails=None, emailSources=None, socialLinks=None,
                  robotsCache=None, maxHops=None, useSitemap=False):
        # BFS-crawls seedUrl up to `depth` pages total, fetching each batch concurrently.
        # visited/emails/emailSources/socialLinks/robotsCache, when passed in, are mutated
        # in place so a batch run over multiple seeds shares dedup/cache state and partial
        # progress survives Ctrl-C. Each queued item is (url, hopCount) so maxHops can cap
        # link-distance from the seed independently of depth's total-page-count cap.
        seedUrl = up.urldefrag(seedUrl)[0]
        scrappedUrls = visited if visited is not None else set()
        emails = emails if emails is not None else set()
        emailSources = emailSources if emailSources is not None else {}
        socialLinks = socialLinks if socialLinks is not None else set()
        robotsCache = robotsCache if robotsCache is not None else {}
        lastRequestAt = {}  # domain -> time of the last request submitted to it (main thread only)

        urls = dq([(seedUrl, 0)])
        if useSitemap and (maxHops is None or maxHops >= 1):
            for sitemapUrl in _urlsFromSitemap(seedUrl, session, timeoutSeconds):
                sitemapUrl = up.urldefrag(sitemapUrl)[0]
                if sitemapUrl not in scrappedUrls:
                    urls.append((sitemapUrl, 1))

        seedDomain = up.urlsplit(seedUrl).netloc
        count = 0
        while urls and count < depth:
            batchSize = min(maxWorkers, depth - count, len(urls))
            rawBatch = [urls.popleft() for _ in range(batchSize)]
            batch = []
            for url, hop in rawBatch:
                scrappedUrls.add(url)
                if not ignoreRobots and not _isAllowedByRobots(url, robotsCache, session, timeoutSeconds, userAgent):
                    console.print(sWarning + " Blocked By robots.txt, Skipping: " + url)
                    log.info("Blocked By robots.txt: {}".format(url))
                    continue
                batch.append((url, hop))
            if not batch:
                continue
            with ThreadPoolExecutor(max_workers=maxWorkers) as executor:
                futures = {}
                for url, hop in batch:
                    count += 1
                    # Per-domain rate limit: wait only if THIS domain was hit recently,
                    # so a multi-domain crawl doesn't pay one global domain's delay.
                    # robots.txt's own Crawl-delay (if larger) always takes priority.
                    domain = up.urlsplit(url).netloc
                    effectiveDelay = max(delaySeconds, _crawlDelayFor(url, robotsCache, userAgent))
                    if effectiveDelay:
                        waitFor = effectiveDelay - (time.time() - lastRequestAt.get(domain, 0))
                        if waitFor > 0:
                            time.sleep(waitFor)
                    lastRequestAt[domain] = time.time()
                    futures[executor.submit(
                        _fetchAndExtract, url, session, timeoutSeconds, count)] = (url, hop)
                for future in as_completed(futures):
                    sourceUrl, hop = futures[future]
                    newEmails, discoveredLinks, newSocialLinks = future.result()
                    emails.update(newEmails)
                    socialLinks.update(newSocialLinks)
                    for email in newEmails:
                        emailSources.setdefault(email, set()).add(sourceUrl)
                    if maxHops is not None and hop >= maxHops:
                        continue
                    for link in discoveredLinks:
                        if sameDomainOnly and up.urlsplit(link).netloc != seedDomain:
                            continue
                        if link not in scrappedUrls:
                            urls.append((link, hop + 1))
            console.print(sInfo + " Progress: {}/{} Pages Crawled".format(count, depth))
            log.info("Progress: {}/{} Pages Crawled".format(count, depth))
        return scrappedUrls, emails, emailSources, socialLinks

    def MAILGRAB():
        if 1 < 2:
            # Now Check If The _inputUrls.txt File Is Empty Or Not. If Empty Then Pass The Program
            # if exists _inputUrls.txt file then open it
            f = open(cliArgs.input, "r")
            # Read The Content Of The Text File And Append It To An Empty List
            # Creating A Empty String For Store The Urls
            urlListFromTxtFile = []
            # Now Reading The Content Of The Text File And Append It To The List By Replacing \n With ,
            for line in f:
                urlListFromTxtFile.append(line.strip())
            # Closing The File
            f.close()
            # Replacing Blank Lines And Spaces With , From urlListFromTxtFile
            if '' in urlListFromTxtFile:
                urlListFromTxtFile.remove('')
            elif ' ' in urlListFromTxtFile:
                urlListFromTxtFile.remove(' ')
                urlListFromTxtFile.remove('')
            elif '\n' in urlListFromTxtFile:
                urlListFromTxtFile.remove('\n')
                urlListFromTxtFile.remove('')
            elif '\r' in urlListFromTxtFile:
                urlListFromTxtFile.remove('\r')
                urlListFromTxtFile.remove('')
            elif '\t' in urlListFromTxtFile:
                urlListFromTxtFile.remove('\t')
                urlListFromTxtFile.remove('')
            elif '\r\n' in urlListFromTxtFile:
                urlListFromTxtFile.remove('\r\n')
                urlListFromTxtFile.remove('')
            elif '\t\n' in urlListFromTxtFile:
                urlListFromTxtFile.remove('\t\n')
                urlListFromTxtFile.remove('')
            elif "" in urlListFromTxtFile:
                urlListFromTxtFile.remove("")
            else:
                pass
            urlListFromTxtFile = [x.strip()
                                  for x in urlListFromTxtFile if x.strip()]
            urlListFromTxtFile = ordered_set(urlListFromTxtFile)
            # Now Printing The Banner
            try:
                BANNER()
            except Exception as e:
                console.print(sDanger + "Error: {}".format(e))
                console.print(sInfo + "Error While Printing The Banner")
                log.warning("Error: {}".format(e))
                log.info("Error While Printing The Banner")
            # Now Take Each Url From The List And Harvest The Email From The Url and The url's subdomains
            startTimeg = time.time()
            if cliArgs.depth is not None:
                depth = cliArgs.depth
                if depth <= 0 or depth > 200:
                    console.print(sDanger + " --depth Must Be Between 1 And 200")
                    print("Error: --depth Must Be Between 1 And 200", file=sys.stderr)  # still visible under --quiet
                    log.error("Invalid --depth: {}".format(depth))
                    sys.exit(1)
            else:
                # Taking Input From The User For The Depth Of The Search
                console.print(
                    sInput + "Enter The Search Depth For Each Url (Max Is 200): ", end="")
                depthInput = input()
                log.info("User Inputted Depth")
                while True:
                    while True:
                        # checking the input is float or not
                        if type(depthInput) == float:
                            console.print(sWarning + " Input Cannot Be A Float!")
                            log.warn("User Entered Invalid Depth")
                            console.print(
                                sInput + "Enter The Search Depth For Each Url (Max Is 200): ", end="")
                            depthInput = input()
                            log.info("User Inputted Depth")
                        else:
                            break
                    while True:
                        # checking the input is digit or not, if yes breal while loop
                        if depthInput.isdigit():
                            depthInput = int(depthInput)
                            break
                        # checking the input is digit or not, if not continuing while loop
                        elif not depthInput.isdigit():
                            console.print(sWarning + " Input Must Be A Number!")
                            log.warn("User Entered Invalid Depth")
                            console.print(
                                sInput + "Enter The Search Depth For Each Url (Max Is 200): ", end="")
                            depthInput = input()
                            log.info("User Inputted Depth")
                        # checking the input is digit or not, else continuing while loop
                        else:
                            console.print(sWarning + " Input Must Be A Number!")
                            log.warn("User Entered Invalid Depth")
                            console.print(
                                sInput + "Enter The Search Depth For Each Url (Max Is 200): ", end="")
                            depthInput = input()
                            log.info("User Inputted Depth")
                    if depthInput < 0:
                        console.print(
                            sWarning + " Search Depth Cannot Be Negative")
                        log.warn("User Entered Invalid Depth")
                        console.print(
                            sInput + "Enter The Search Depth For Each Url (Max Is 200): ", end="")
                        depthInput = input()
                        log.info("User Inputted Depth")
                        # depth = depthInput
                    elif depthInput == 0:
                        console.print(sWarning + " Search Depth Cannot Be Zero")
                        log.warn("User Entered Invalid Depth")
                        console.print(
                            sInput + "Enter The Search Depth For Each Url (Max Is 200): ", end="")
                        depthInput = input()
                        log.info("User Inputted Depth")
                        # depth = depthInput
                    elif depthInput > 200:
                        console.print(
                            sWarning
                            + " To Prevent Crashing The Program, Search Depth Cannot Be Greater Than 200"
                        )
                        log.warn("User Entered Invalid Depth")
                        console.print(
                            sInput + "Enter The Search Depth For Each Url (Max Is 200): ", end="")
                        depthInput = input()
                        log.info("User Inputted Depth")
                        # depth = depthInput
                    else:
                        depth = depthInput
                        break
                    depth = int(depthInput)
                    cursor.hide()
            console.print(sSuccess + " Search Depth Is Set To: " + str(depth))
            log.info("Search Depth Set To: " + str(depth))
            scrappedUrls = set()
            emails = set()
            emailSources = {}
            socialLinks = set()
            robotsCache = {}
            if RESUME_MODE:
                try:
                    priorScrapped, priorEmails, priorSources = _loadPriorResults()
                    scrappedUrls |= priorScrapped
                    emails |= priorEmails
                    for email, urls_ in priorSources.items():
                        emailSources.setdefault(email, set()).update(urls_)
                except Exception as e:
                    console.print(sWarning + " Could Not Load Prior Results For --resume: {}".format(e))
                    log.warning("Could Not Load Prior Results For Resume: {}".format(e))
            emailCount = 0
            startTime = time.time()
            try:
                for url in urlListFromTxtFile:
                    # Removing white spaces from the url
                    url = url.replace(" ", "")
                    log.info("Removed White Spaces From: {}".format(url))
                    # Now Check If The Url Is Valid Or Not
                    if not url.startswith("http"):
                        outputUrl = "http://" + url
                    else:
                        outputUrl = url
                    print("\n\n")
                    console.print(sSuccess + " Base Url Is Set To:  " + url)
                    log.info("Base Url Set To: " + url)
                    startTimeg = time.time()
                    crawlUrls(
                        outputUrl, depth, session, MAX_WORKERS, REQUEST_DELAY, REQUEST_TIMEOUT,
                        sameDomainOnly=SAME_DOMAIN_ONLY, ignoreRobots=IGNORE_ROBOTS, userAgent=USER_AGENT,
                        visited=scrappedUrls, emails=emails, emailSources=emailSources,
                        socialLinks=socialLinks, robotsCache=robotsCache, maxHops=MAX_HOPS,
                        useSitemap=USE_SITEMAP)
            except KeyboardInterrupt:
                console.print(
                    sDanger + " Closing! Because User Interrupted The Program")
                log.error("Closing! Because User Interrupted The Program")
                console.print(sInfo + " Keyboard Interrupt Detected")
                log.info("Keyboard Interrupt Detected")
            print("\n\n\n")
            #  Now Showing The Time Taken To Collect Emails And Urls
            try:
                console.print(
                    sSuccess +
                    " Time Taken To Collect Emails: {} MiliSeconds".format(
                        time.time() - startTimeg)
                )
                log.info("Time Taken To Collect Emails: {}".format(
                    time.time() - startTimeg))
                console.print(
                    sSuccess +
                    " Time Taken To Collect Sub Urls: {} MiliSeconds".format(
                        time.time() - startTimeg)
                )
                log.info("Time Taken To Collect Sub Urls: {}".format(
                    time.time() - startTimeg))
            except Exception as e:
                console.print(sDanger + " Error: {}".format(e))
                log.error("Error: {}".format(e))
                console.print(
                    sInfo + " An Error Ocurred While Printing The Time Taken\n")
                log.info("An Error Ocurred While Printing The Time Taken\n")

            try:
                print(" ")
                print(" ")
                for mail in sorted(emails):
                    emailCount += 1
                    ben = Text(f"[{emailCount}]")
                    ben.stylize("bold #5fd700")
                    cEmails = Text(mail)
                    cEmails.stylize("bold #a8a8a8")
                    console.print(ben + "Email: " + cEmails)
            except Exception as e:
                console.print(sDanger + " Error: {}".format(e))
                log.error("Error: {}".format(e))
                console.print(
                    sInfo + " An Error Ocurred While Printing The Emails")
                log.info("An Error Ocurred While Printing The Emails")

            # Saving Emails And Urls (plain text, csv, and json; merges with prior results in append mode)
            saveFailed = False
            try:
                console.print(
                    sInfo + "Saving Scrapped Emails And Urls (_emails.txt, _scrappedUrls.txt, _emails.csv, _results.json)\n")
                emailList, scrappedUrlList, savedEmails, savedScrappedUrls = saveResults(
                    scrappedUrls, emails, emailSources, socialLinks, APPEND_RESULTS or RESUME_MODE,
                    VERIFY_MX, REQUEST_TIMEOUT, MAX_WORKERS)
                console.print(sSuccess + " Scrapped Emails And Urls Are Successfully Saved!\n\n")
            except Exception as e:
                saveFailed = True
                console.print(sDanger + " Error: {}".format(e))
                print("Error: An Error Ocurred While Saving The Emails And Urls: {}".format(e),
                      file=sys.stderr)  # still visible under --quiet, and this failure changes the exit code
                log.error("Error: {}".format(e))
                console.print(
                    sInfo + " An Error Ocurred While Saving The Emails And Urls")
                log.info("An Error Ocurred While Saving The Emails And Urls")
                savedEmails, savedScrappedUrls = emails, scrappedUrls

            # Now Showing How Many Emails And Urls Are Collected (post-append-merge totals)
            try:
                console.print(
                    sSuccess + " Number Of Scrapped Emails: {}".format(len(savedEmails)))
                log.info("Number Of Scrapped Emails: {}".format(len(savedEmails)))
                console.print(
                    sSuccess + " Number Of Scrapped Urls: {}".format(len(savedScrappedUrls)))
                log.info("Number Of Scrapped Urls: {}".format(
                    len(savedScrappedUrls)))
            except Exception as e:
                console.print(sDanger + " Error: {}".format(e))
                log.error("Error: {}".format(e))
                console.print(
                    sInfo + " An Error Ocurred While Printing The Emails And Urls")
                log.info("An Error Ocurred While Printing The Emails And Urls")
            #  Now Showing The Time Taken To Collect Emails And Urls
            try:
                console.print(
                    sSuccess +
                    " Time Taken To Collect Emails: {} MiliSeconds".format(
                        time.time() - startTime)
                )
                log.info("Time Taken To Collect Emails: {}".format(
                    time.time() - startTime))
                console.print(
                    sSuccess +
                    " Time Taken To Collect Sub Urls: {} MiliSeconds".format(
                        time.time() - startTime)
                )
                log.info("Time Taken To Collect Sub Urls: {}".format(
                    time.time() - startTime))
            except Exception as e:
                console.print(sDanger + " Error: {}".format(e))
                log.error("Error: {}".format(e))
                console.print(
                    sInfo + " An Error Ocurred While Printing The Time Taken\n")
                log.info("An Error Ocurred While Printing The Time Taken\n")
            # showing How Much system resource is using
            try:
                console.print(sSuccess + " Current Usage Of System Resource:")
                log.info("Current Usage Of System Resource:")
                console.print(
                    sSuccess + " CPU: {}%".format(psutil.cpu_percent()))
                log.info("CPU: {}%".format(psutil.cpu_percent()))
                console.print(
                    sSuccess + " RAM: {}%".format(psutil.virtual_memory()[2]))
                log.info("RAM: {}%".format(psutil.virtual_memory()[2]))
                console.print(
                    sSuccess + " Disk: {}%".format(psutil.disk_usage("/")[3]))
                log.info("Disk: {}%".format(psutil.disk_usage("/")[3]))
                console.print(
                    sSuccess + " Network: {}%".format(psutil.net_io_counters()[0]))
                log.info("Network: {}%".format(psutil.net_io_counters()[0]))
                console.print(
                    sSuccess + " Network Speed: {} kbps".format(psutil.net_io_counters()[1]))
                log.info("Network Speed: {} kbps".format(
                    psutil.net_io_counters()[1]))

            except Exception as e:
                console.print(sDanger + " Error: {}".format(e))
                log.error("Error: {}".format(e))
                console.print(
                    sInfo + " An Error Ocurred While Printing The System Resource Used By The Program\n")
                log.info(
                    "An Error Ocurred While Printing The System Resource Used By The Program\n")
            # Showing The os Information
            try:
                console.print(
                    sSuccess + " OS Information: {}".format(platform.platform()))
                log.info("OS Information: {}".format(platform.platform()))
            except Exception as e:
                console.print(sDanger + " Error: {}".format(e))
                log.error("Error: {}".format(e))
                console.print(
                    sInfo + " An Error Ocurred While Printing The OS Information\n")
                log.info("An Error Ocurred While Printing The OS Information\n")
            # showing the name of the os
            try:
                console.print(
                    sSuccess + " OS Name: {}".format(platform.system()))
                log.info("OS Name: {}".format(platform.system()))
            except Exception as e:
                console.print(sDanger + " Error: {}".format(e))
                log.error("Error: {}".format(e))
                console.print(
                    sInfo + " An Error Ocurred While Printing The OS Name\n")
                log.info("An Error Ocurred While Printing The OS Name\n")
            # showing the Current Date And Time in a formal way
            try:
                console.print(
                    sSuccess + " Current Date And Time: {}".format(datetime.datetime.now()))
                log.info("Current Date And Time: {}".format(time.ctime()))
            except Exception as e:
                console.print(sDanger + " Error: {}".format(e))
                log.error("Error: {}".format(e))
                console.print(
                    sInfo + " An Error Ocurred While Printing The Current Date And Time\n")
                log.info(
                    "An Error Ocurred While Printing The Current Date And Time\n")

            console.print(
                sSuccess + " All Scrapped Emails And Urls Are Saved In _emails.txt And _scrappedUrls.txt\n")
            log.info(
                "All Scrapped Emails And Urls Are Saved In _emails.txt And _scrappedUrls.txt\n")

            console.print(sSuccess + " Thanks For Using MailGrab!")
            console.print(sSuccess + " Made By: " + __author__)
            console.print(
                sInfo + " If You Have Any Suggestion Or Bug Please Contact Me At: " + __email__)
            cursor.show()
            if QUIET_MODE:
                printQuietSummary(savedEmails, savedScrappedUrls)
            if not NON_INTERACTIVE:
                console.print(sInfo + " Press Any Key To Exit", end="")
                input()
            log.info("Thanks For Using MailGrab!")
            log.info("Program Ended Successfully")
            log.info(
                "----------------------------------------------------------------------------------------------------------------------")
            sys.exit(1 if saveFailed else 0)
        else:
            pass

    # checking if user provide a text file containing bunch of urls, if not pass the program
    # Checking if _inputUrls is a Empty File -- an explicit --url always wins over the seed file
    if cliArgs.url is None and os.path.isfile(cliArgs.input) and os.path.getsize(cliArgs.input) > 0:
        MAILGRAB()
    else:
        pass
    # this code will try to get the emails from the url
    emailCount = 0
    try:
        # Showing Banner
        BANNER()
        log.info("Banner Printed")
        if cliArgs.url is not None:
            userUrl = cliArgs.url if cliArgs.url.startswith("http") else "http://" + cliArgs.url
            log.info("Url Provided Via --url")
        else:
            # taking url input as a string from user
            console.print(sInput + "Enter The Url To Be Scanned: ", end="")
            inputUserUrl = str(input())
            log.info("User Inputted Url")
            # Removing White Spaces From The URL
            inputUserUrl = inputUserUrl.replace(" ", "")
            log.info("Removed White Spaces From Url")
            # checking the user input is a valid url or not. if not then formatting the string as url

            while True:
                if inputUserUrl == " ":
                    console.print(sWarning + " Please Enter A Valid Url")
                    log.warn("User Entered Invalid Url")
                    console.print(sInput + "Enter The Url To Be Scanned: ", end="")
                    inputUserUrl = str(input())
                    log.info("User Inputted Url")
                elif inputUserUrl == "":
                    console.print(sWarning + " Url Cannot Be Blank")
                    log.warn("User Entered Invalid Url")
                    console.print(sInput + "Enter The Url To Be Scanned: ", end="")
                    inputUserUrl = str(input())
                    log.info("User Inputted Url")
                elif " " in inputUserUrl:
                    console.print(
                        sWarning + " You Cannot Use White Spaces As/In Url")
                    log.warn("User Entered Invalid Url")
                    console.print(sInput + "Enter The Url To Be Scanned: ", end="")
                    inputUserUrl = str(input())
                    log.info("User Inputted Url")
                else:
                    inputUserUrl = inputUserUrl.replace(" ", "")
                    break
            if not inputUserUrl.startswith("http"):
                userUrl = "http://" + inputUserUrl
            else:
                userUrl = inputUserUrl

        if cliArgs.depth is not None:
            depth = cliArgs.depth
            depthInput = depth
            if depth <= 0 or depth > 500:
                console.print(sDanger + " --depth Must Be Between 1 And 500")
                print("Error: --depth Must Be Between 1 And 500", file=sys.stderr)  # still visible under --quiet
                log.error("Invalid --depth: {}".format(depth))
                sys.exit(1)
        else:
            # Taking Input From The User For The Depth Of The Search
            console.print(sInput + "Enter The Depth Of The Search: ", end="")
            depthInput = input()
            log.info("User Inputted Depth")
            # checking the input is int or not. if not then taking the input again and again continously in loop until the input is int
            while True:
                while True:
                    # checking the input is float or not
                    if type(depthInput) == float:
                        console.print(sWarning + " Input Cannot Be A Float!")
                        log.warn("User Entered Invalid Depth")
                        console.print(
                            sInput + "Enter The Depth Of The Search (Max Is 500): ", end="")
                        depthInput = input()
                        log.info("User Inputted Depth")
                    else:
                        break
                while True:
                    # checking the input is digit or not, if yes breal while loop
                    if depthInput.isdigit():
                        depthInput = int(depthInput)
                        break
                    # checking the input is digit or not, if not continuing while loop
                    elif not depthInput.isdigit():
                        console.print(sWarning + " Input Must Be A Number!")
                        log.warn("User Entered Invalid Depth")
                        console.print(
                            sInput + "Enter The Depth Of The Search (Max Is 500): ", end="")
                        depthInput = input()
                        log.info("User Inputted Depth")
                    # checking the input is digit or not, else continuing while loop
                    else:
                        console.print(sWarning + " Input Must Be A Number!")
                        log.warn("User Entered Invalid Depth")
                        console.print(
                            sInput + "Enter The Depth Of The Search (Max Is 500): ", end="")
                        depthInput = input()
                        log.info("User Inputted Depth")
                if depthInput < 0:
                    console.print(sWarning + " Search Depth Cannot Be Negative")
                    log.warn("User Entered Invalid Depth")
                    console.print(
                        sInput + "Enter The Depth Of The Search (Max Is 500): ", end="")
                    depthInput = input()
                    log.info("User Inputted Depth")
                    # depth = depthInput
                elif depthInput == 0:
                    console.print(sWarning + " Search Depth Cannot Be Zero")
                    log.warn("User Entered Invalid Depth")
                    console.print(
                        sInput + "Enter The Depth Of The Search (Max Is 500): ", end="")
                    depthInput = input()
                    log.info("User Inputted Depth")
                    # depth = depthInput
                elif depthInput > 500:
                    console.print(
                        sWarning
                        + " To Prevent Crashing The Program, Search Depth Cannot Be Greater Than 500"
                    )
                    log.warn("User Entered Invalid Depth")
                    console.print(
                        sInput + "Enter The Depth Of The Search (Max Is 500): ", end="")
                    depthInput = input()
                    log.info("User Inputted Depth")
                    # depth = depthInput
                else:
                    depth = depthInput
                    break
                depth = int(depthInput)
        # storing the urls and emails in sets
        scrappedUrls = set()
        emails = set()
        emailSources = {}
        socialLinks = set()
        robotsCache = {}
        if RESUME_MODE:
            try:
                priorScrapped, priorEmails, priorSources = _loadPriorResults()
                scrappedUrls |= priorScrapped
                emails |= priorEmails
                for email, urls_ in priorSources.items():
                    emailSources.setdefault(email, set()).update(urls_)
            except Exception as e:
                console.print(sWarning + " Could Not Load Prior Results For --resume: {}".format(e))
                log.warning("Could Not Load Prior Results For Resume: {}".format(e))
        os.system("cls")
        log.info("Cleared The Screen")
        BANNER()
        # Checking The Current Time
        startTime = time.time()
        depthLimit = str(depthInput)
        console.print(sSuccess + " Base Url Is Set To:  " + userUrl)
        log.info("Base Url Set To: " + userUrl)
        console.print(sSuccess + " Search Depth Is Set To: " + depthLimit)
        log.info("Search Depth Set To: " + depthLimit)
        console.print("\n")

        crawlUrls(
            userUrl, depth, session, MAX_WORKERS, REQUEST_DELAY, REQUEST_TIMEOUT,
            sameDomainOnly=SAME_DOMAIN_ONLY, ignoreRobots=IGNORE_ROBOTS, userAgent=USER_AGENT,
            visited=scrappedUrls, emails=emails, emailSources=emailSources,
            socialLinks=socialLinks, robotsCache=robotsCache, maxHops=MAX_HOPS,
            useSitemap=USE_SITEMAP)

    except KeyboardInterrupt:
        console.print(
            sDanger + " Closing! Because User Interrupted The Program")
        log.error("Closing! Because User Interrupted The Program")
        console.print(sInfo + " Keyboard Interrupt Detected")
        log.info("Keyboard Interrupt Detected")
    # printing the extracted emails
    try:
        print(" ")
        print(" ")
        for mail in sorted(emails):
            emailCount += 1
            ben = Text(f"[{emailCount}]")
            ben.stylize("bold #5fd700")
            cEmails = Text(mail)
            cEmails.stylize("bold #a8a8a8")
            console.print(ben + "Email: " + cEmails)
    except Exception as e:
        console.print(sDanger + " Error: {}".format(e))
        log.error("Error: {}".format(e))
        console.print(sInfo + " An Error Ocurred While Printing The Emails")
        log.info("An Error Ocurred While Printing The Emails")

    # Saving Emails And Urls (plain text, csv, and json; merges with prior results in append mode)
    saveFailed = False
    try:
        console.print(
            sInfo + "Saving Scrapped Emails And Urls (_emails.txt, _scrappedUrls.txt, _emails.csv, _results.json)\n")
        emailList, scrappedUrlList, savedEmails, savedScrappedUrls = saveResults(
            scrappedUrls, emails, emailSources, socialLinks, APPEND_RESULTS or RESUME_MODE,
            VERIFY_MX, REQUEST_TIMEOUT, MAX_WORKERS)
        console.print(sSuccess + " Scrapped Emails And Urls Are Successfully Saved!\n\n")
    except Exception as e:
        saveFailed = True
        console.print(sDanger + " Error: {}".format(e))
        print("Error: An Error Ocurred While Saving The Emails And Urls: {}".format(e),
              file=sys.stderr)  # still visible under --quiet, and this failure changes the exit code
        log.error("Error: {}".format(e))
        console.print(sInfo + " An Error Ocurred While Saving The Emails And Urls")
        log.info("An Error Ocurred While Saving The Emails And Urls")
        savedEmails, savedScrappedUrls = emails, scrappedUrls

    # Now Showing How Many Emails And Urls Are Collected (post-append-merge totals)
    try:
        console.print(
            sSuccess + " Number Of Scrapped Emails: {}".format(len(savedEmails)))
        log.info("Number Of Scrapped Emails: {}".format(len(savedEmails)))
        console.print(
            sSuccess + " Number Of Scrapped Urls: {}".format(len(savedScrappedUrls)))
        log.info("Number Of Scrapped Urls: {}".format(len(savedScrappedUrls)))
    except Exception as e:
        console.print(sDanger + " Error: {}".format(e))
        log.error("Error: {}".format(e))
        console.print(
            sInfo + " An Error Ocurred While Printing The Emails And Urls")
        log.info("An Error Ocurred While Printing The Emails And Urls")
    #  Now Showing The Time Taken To Collect Emails And Urls
    try:
        console.print(
            sSuccess +
            " Time Taken To Collect Emails: {} MiliSeconds".format(
                time.time() - startTime)
        )
        log.info("Time Taken To Collect Emails: {}".format(
            time.time() - startTime))
        console.print(
            sSuccess +
            " Time Taken To Collect Sub Urls: {} MiliSeconds".format(
                time.time() - startTime)
        )
        log.info("Time Taken To Collect Sub Urls: {}".format(
            time.time() - startTime))
    except Exception as e:
        console.print(sDanger + " Error: {}".format(e))
        log.error("Error: {}".format(e))
        console.print(
            sInfo + " An Error Ocurred While Printing The Time Taken\n")
        log.info("An Error Ocurred While Printing The Time Taken\n")
    # showing How Much system resource is using
    try:
        console.print(sSuccess + " Current Usage Of System Resource:")
        log.info("Current Usage Of System Resource:")
        console.print(sSuccess + " CPU: {}%".format(psutil.cpu_percent()))
        log.info("CPU: {}%".format(psutil.cpu_percent()))
        console.print(
            sSuccess + " RAM: {}%".format(psutil.virtual_memory()[2]))
        log.info("RAM: {}%".format(psutil.virtual_memory()[2]))
        console.print(
            sSuccess + " Disk: {}%".format(psutil.disk_usage("/")[3]))
        log.info("Disk: {}%".format(psutil.disk_usage("/")[3]))
        console.print(
            sSuccess + " Network: {}%".format(psutil.net_io_counters()[0]))
        log.info("Network: {}%".format(psutil.net_io_counters()[0]))
        console.print(
            sSuccess + " Network Speed: {} kbps".format(psutil.net_io_counters()[1]))
        log.info("Network Speed: {} kbps".format(psutil.net_io_counters()[1]))

    except Exception as e:
        console.print(sDanger + " Error: {}".format(e))
        log.error("Error: {}".format(e))
        console.print(
            sInfo + " An Error Ocurred While Printing The System Resource Used By The Program\n")
        log.info(
            "An Error Ocurred While Printing The System Resource Used By The Program\n")
    # Showing The os Information
    try:
        console.print(
            sSuccess + " OS Information: {}".format(platform.platform()))
        log.info("OS Information: {}".format(platform.platform()))
    except Exception as e:
        console.print(sDanger + " Error: {}".format(e))
        log.error("Error: {}".format(e))
        console.print(
            sInfo + " An Error Ocurred While Printing The OS Information\n")
        log.info("An Error Ocurred While Printing The OS Information\n")
    # showing the name of the os
    try:
        console.print(sSuccess + " OS Name: {}".format(platform.system()))
        log.info("OS Name: {}".format(platform.system()))
    except Exception as e:
        console.print(sDanger + " Error: {}".format(e))
        log.error("Error: {}".format(e))
        console.print(sInfo + " An Error Ocurred While Printing The OS Name\n")
        log.info("An Error Ocurred While Printing The OS Name\n")
    # showing the Current Date And Time in a formal way
    try:
        console.print(
            sSuccess + " Current Date And Time: {}".format(datetime.datetime.now()))
        log.info("Current Date And Time: {}".format(time.ctime()))
    except Exception as e:
        console.print(sDanger + " Error: {}".format(e))
        log.error("Error: {}".format(e))
        console.print(
            sInfo + " An Error Ocurred While Printing The Current Date And Time\n")
        log.info("An Error Ocurred While Printing The Current Date And Time\n")

    console.print(
        sSuccess + " All Scrapped Emails And Urls Are Saved In _emails.txt And _scrappedUrls.txt\n")
    log.info(
        "All Scrapped Emails And Urls Are Saved In _emails.txt And _scrappedUrls.txt\n")
    console.print(sSuccess + " Thanks For Using MailGrab!")
    console.print(sSuccess + " Made By: " + __author__)
    console.print(
        sInfo + " If You Have Any Suggestion Or Bug Please Contact Me At: " + __email__)
    if QUIET_MODE:
        printQuietSummary(savedEmails, savedScrappedUrls)
    if not NON_INTERACTIVE:
        console.print(sInfo + " Press Any Key To Exit", end="")
        input()
    log.info("Thanks For Using MailGrab!")
    log.info("Program Ended Successfully")
    log.info("----------------------------------------------------------------------------------------------------------------------")
    sys.exit(1 if saveFailed else 0)
