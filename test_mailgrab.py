"""
Smoke tests for MailGrab.py's crawl engine, output formats, CLI flags, and
robustness features. Runs the real script via runpy against a local HTTP
server (no real network needed) and inspects the output files / elapsed time.
Run: python test_mailgrab.py
"""
import contextlib
import csv
import http.server
import io
import itertools
import json
import os
import runpy
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

MAILGRAB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "MailGrab.py"))

ENV_KEYS = (
    "MAILGRAB_MAX_WORKERS", "MAILGRAB_DELAY", "MAILGRAB_TIMEOUT", "MAILGRAB_SAME_DOMAIN",
    "MAILGRAB_IGNORE_ROBOTS", "MAILGRAB_APPEND", "MAILGRAB_USER_AGENT", "MAILGRAB_PROXY",
    "MAILGRAB_RESUME", "MAILGRAB_USE_SITEMAP", "MAILGRAB_VERIFY_MX", "MAILGRAB_MAX_HOPS",
    "MAILGRAB_QUIET",
)


def encode_cloudflare_email(email, key=0x2A):
    """Independent re-implementation of Cloudflare's obfuscation, to build test fixtures."""
    encoded = [key] + [b ^ key for b in email.encode("utf-8")]
    return "".join(f"{b:02x}" for b in encoded)


class RecordingHandler(http.server.BaseHTTPRequestHandler):
    delay = 0.0
    pages = {}
    statusScript = {}  # path -> list of status codes to return on successive requests
    requestedPaths = []
    userAgents = []

    def do_GET(self):
        if RecordingHandler.delay:
            time.sleep(RecordingHandler.delay)
        RecordingHandler.requestedPaths.append(self.path)
        RecordingHandler.userAgents.append(self.headers.get("User-Agent", ""))
        script = RecordingHandler.statusScript.get(self.path)
        if script:
            status = script.pop(0) if len(script) > 1 else script[0]
        else:
            status = 200
        body = self.pages.get(self.path, b"<html></html>")
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if status < 300:
            self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_server():
    # ThreadingHTTPServer: plain HTTPServer handles one request at a time, which would
    # serialize "concurrent" requests this test sends and hide any real concurrency bug.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run_mailgrab(url, depth):
    """Runs MailGrab.py's interactive path (no _inputUrls.txt) against `url` in the CWD."""
    inputs = itertools.chain([url, str(depth)], itertools.repeat(""))
    with mock.patch("os.system", return_value=0), \
            mock.patch("requests.get", return_value=mock.Mock()), \
            mock.patch("builtins.input", side_effect=inputs), \
            mock.patch.object(sys, "argv", ["MailGrab.py"]):
        try:
            runpy.run_path(MAILGRAB_PATH, run_name="__main__")
        except SystemExit:
            pass


def run_mailgrab_argv(extraArgs, expectNoPrompts=True):
    """Runs MailGrab.py with CLI flags, returning its sys.exit() code (None if none raised).
    If expectNoPrompts, input() raises instead of hanging."""
    inputSideEffect = (
        (lambda *a, **k: (_ for _ in ()).throw(AssertionError("input() was called unexpectedly")))
        if expectNoPrompts else itertools.chain([""], itertools.repeat(""))
    )
    with mock.patch("os.system", return_value=0), \
            mock.patch("requests.get", return_value=mock.Mock()), \
            mock.patch("builtins.input", side_effect=inputSideEffect), \
            mock.patch.object(sys, "argv", ["MailGrab.py"] + extraArgs):
        try:
            runpy.run_path(MAILGRAB_PATH, run_name="__main__")
        except SystemExit as e:
            return e.code
    return None


class MailGrabTestCase(unittest.TestCase):
    def setUp(self):
        self.server = start_server()
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.origDir = os.getcwd()
        self.tmpDir = tempfile.mkdtemp()
        os.chdir(self.tmpDir)
        self.addCleanup(os.chdir, self.origDir)
        self.addCleanup(shutil.rmtree, self.tmpDir, True)
        RecordingHandler.pages = {}
        RecordingHandler.statusScript = {}
        RecordingHandler.requestedPaths = []
        RecordingHandler.userAgents = []
        RecordingHandler.delay = 0.0
        for key in ENV_KEYS:
            self.addCleanup(os.environ.pop, key, None)
            os.environ.pop(key, None)

    def _read(self, name):
        with open(name, encoding="utf-8") as f:
            return f.read()

    def _resultsJson(self):
        return json.loads(self._read("_results.json"))


class CrawlTest(MailGrabTestCase):
    def setUp(self):
        super().setUp()
        RecordingHandler.pages = {
            "/": b'<html><body>a@acme.test <a href="/p1">1</a> <a href="/p2">2</a></body></html>',
            "/p1": b"<html><body>b@acme.test</body></html>",
            "/p2": b"<html><body>c@acme.test</body></html>",
        }

    def test_concurrent_fetch_and_session_reuse(self):
        # /p1 and /p2 are the same BFS level (root fetched alone first, then both together):
        # forcing 1 worker makes that second round serialize, so it must be slower than the pool default
        RecordingHandler.delay = 0.3

        os.environ["MAILGRAB_MAX_WORKERS"] = "1"
        start = time.time()
        run_mailgrab(self.base, depth=3)
        sequentialElapsed = time.time() - start

        os.environ.pop("MAILGRAB_MAX_WORKERS")
        start = time.time()
        run_mailgrab(self.base, depth=3)
        concurrentElapsed = time.time() - start

        content = self._read("_emails.txt")
        for addr in ("a@acme.test", "b@acme.test", "c@acme.test"):
            self.assertIn(addr, content)
        self.assertLess(
            concurrentElapsed, sequentialElapsed - 0.15,
            f"same-level urls were not fetched concurrently "
            f"(sequential={sequentialElapsed:.2f}s, concurrent={concurrentElapsed:.2f}s)")

    def test_rate_limit_delay(self):
        RecordingHandler.pages["/p3"] = b"<html><body>d@acme.test</body></html>"
        RecordingHandler.pages["/"] = (
            b'<html><body><a href="/p1">1</a><a href="/p2">2</a><a href="/p3">3</a></body></html>'
        )
        os.environ["MAILGRAB_DELAY"] = "0.2"
        start = time.time()
        run_mailgrab(self.base, depth=4)
        elapsed = time.time() - start
        # 3 submissions in the same batch -> 2 gaps of 0.2s just from the rate limit
        self.assertGreaterEqual(elapsed, 0.35, "MAILGRAB_DELAY was not applied between requests")

    def test_request_timeout(self):
        RecordingHandler.delay = 2.0
        os.environ["MAILGRAB_TIMEOUT"] = "0.3"
        start = time.time()
        run_mailgrab(self.base, depth=1)
        elapsed = time.time() - start
        self.assertLess(elapsed, 1.5, "request timeout was not honored, crawl hung on a slow server")


class CrawlCorrectnessTest(MailGrabTestCase):
    def test_same_domain_extension_and_scheme_filtering(self):
        RecordingHandler.pages = {
            "/": (
                b'<html><body>'
                b'<a href="/same">same</a>'
                b'<a href="/image.png">img</a>'
                b'<a href="mailto:mailto@acme.test">mail</a>'
                b'<a href="tel:+15551234567">tel</a>'
                b'<a href="javascript:void(0)">js</a>'
                b'<a href="http://offsite.invalid/never">offsite</a>'
                b'</body></html>'
            ),
            "/same": b"<html><body>onsite@acme.test</body></html>",
        }
        run_mailgrab_argv(["--url", self.base, "--depth", "10", "--same-domain"])

        results = self._resultsJson()
        self.assertIn("mailto@acme.test", results["emails"])
        self.assertIn("onsite@acme.test", results["emails"])
        self.assertNotIn("/image.png", "".join(results["scrappedUrls"]))
        self.assertFalse(any("offsite.invalid" in u for u in results["scrappedUrls"]))
        self.assertNotIn("/image.png", RecordingHandler.requestedPaths)

    def test_deobfuscation_cloudflare_and_at_dot(self):
        encoded = encode_cloudflare_email("hidden@acme.test")
        RecordingHandler.pages = {
            "/": (
                f'<html><body>'
                f'<span class="__cf_email__" data-cfemail="{encoded}"></span>'
                f'Contact us: person [at] acme [dot] test'
                f'</body></html>'
            ).encode("utf-8"),
        }
        run_mailgrab_argv(["--url", self.base, "--depth", "1"])

        emails = self._resultsJson()["emails"]
        self.assertIn("hidden@acme.test", emails)
        self.assertIn("person@acme.test", emails)

    def test_robots_txt_respected_and_ignorable(self):
        RecordingHandler.pages = {
            "/robots.txt": b"User-agent: *\nDisallow: /blocked\n",
            "/": b'<html><body><a href="/blocked">b</a><a href="/allowed">a</a></body></html>',
            "/blocked": b"<html><body>blocked@acme.test</body></html>",
            "/allowed": b"<html><body>allowed@acme.test</body></html>",
        }

        run_mailgrab_argv(["--url", self.base, "--depth", "10"])
        respectedEmails = self._resultsJson()["emails"]
        self.assertIn("allowed@acme.test", respectedEmails)
        self.assertNotIn("blocked@acme.test", respectedEmails)

        run_mailgrab_argv(["--url", self.base, "--depth", "10", "--ignore-robots"])
        ignoredEmails = self._resultsJson()["emails"]
        self.assertIn("blocked@acme.test", ignoredEmails)


class RobustnessTest(MailGrabTestCase):
    def test_user_agent_header_and_retry_on_5xx(self):
        RecordingHandler.pages = {
            "/": b'<html><body><a href="/flaky">f</a></body></html>',
            "/flaky": b"<html><body>flaky@acme.test</body></html>",
        }
        RecordingHandler.statusScript = {"/flaky": [503, 200]}

        run_mailgrab_argv(["--url", self.base, "--depth", "2", "--user-agent", "TestBot/1.0"])

        self.assertIn("TestBot/1.0", RecordingHandler.userAgents)
        self.assertIn("flaky@acme.test", self._resultsJson()["emails"])


class OutputTest(MailGrabTestCase):
    def test_csv_and_json_exports_with_source_tracking(self):
        RecordingHandler.pages = {"/": b"<html><body>a@acme.test</body></html>"}
        run_mailgrab_argv(["--url", self.base, "--depth", "1"])

        results = self._resultsJson()
        self.assertEqual(results["emails"], ["a@acme.test"])
        self.assertEqual(results["sources"]["a@acme.test"], [self.base])

        with open("_emails.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["email", "found_on_url"])
        self.assertIn(["a@acme.test", self.base], rows)

    def test_append_merges_with_prior_run(self):
        RecordingHandler.pages = {"/": b"<html><body>first@acme.test</body></html>"}
        run_mailgrab_argv(["--url", self.base, "--depth", "1"])
        self.assertEqual(self._resultsJson()["emails"], ["first@acme.test"])

        RecordingHandler.pages = {"/": b"<html><body>second@acme.test</body></html>"}
        run_mailgrab_argv(["--url", self.base, "--depth", "1", "--append"])
        merged = set(self._resultsJson()["emails"])
        self.assertEqual(merged, {"first@acme.test", "second@acme.test"})

        run_mailgrab_argv(["--url", self.base, "--depth", "1"])
        overwritten = set(self._resultsJson()["emails"])
        self.assertEqual(overwritten, {"second@acme.test"})


class CliAndConfigTest(MailGrabTestCase):
    def test_url_and_depth_flags_skip_all_prompts(self):
        RecordingHandler.pages = {"/": b"<html><body>a@acme.test</body></html>"}
        run_mailgrab_argv(["--url", self.base, "--depth", "1"], expectNoPrompts=True)
        self.assertIn("a@acme.test", self._resultsJson()["emails"])

    def test_input_flag_overrides_default_seed_file(self):
        RecordingHandler.pages = {"/": b"<html><body>batch@acme.test</body></html>"}
        with open("custom_urls.txt", "w", encoding="utf-8") as f:
            f.write(self.base + "\n")
        self.assertFalse(os.path.exists("_inputUrls.txt"))
        run_mailgrab_argv(["--input", "custom_urls.txt", "--depth", "1"], expectNoPrompts=True)
        self.assertIn("batch@acme.test", self._resultsJson()["emails"])

    def test_concurrency_delay_timeout_flags_override_env(self):
        RecordingHandler.pages = {
            "/": b'<html><body><a href="/p1">1</a><a href="/p2">2</a></body></html>',
            "/p1": b"<html><body>x@acme.test</body></html>",
            "/p2": b"<html><body>y@acme.test</body></html>",
        }
        RecordingHandler.delay = 0.0
        start = time.time()
        run_mailgrab_argv(
            ["--url", self.base, "--depth", "3", "--concurrency", "1", "--delay", "0.2", "--timeout", "5"],
            expectNoPrompts=True,
        )
        elapsed = time.time() - start
        # concurrency=1 serializes p1/p2, and delay=0.2 adds one more gap on top -> at least ~0.2s
        self.assertGreaterEqual(elapsed, 0.15, "--delay flag was not honored")
        emails = self._resultsJson()["emails"]
        self.assertIn("x@acme.test", emails)
        self.assertIn("y@acme.test", emails)

    def test_config_file_prefills_env_vars(self):
        configPath = os.path.join(self.tmpDir, "config.json")
        with open(configPath, "w", encoding="utf-8") as f:
            json.dump({"delay": "0.05", "same_domain": "1"}, f)
        RecordingHandler.pages = {"/": b"<html><body>a@acme.test</body></html>"}
        run_mailgrab_argv(["--url", self.base, "--depth", "1", "--config", configPath], expectNoPrompts=True)
        self.assertEqual(os.environ.get("MAILGRAB_DELAY"), "0.05")
        self.assertEqual(os.environ.get("MAILGRAB_SAME_DOMAIN"), "1")

    def test_url_overrides_existing_seed_file(self):
        # A first verification pass found --url was silently discarded whenever a seed
        # file existed, because MAILGRAB() read the file and exited before --url's own
        # code path could ever run.
        with open("_inputUrls.txt", "w", encoding="utf-8") as f:
            f.write(self.base + "/fromfile\n")
        RecordingHandler.pages = {
            "/fromfile": b"<html><body>fromfile@acme.test</body></html>",
            "/fromflag": b"<html><body>fromflag@acme.test</body></html>",
        }
        run_mailgrab_argv(["--url", self.base + "/fromflag", "--depth", "1"], expectNoPrompts=True)
        emails = self._resultsJson()["emails"]
        self.assertIn("fromflag@acme.test", emails)
        self.assertNotIn("fromfile@acme.test", emails)

    def test_partial_cli_flags_exit_cleanly_instead_of_hanging(self):
        # A first verification pass found that giving only one of --url/--depth still
        # blocked on the other's input() prompt -- fatal in a script/CI job with no
        # interactive stdin. Both cases must now fail fast with a clear error instead.
        self.assertEqual(run_mailgrab_argv(["--url", self.base], expectNoPrompts=True), 1)

        self.assertFalse(os.path.exists("_inputUrls.txt"))
        self.assertEqual(run_mailgrab_argv(["--depth", "1"], expectNoPrompts=True), 1)


class RegressionTest(MailGrabTestCase):
    def test_deobfuscation_regex_does_not_hang_on_long_unmarked_text(self):
        # A first verification pass found OBFUSCATED_EMAIL_RE had catastrophic
        # backtracking on a long run of word characters with no "at"/"dot" marker
        # (e.g. a base64 blob or minified inline script) -- exactly like this fixture.
        longBlob = "a" * 200000
        RecordingHandler.pages = {
            "/": ("<html><body>real@acme.test<script>" + longBlob + "</script></body></html>").encode("utf-8"),
        }
        start = time.time()
        run_mailgrab_argv(["--url", self.base, "--depth", "1"], expectNoPrompts=True)
        elapsed = time.time() - start
        self.assertLess(elapsed, 5.0, "de-obfuscation regex hung on adversarial input")
        self.assertIn("real@acme.test", self._resultsJson()["emails"])

    def test_batch_mode_keyboard_interrupt_preserves_partial_results(self):
        # A first verification pass found batch mode had no try/except around its crawl
        # loop, so interrupting it (or any exception mid-crawl) discarded everything
        # gathered so far instead of saving it, unlike the single-url interactive path.
        RecordingHandler.pages = {
            "/": b'<html><body>root@acme.test<a href="/c1">1</a><a href="/c2">2</a></body></html>',
            "/c1": b"<html><body>child@acme.test</body></html>",
            "/c2": b"<html><body>child@acme.test</body></html>",
        }
        with open("_inputUrls.txt", "w", encoding="utf-8") as f:
            f.write(self.base + "\n")
        os.environ["MAILGRAB_DELAY"] = "0.01"

        with mock.patch("time.sleep", side_effect=KeyboardInterrupt):
            run_mailgrab_argv(["--depth", "3"], expectNoPrompts=True)

        self.assertTrue(os.path.exists("_results.json"), "no results were saved after the interrupt")
        self.assertIn("root@acme.test", self._resultsJson()["emails"])


class EmailQualityTest(MailGrabTestCase):
    def test_case_insensitive_dedup(self):
        RecordingHandler.pages = {
            "/": b'<html><body>User@Acme.test <a href="/p1">1</a></body></html>',
            "/p1": b"<html><body>user@acme.test</body></html>",
        }
        run_mailgrab_argv(["--url", self.base, "--depth", "2"], expectNoPrompts=True)
        emails = self._resultsJson()["emails"]
        self.assertEqual(emails, ["user@acme.test"])

    def test_placeholder_emails_filtered(self):
        RecordingHandler.pages = {
            "/": b"<html><body>real@acme-realbiz.test and test@test.com and example@example.com</body></html>",
        }
        run_mailgrab_argv(["--url", self.base, "--depth", "1"], expectNoPrompts=True)
        emails = self._resultsJson()["emails"]
        self.assertEqual(emails, ["real@acme-realbiz.test"])

    def test_real_live_domains_are_not_treated_as_placeholders(self):
        # A first verification pass found the placeholder-domain blocklist wholesale-dropped
        # domain.com/email.com/test.com/company.com/sentry.io -- all real, live domains that
        # legitimately hand out real mailboxes. Only exact known-placeholder addresses and
        # RFC 2606 reserved domains (example.com/.org/.net) or a site builder's own internal
        # domain (wixpress.com) should ever be filtered.
        RecordingHandler.pages = {
            "/": b"<html><body>jane@domain.com and john@email.com and staff@company.com</body></html>",
        }
        run_mailgrab_argv(["--url", self.base, "--depth", "1"], expectNoPrompts=True)
        emails = self._resultsJson()["emails"]
        self.assertEqual(set(emails), {"jane@domain.com", "john@email.com", "staff@company.com"})

    def test_verify_mx_drops_domains_without_mx_record(self):
        import dns.resolver
        RecordingHandler.pages = {
            "/": b"<html><body>good@hasmx.test and bad@nomx.test</body></html>",
        }

        def fakeResolve(domain, rtype, lifetime=None):
            if domain == "hasmx.test":
                return ["mx record"]
            raise dns.resolver.NXDOMAIN()

        with mock.patch("dns.resolver.resolve", side_effect=fakeResolve):
            run_mailgrab_argv(["--url", self.base, "--depth", "1", "--verify-mx"], expectNoPrompts=True)
        emails = self._resultsJson()["emails"]
        self.assertEqual(emails, ["good@hasmx.test"])

    def test_verify_mx_keeps_email_on_transient_dns_failure(self):
        RecordingHandler.pages = {
            "/": b"<html><body>real@flaky-dns.test</body></html>",
        }

        def fakeResolve(domain, rtype, lifetime=None):
            raise Exception("simulated resolver timeout")

        with mock.patch("dns.resolver.resolve", side_effect=fakeResolve):
            run_mailgrab_argv(["--url", self.base, "--depth", "1", "--verify-mx"], expectNoPrompts=True)
        emails = self._resultsJson()["emails"]
        self.assertEqual(emails, ["real@flaky-dns.test"])


class SmarterDiscoveryTest(MailGrabTestCase):
    def test_sitemap_seeds_extra_urls_only_when_enabled(self):
        RecordingHandler.pages = {
            "/": b"<html><body>root@acme.test</body></html>",
            "/sitemap.xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b'<url><loc>{}/from-sitemap</loc></url>'
                b'</urlset>'
            ).replace(b"{}", self.base.encode()),
            "/from-sitemap": b"<html><body>sitemap@acme.test</body></html>",
        }
        run_mailgrab_argv(["--url", self.base, "--depth", "5"], expectNoPrompts=True)
        self.assertNotIn("sitemap@acme.test", self._resultsJson()["emails"])

        run_mailgrab_argv(["--url", self.base, "--depth", "5", "--use-sitemap"], expectNoPrompts=True)
        self.assertIn("sitemap@acme.test", self._resultsJson()["emails"])

    def test_sitemap_index_is_resolved_to_page_urls(self):
        # A first verification pass found a <sitemapindex> (the layout WordPress/Yoast,
        # Shopify, and most CMSes default to) was treated as if its <loc> entries were
        # content pages, instead of being fetched as child sitemaps in their own right.
        RecordingHandler.pages = {
            "/": b"<html><body>root@acme.test</body></html>",
            "/sitemap.xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b'<sitemap><loc>{base}/sitemap-pages.xml</loc></sitemap>'
                b'</sitemapindex>'
            ).replace(b"{base}", self.base.encode()),
            "/sitemap-pages.xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b'<url><loc>{base}/from-index</loc></url>'
                b'</urlset>'
            ).replace(b"{base}", self.base.encode()),
            "/from-index": b"<html><body>indexed@acme.test</body></html>",
        }
        run_mailgrab_argv(["--url", self.base, "--depth", "5", "--use-sitemap"], expectNoPrompts=True)
        self.assertIn("indexed@acme.test", self._resultsJson()["emails"])

    def test_robots_crawl_delay_is_honored(self):
        # stdlib RobotFileParser.crawl_delay() only recognizes an integer delay
        # (it checks .isdigit()); "Crawl-delay: 1" is the smallest realistic value
        RecordingHandler.pages = {
            "/robots.txt": b"User-agent: *\nCrawl-delay: 1\n",
            "/": b'<html><body><a href="/p1">1</a><a href="/p2">2</a></body></html>',
            "/p1": b"<html><body>x@acme.test</body></html>",
            "/p2": b"<html><body>y@acme.test</body></html>",
        }
        start = time.time()
        run_mailgrab_argv(["--url", self.base, "--depth", "3"], expectNoPrompts=True)
        elapsed = time.time() - start
        # p1/p2 submitted back-to-back on the same domain -> one 1s crawl-delay gap between them
        self.assertGreaterEqual(elapsed, 0.9, "robots.txt Crawl-delay was not honored")

    def test_max_hops_caps_link_distance_independent_of_depth(self):
        RecordingHandler.pages = {
            "/": b'<html><body>root@acme.test<a href="/p1">1</a></body></html>',
            "/p1": b'<html><body>hop1@acme.test<a href="/p2">2</a></body></html>',
            "/p2": b"<html><body>hop2@acme.test</body></html>",
        }
        run_mailgrab_argv(["--url", self.base, "--depth", "10", "--max-hops", "1"], expectNoPrompts=True)
        emails = self._resultsJson()["emails"]
        self.assertIn("root@acme.test", emails)
        self.assertIn("hop1@acme.test", emails)
        self.assertNotIn("hop2@acme.test", emails)

    def test_per_domain_rate_limit_does_not_compound_across_domains(self):
        otherServer = start_server()
        self.addCleanup(otherServer.shutdown)
        otherBase = f"http://127.0.0.1:{otherServer.server_port}"
        try:
            RecordingHandler.pages = {
                "/": (
                    '<html><body><a href="/p1">1</a><a href="{}/other">2</a></body></html>'
                    .format(otherBase)
                ).encode(),
                "/p1": b"<html><body>same@acme.test</body></html>",
                "/other": b"<html><body>other@acme.test</body></html>",
            }
            os.environ["MAILGRAB_DELAY"] = "0.5"
            start = time.time()
            run_mailgrab_argv(["--url", self.base, "--depth", "3"], expectNoPrompts=True)
            elapsed = time.time() - start
            # same-domain p1 and cross-domain /other are two DIFFERENT domains -- neither
            # should wait on the other's 0.5s delay, so this should be much faster than 1s
            self.assertLess(elapsed, 0.8, "delay compounded across unrelated domains")
        finally:
            RecordingHandler.pages = {}


class OpsResilienceTest(MailGrabTestCase):
    def test_resume_skips_already_visited_urls_and_keeps_emails(self):
        # The seed itself is always re-fetched on resume (that's how new links get
        # rediscovered at all -- nothing else persists the link graph), but a
        # previously-visited NON-seed page should not be re-fetched.
        RecordingHandler.pages = {
            "/": b'<html><body>first@acme.test<a href="/old">1</a></body></html>',
            "/old": b"<html><body>old@acme.test</body></html>",
        }
        run_mailgrab_argv(["--url", self.base, "--depth", "5"], expectNoPrompts=True)
        firstRunEmails = self._resultsJson()["emails"]
        self.assertIn("first@acme.test", firstRunEmails)
        self.assertIn("old@acme.test", firstRunEmails)

        RecordingHandler.pages = {
            "/": b'<html><body>first@acme.test<a href="/old">1</a><a href="/new">2</a></body></html>',
            "/old": b"<html><body>old@acme.test</body></html>",
            "/new": b"<html><body>new@acme.test</body></html>",
        }
        RecordingHandler.requestedPaths = []
        run_mailgrab_argv(["--url", self.base, "--depth", "5", "--resume"], expectNoPrompts=True)
        emails = self._resultsJson()["emails"]
        self.assertIn("new@acme.test", emails, "resume did not discover a new page linked from the seed")
        self.assertNotIn("/old", RecordingHandler.requestedPaths, "resume re-fetched an already-visited non-seed page")

    def test_quiet_mode_prints_only_a_json_summary(self):
        RecordingHandler.pages = {"/": b"<html><body>a@acme.test</body></html>"}
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            run_mailgrab_argv(["--url", self.base, "--depth", "1", "--quiet"], expectNoPrompts=True)
        summary = json.loads(buffer.getvalue().strip())
        self.assertEqual(summary["emailCount"], 1)
        self.assertEqual(summary["urlCount"], 1)


class BonusExtractionTest(MailGrabTestCase):
    def test_social_and_contact_links_captured(self):
        RecordingHandler.pages = {
            "/": (
                b'<html><body>a@acme.test'
                b'<a href="https://www.linkedin.com/company/acme">li</a>'
                b'<a href="/contact-us">contact</a>'
                b'</body></html>'
            ),
        }
        run_mailgrab_argv(["--url", self.base, "--depth", "1"], expectNoPrompts=True)
        socialLinks = self._resultsJson()["socialLinks"]
        self.assertTrue(any("linkedin.com" in link for link in socialLinks))
        self.assertTrue(any("contact-us" in link for link in socialLinks))


class KnownLimitationsFixedTest(MailGrabTestCase):
    def test_proxy_validation_rejects_malformed_value(self):
        code = run_mailgrab_argv(["--url", self.base, "--depth", "1", "--proxy", "not-a-url"])
        self.assertEqual(code, 1)

    def test_timeout_is_logged_as_timeout_not_connection_error(self):
        # Not reading _MailGrabLog.txt: logging.basicConfig() is a no-op after the first
        # runpy-loaded run in this process, so later tests' log files aren't reliably
        # written (a pre-existing quirk of testing this script via repeated runpy calls).
        # Console output is captured instead, same approach as the --quiet test.
        RecordingHandler.delay = 2.0
        os.environ["MAILGRAB_TIMEOUT"] = "0.3"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            run_mailgrab_argv(["--url", self.base, "--depth", "1"], expectNoPrompts=True)
        output = buffer.getvalue()
        self.assertIn("Timeout Error", output)
        self.assertNotIn("Connection Error", output)

    def test_dns_failure_is_logged_as_connection_error_not_timeout(self):
        # A first verification pass found _isDisguisedTimeout's unwrapping was too broad:
        # NewConnectionError/NameResolutionError (connection refused / DNS failure) are
        # themselves urllib3 ConnectTimeoutError subclasses, so a dead domain was being
        # mislabeled as a timeout -- the single most common real cause of ConnectionError
        # while crawling arbitrary discovered links.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            run_mailgrab_argv(
                ["--url", "http://nonexistent-domain-xyz-123.invalid", "--depth", "1"],
                expectNoPrompts=True,
            )
        output = buffer.getvalue()
        self.assertIn("Connection Error", output)
        self.assertNotIn("Timeout Error", output)

    def test_robots_txt_cached_across_seeds_in_batch_mode(self):
        RecordingHandler.pages = {
            "/robots.txt": b"User-agent: *\n",
            "/a": b"<html><body>a@acme.test</body></html>",
            "/b": b"<html><body>b@acme.test</body></html>",
        }
        with open("_inputUrls.txt", "w", encoding="utf-8") as f:
            f.write(self.base + "/a\n")
            f.write(self.base + "/b\n")
        run_mailgrab_argv(["--depth", "1"], expectNoPrompts=True)
        self.assertEqual(RecordingHandler.requestedPaths.count("/robots.txt"), 1)

    def test_seed_url_fragment_is_stripped_before_dedup(self):
        RecordingHandler.pages = {
            "/page": b'<html><body>a@acme.test<a href="/page">self</a></body></html>',
        }
        run_mailgrab_argv(["--url", self.base + "/page#section", "--depth", "5"], expectNoPrompts=True)
        self.assertEqual(RecordingHandler.requestedPaths.count("/page"), 1)


if __name__ == "__main__":
    unittest.main()
