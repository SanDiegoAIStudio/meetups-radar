"""Tests for luma_details.py.

Luma's Apify items leave the description blank, so display text comes from the public
event/get description_mirror and is written to a sidecar file. Tests replace fetch_json
and never call the network. Run: python3 -m unittest discover -s tests
"""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import re
import tempfile
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import Message

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import luma_details  # noqa: E402

_REAL_FETCH = luma_details.fetch_json

LINE = re.compile(
    r"^LUMA-DETAILS: ids=(\d+) cached=(\d+) fetched=(\d+) empty=(\d+) failed=(\d+) deferred=(\d+)$"
)


def forbid_network(url, timeout):
    raise AssertionError("network fetch is not allowed in tests: %s" % url)


def write_json(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def paragraph(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def payload_for(text, guest_count=7):
    return {
        "description_mirror": {"type": "doc", "content": [paragraph(text)]},
        "guest_count": guest_count,
        "hosts": [{"name": "Ignored Host"}],
    }


def stamp(days_ago):
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class LumaDetailsTests(unittest.TestCase):
    def setUp(self):
        luma_details.fetch_json = forbid_network

    def tearDown(self):
        luma_details.fetch_json = forbid_network

    def work(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        work = os.path.join(folder.name, "work")
        os.makedirs(work)
        cache = os.path.join(folder.name, "cache.json")
        return work, cache

    def execute(self, work, cache, fetch, max_fetch=400, delay=0, timeout=5):
        luma_details.fetch_json = fetch
        buf = io.StringIO()
        argv = [
            work, "--cache", cache,
            "--max-fetch", str(max_fetch),
            "--delay", str(delay),
            "--timeout", str(timeout),
        ]
        with contextlib.redirect_stdout(buf):
            code = luma_details.main(argv)
        return code, buf.getvalue()

    def load(self, path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def stub_sleep(self, bucket=None):
        original = luma_details.time.sleep

        def _sleep(seconds):
            if bucket is not None:
                bucket.append(seconds)

        luma_details.time.sleep = _sleep
        self.addCleanup(setattr, luma_details.time, "sleep", original)

    def test_paragraphs_do_not_run_together(self):
        """source: adjacent paragraphs carry no separator, so each paragraph ends with a newline."""
        doc = {"type": "doc", "content": [paragraph("Models"), paragraph("But")]}
        self.assertEqual(luma_details.mirror_text(doc), "Models\nBut")

    def test_heading_ends_a_line(self):
        """source: a heading ends with a newline before the following paragraph."""
        doc = {"type": "doc", "content": [
            {"type": "heading", "content": [{"type": "text", "text": "Title"}]},
            paragraph("Body"),
        ]}
        self.assertEqual(luma_details.mirror_text(doc), "Title\nBody")

    def test_blockquote_and_code_block_end_lines(self):
        """source: blockquote and code_block end with a newline, same as a paragraph."""
        doc = {"type": "doc", "content": [
            {"type": "blockquote", "content": [paragraph("quote")]},
            {"type": "code_block", "content": [{"type": "text", "text": "code"}]},
        ]}
        self.assertEqual(luma_details.mirror_text(doc), "quote\n\ncode")

    def test_bullet_list_marks_items(self):
        """source: a list_item starts with '- ' and ends with a newline."""
        doc = {"type": "doc", "content": [{
            "type": "bullet_list",
            "content": [
                {"type": "list_item", "content": [paragraph("one")]},
                {"type": "list_item", "content": [paragraph("two")]},
            ],
        }]}
        self.assertEqual(luma_details.mirror_text(doc), "- one\n\n- two")

    def test_hard_break_is_a_newline(self):
        """source: a hard_break node is a newline so the words on either side stay apart."""
        doc = {"type": "doc", "content": [{
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "left"},
                {"type": "hard_break"},
                {"type": "text", "text": "right"},
            ],
        }]}
        self.assertEqual(luma_details.mirror_text(doc), "left\nright")

    def test_none_returns_empty_string(self):
        """source: mirror_text tolerates None and returns an empty string."""
        self.assertEqual(luma_details.mirror_text(None), "")

    def test_garbage_nodes_do_not_raise(self):
        """source: non-dict nodes, a missing content list, and a non-string text are skipped."""
        doc = {
            "type": "doc",
            "content": [
                None,
                "nope",
                3,
                {"type": "text", "text": None},
                {"type": "paragraph"},
                {"type": "paragraph", "content": "nope"},
                {"type": "unknown", "content": [{"type": "text", "text": "a \t  b"}]},
                {"type": "paragraph", "content": [{"type": "weird"}, {"text": "skip"}]},
            ],
        }
        self.assertEqual(luma_details.mirror_text(doc), "a b")
        self.assertEqual(luma_details.mirror_text([1, None, "x"]), "")
        linked = paragraph_with_mark()
        self.assertEqual(luma_details.mirror_text(linked), "link")

    def test_three_newlines_collapse_to_two(self):
        """source: three or more newlines collapse to a blank line, then the whole text is stripped."""
        doc = {"type": "doc", "content": [
            paragraph("a"),
            {"type": "paragraph"},
            {"type": "paragraph"},
            paragraph("b"),
        ]}
        self.assertEqual(luma_details.mirror_text(doc), "a\n\nb")

    def test_cli_defaults(self):
        """source: cache, max-fetch, delay, and timeout default when only the work folder is passed."""
        args = luma_details.parse_args(["work-folder"])
        self.assertEqual(args.max_fetch, 400)
        self.assertEqual(args.delay, 0.5)
        self.assertEqual(args.timeout, 15)
        self.assertEqual(
            args.cache,
            os.path.join(os.path.dirname(os.path.abspath(luma_details.__file__)),
                         "luma-details-cache.json"),
        )

    def test_fetch_json_sends_event_api_id_header(self):
        """source: Luma returns HTTP 400 unless the query parameter is named event_api_id."""
        captured = {}

        class FakeResp:
            def read(self):
                return b'{"description_mirror": null, "guest_count": 2}'

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_open(req, timeout=None):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["ua"] = req.get_header("User-agent")
            captured["accept"] = req.get_header("Accept")
            return FakeResp()

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_open
        self.addCleanup(setattr, urllib.request, "urlopen", original)
        payload = _REAL_FETCH(
            "https://api.lu.ma/event/get?event_api_id=evt-abc", 15)
        self.assertIsNone(payload["description_mirror"])
        self.assertEqual(captured["timeout"], 15)
        self.assertIn("event_api_id=evt-abc", captured["url"])
        self.assertEqual(captured["accept"], "application/json")
        self.assertEqual(
            captured["ua"],
            "meetups-radar/1",
        )

    def test_ids_come_from_luma_files_with_blank_descriptions(self):
        """source: only luma item files contribute, and only blank descriptions, in first-seen order."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-a-luma.json"), [
            {"id": "evt-aaa", "description": ""},
            {"id": "evt-keep", "description": "Already written."},
            {"id": "evt-aaa", "description": None},
            {"id": "evt-bbb", "description": "   "},
            {"id": "evt-bad_id", "description": ""},
            {"id": "event-nope", "description": ""},
            {"id": "evt-ccc", "description": None},
            42,
            {"id": "evt-ddd"},
        ])
        write_json(os.path.join(work, "items-b-luma.json"), [
            {"id": "evt-aaa", "description": ""},
            {"id": "evt-eee", "description": ""},
        ])
        write_json(os.path.join(work, "items-m-meetup.json"), [
            {"id": "evt-frommeetup", "description": ""},
        ])
        write_json(os.path.join(work, "notes-luma.json"), [
            {"id": "evt-notes", "description": ""},
        ])
        write_json(os.path.join(work, "items-z-luma.json"), {"id": "evt-object"})
        with open(os.path.join(work, "items-bad-luma.json"), "w", encoding="utf-8") as handle:
            handle.write("{")
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            self.assertEqual(timeout, 9)
            return payload_for("text")

        code, out = self.execute(work, cache, fetch, timeout=9)
        self.assertEqual(code, 0, out)
        found = []
        for url in calls:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            found.append(params["event_api_id"][0])
            self.assertNotIn("api_id=", url.replace("event_api_id=", ""))
        self.assertEqual(found, ["evt-aaa", "evt-bbb", "evt-ccc", "evt-ddd", "evt-eee"])
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIsNotNone(LINE.match(out.strip()))

    def test_cache_reuses_fresh_and_refetches_stale(self):
        """source: a cache entry fetched within 21 days is reused; an older one is fetched again."""
        work, cache = self.work()
        fresh_at = stamp(2)
        blank_at = stamp(1)
        stale_at = stamp(22)
        keep_at = stamp(3)
        write_json(cache, {
            "evt-fresh": {"description": "from cache", "guest_count": 9, "fetched_at": fresh_at},
            "evt-blank": {"description": "", "guest_count": None, "fetched_at": blank_at},
            "evt-stale": {"description": "old text", "guest_count": 2, "fetched_at": stale_at},
            "evt-keep": {"description": "keep me", "guest_count": 8, "fetched_at": keep_at},
        })
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-fresh", "description": ""},
            {"id": "evt-blank", "description": ""},
            {"id": "evt-stale", "description": None},
        ])
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return payload_for("from network", guest_count=7)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 1)
        self.assertIn("event_api_id=evt-stale", calls[0])
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        details = sidecar["details"]
        self.assertEqual(details["evt-fresh"]["description"], "from cache")
        self.assertEqual(details["evt-fresh"]["guest_count"], 9)
        self.assertNotIn("fetched_at", details["evt-fresh"])
        self.assertEqual(details["evt-blank"]["description"], "")
        self.assertEqual(details["evt-stale"]["description"], "from network")
        self.assertEqual(details["evt-stale"]["guest_count"], 7)
        self.assertNotIn("evt-keep", details)
        saved = self.load(cache)
        self.assertEqual(saved["evt-fresh"]["fetched_at"], fresh_at)
        self.assertEqual(saved["evt-blank"]["fetched_at"], blank_at)
        self.assertEqual(saved["evt-keep"]["description"], "keep me")
        self.assertNotEqual(saved["evt-stale"]["fetched_at"], stale_at)
        self.assertEqual(saved["evt-stale"]["description"], "from network")
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=3 cached=2 fetched=1 empty=0 failed=0 deferred=0",
        )

    def test_http_400_is_failed_and_not_cached(self):
        """source: an HTTP error, including 400, is failed and is not written to the cache."""
        work, cache = self.work()
        write_json(cache, {
            "evt-keep": {"description": "stay", "guest_count": 4, "fetched_at": stamp(1)},
        })
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-bad", "description": ""},
        ])

        def fetch(url, timeout):
            raise urllib.error.HTTPError(url, 400, "Bad Request", Message(), None)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 3, out)
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=1 cached=0 fetched=0 empty=0 failed=1 deferred=0",
        )
        saved = self.load(cache)
        self.assertNotIn("evt-bad", saved)
        self.assertEqual(saved["evt-keep"]["description"], "stay")
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertNotIn("evt-bad", sidecar["details"])
        self.assertFalse(temp_names(work))
        self.assertFalse(temp_names(os.path.dirname(cache)))

    def test_timeout_non_json_and_missing_key_are_failed(self):
        """source: a timeout, a non-JSON body, a non-object, or a JSON object without description_mirror is failed."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-time", "description": ""},
            {"id": "evt-json", "description": ""},
            {"id": "evt-list", "description": ""},
            {"id": "evt-nokey", "description": ""},
            {"id": "evt-server", "description": ""},
        ])

        def fetch(url, timeout):
            if url.endswith("evt-time"):
                raise TimeoutError("timed out")
            if url.endswith("evt-json"):
                raise json.JSONDecodeError("Expecting value", "", 0)
            if url.endswith("evt-list"):
                return ["not", "an", "object"]
            if url.endswith("evt-server"):
                raise urllib.error.HTTPError(url, 500, "Server Error", Message(), None)
            return {"guest_count": 3}

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 3, out)
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=5 cached=0 fetched=0 empty=0 failed=5 deferred=0",
        )
        self.assertEqual(self.load(cache), {})
        self.assertEqual(self.load(os.path.join(work, "luma-details.json"))["details"], {})

    def test_null_mirror_is_empty_and_cached(self):
        """source: a null description_mirror, or one whose text is empty, is empty and cached as ''."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-null", "description": ""},
            {"id": "evt-blankdoc", "description": ""},
            {"id": "evt-bool", "description": ""},
        ])

        def fetch(url, timeout):
            if url.endswith("evt-null"):
                return {"description_mirror": None, "guest_count": 5, "hosts": [{"name": "A"}]}
            if url.endswith("evt-blankdoc"):
                return {
                    "description_mirror": {"type": "doc", "content": [{"type": "paragraph"}]},
                    "guest_count": 0,
                }
            return {"description_mirror": None, "guest_count": True}

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=3 cached=0 fetched=0 empty=3 failed=0 deferred=0",
        )
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        saved = self.load(cache)
        self.assertEqual(sidecar["details"]["evt-null"]["description"], "")
        self.assertEqual(sidecar["details"]["evt-null"]["guest_count"], 5)
        self.assertNotIn("hosts", sidecar["details"]["evt-null"])
        self.assertEqual(saved["evt-null"]["description"], "")
        self.assertRegex(saved["evt-null"]["fetched_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(sidecar["details"]["evt-blankdoc"]["guest_count"], 0)
        self.assertEqual(saved["evt-blankdoc"]["description"], "")
        self.assertIsNone(sidecar["details"]["evt-bool"]["guest_count"])
        self.assertEqual(saved["evt-bool"]["description"], "")

    def test_deferred_beyond_max_fetch(self):
        """source: after max-fetch network requests the remaining uncached ids are deferred."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-a", "description": ""},
            {"id": "evt-b", "description": ""},
            {"id": "evt-c", "description": ""},
        ])
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return payload_for("hello", guest_count=1)

        code, out = self.execute(work, cache, fetch, max_fetch=1)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 1)
        self.assertIn("event_api_id=evt-a", calls[0])
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=3 cached=0 fetched=1 empty=0 failed=0 deferred=2",
        )
        details = self.load(os.path.join(work, "luma-details.json"))["details"]
        self.assertEqual(list(details.keys()), ["evt-a"])

    def test_failed_fetches_count_toward_max_fetch(self):
        """source: a failed response still counts as one of the max-fetch network requests."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-bad", "description": ""},
            {"id": "evt-later", "description": ""},
        ])
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            raise urllib.error.HTTPError(url, 400, "Bad Request", Message(), None)

        code, out = self.execute(work, cache, fetch, max_fetch=1)
        self.assertEqual(code, 3, out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=2 cached=0 fetched=0 empty=0 failed=1 deferred=1",
        )

    def test_exit_3_when_every_id_fails(self):
        """source: when every id fails, the process exits 3 after reporting the counts."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-one", "description": ""},
            {"id": "evt-two", "description": ""},
        ])

        def fetch(url, timeout):
            raise urllib.error.HTTPError(url, 400, "Bad Request", Message(), None)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 3, out)
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=2 cached=0 fetched=0 empty=0 failed=2 deferred=0",
        )

    def test_exit_0_with_zero_ids(self):
        """source: a run with no blank Luma ids exits 0 and still writes an empty sidecar."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-m-meetup.json"), [
            {"id": "evt-frommeetup", "description": ""},
        ])
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-filled", "description": "has text"},
        ])
        write_json(os.path.join(work, "items-object-luma.json"), {"id": "evt-object"})
        with open(os.path.join(work, "items-bad-luma.json"), "w", encoding="utf-8") as handle:
            handle.write("{")
        code, out = self.execute(work, cache, forbid_network)
        self.assertEqual(code, 0, out)
        self.assertEqual(
            out,
            "LUMA-DETAILS: ids=0 cached=0 fetched=0 empty=0 failed=0 deferred=0\n",
        )
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(sidecar["schema"], 1)
        self.assertEqual(sidecar["details"], {})
        self.assertEqual(sidecar["counts"]["ids"], 0)

    def test_writes_leave_no_temp_file(self):
        """source: the sidecar and the cache are replaced into place, so no temp file remains."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-one", "description": ""},
        ])

        def fetch(url, timeout):
            return payload_for("hello", guest_count=4)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertFalse(temp_names(work))
        self.assertFalse(temp_names(os.path.dirname(cache)))
        self.assertTrue(os.path.isfile(os.path.join(work, "luma-details.json")))
        self.assertTrue(os.path.isfile(cache))
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(sidecar["schema"], 1)
        self.assertEqual(sidecar["details"]["evt-one"]["description"], "hello")
        self.assertEqual(sidecar["details"]["evt-one"]["guest_count"], 4)
        self.assertNotIn("hosts", sidecar["details"]["evt-one"])

    def test_stdout_line_matches_the_sidecar_counts(self):
        """source: the only stdout line is LUMA-DETAILS with the six counts the sidecar file records."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-one", "description": ""},
        ])

        def fetch(url, timeout):
            return payload_for("hello", guest_count=4)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("\n"), 1)
        match = LINE.match(out.strip())
        self.assertIsNotNone(match)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        counts = sidecar["counts"]
        self.assertEqual(
            [int(group) for group in match.groups()],
            [counts["ids"], counts["cached"], counts["fetched"],
             counts["empty"], counts["failed"], counts["deferred"]],
        )
        self.assertRegex(sidecar["generated_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_sleeps_between_network_requests(self):
        """source: network requests are spaced by the delay; a cache hit is not a request."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-one", "description": ""},
            {"id": "evt-two", "description": ""},
        ])
        slept = []
        original = luma_details.time.sleep
        luma_details.time.sleep = lambda seconds: slept.append(seconds)
        self.addCleanup(setattr, luma_details.time, "sleep", original)

        def fetch(url, timeout):
            return payload_for("hello")

        code, out = self.execute(work, cache, fetch, delay=0.25)
        self.assertEqual(code, 0, out)
        self.assertEqual(slept, [0.25])

    def test_unexpected_exception_exits_4(self):
        """source: an unexpected exception prints LUMA-DETAILS: error <type>: <message> and exits 4."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-one", "description": ""},
        ])

        def fetch(url, timeout):
            raise RuntimeError("boom")

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 4)
        self.assertEqual(out, "LUMA-DETAILS: error RuntimeError: boom\n")

    def test_incomplete_read_is_a_failed_fetch(self):
        """source: http.client.IncompleteRead is one failed fetch, and the other ids still write both files."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-bad", "description": ""},
            {"id": "evt-ok1", "description": ""},
            {"id": "evt-ok2", "description": ""},
        ])
        self.stub_sleep()

        def fetch(url, timeout):
            if url.endswith("evt-bad"):
                raise http.client.IncompleteRead(b"partial", 20)
            return payload_for("kept", guest_count=3)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=3 cached=0 fetched=2 empty=0 failed=1 deferred=0",
        )
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        saved = self.load(cache)
        self.assertEqual(set(sidecar["details"]), {"evt-ok1", "evt-ok2"})
        self.assertEqual(set(saved), {"evt-ok1", "evt-ok2"})
        self.assertEqual(sidecar["details"]["evt-ok1"]["description"], "kept")
        self.assertEqual(saved["evt-ok2"]["description"], "kept")

    def test_cache_saved_every_25_fetches(self):
        """source: a kill during the fetch loop keeps the sidecar and the cache written every 25 network fetches."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-n%02d" % n, "description": ""} for n in range(1, 27)
        ])
        held = {}
        self.stub_sleep()

        def fetch(url, timeout):
            seen = held.setdefault("calls", [])
            seen.append(url)
            if len(seen) == 26:
                held["cache"] = self.load(cache)
                held["side"] = self.load(os.path.join(work, "luma-details.json"))
                raise KeyboardInterrupt
            return payload_for("row", guest_count=1)

        with self.assertRaises(KeyboardInterrupt):
            self.execute(work, cache, fetch)
        wanted = ["evt-n%02d" % n for n in range(1, 26)]
        self.assertEqual(sorted(held["cache"]), wanted)
        self.assertEqual(sorted(held["side"]["details"]), wanted)
        self.assertNotIn("evt-n26", held["cache"])

    def test_breaker_after_five_consecutive_failures(self):
        """source: five consecutive failed fetches trip the breaker, later uncached ids are deferred, and the line prints once."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-f%d" % n, "description": ""} for n in range(1, 8)
        ])
        calls = []
        self.stub_sleep()

        def fetch(url, timeout):
            calls.append(url)
            raise urllib.error.HTTPError(url, 400, "Bad Request", Message(), None)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 3, out)
        self.assertEqual(len(calls), 5)
        self.assertEqual(out.count("LUMA-DETAILS: breaker after 5 consecutive failures"), 1)
        self.assertIn(
            "LUMA-DETAILS: ids=7 cached=0 fetched=0 empty=0 failed=5 deferred=2",
            out,
        )
        self.assertEqual(self.load(os.path.join(work, "luma-details.json"))["details"], {})

    def test_http_429_sleeps_five_seconds_once(self):
        """source: an HTTP 429 sleeps 5 seconds once per run, however many 429 responses follow."""
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-r%d" % n, "description": ""} for n in range(1, 5)
        ])
        calls = []
        slept = []
        self.stub_sleep(slept)

        def fetch(url, timeout):
            calls.append(url)
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", Message(), None)

        code, out = self.execute(work, cache, fetch, delay=0)
        self.assertEqual(code, 3, out)
        self.assertEqual(len(calls), 4)
        self.assertEqual([seconds for seconds in slept if seconds == 5], [5])
        self.assertNotIn("breaker", out)

    def test_empty_description_refetched_after_three_days(self):
        """source: an empty description 4 days old is fetched again; a non-empty one 4 days old is reused."""
        work, cache = self.work()
        empty_at = stamp(4)
        kept_at = stamp(4)
        write_json(cache, {
            "evt-empty": {"description": "", "guest_count": None, "fetched_at": empty_at},
            "evt-full": {"description": "kept text", "guest_count": 2, "fetched_at": kept_at},
        })
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-empty", "description": ""},
            {"id": "evt-full", "description": ""},
        ])
        calls = []
        self.stub_sleep()

        def fetch(url, timeout):
            calls.append(url)
            return payload_for("from network", guest_count=1)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 1)
        self.assertIn("event_api_id=evt-empty", calls[0])
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        saved = self.load(cache)
        self.assertEqual(sidecar["details"]["evt-empty"]["description"], "from network")
        self.assertEqual(sidecar["details"]["evt-full"]["description"], "kept text")
        self.assertEqual(saved["evt-full"]["fetched_at"], kept_at)
        self.assertNotEqual(saved["evt-empty"]["fetched_at"], empty_at)
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=2 cached=1 fetched=1 empty=0 failed=0 deferred=0",
        )

    def test_saved_cache_drops_old_and_bad_entries(self):
        """source: a save drops cache entries older than 21 days and entries whose fetched_at cannot be parsed."""
        work, cache = self.work()
        keep_at = stamp(5)
        write_json(cache, {
            "evt-old": {"description": "too old", "guest_count": 1, "fetched_at": stamp(22)},
            "evt-bad": {"description": "bad clock", "guest_count": 2, "fetched_at": "not-a-time"},
            "evt-keep": {"description": "still good", "guest_count": 3, "fetched_at": keep_at},
        })
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-new", "description": ""},
        ])
        self.stub_sleep()

        def fetch(url, timeout):
            return payload_for("fresh", guest_count=4)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        saved = self.load(cache)
        self.assertNotIn("evt-old", saved)
        self.assertNotIn("evt-bad", saved)
        self.assertEqual(saved["evt-keep"]["description"], "still good")
        self.assertEqual(saved["evt-keep"]["fetched_at"], keep_at)
        self.assertEqual(saved["evt-new"]["description"], "fresh")

    def test_failed_rename_keeps_the_old_file_and_leaves_no_temp_file(self):
        """source: write_json builds a temp file and renames it, so a failure never half-writes the target."""
        work, _cache = self.work()
        target = os.path.join(work, "out.json")
        write_json(target, {"old": True})
        original = os.replace

        def refuse(src, dst):
            raise OSError("rename refused")

        os.replace = refuse
        self.addCleanup(setattr, os, "replace", original)
        with self.assertRaises(OSError):
            luma_details.write_json(target, {"new": True})
        os.replace = original
        self.assertEqual(self.load(target), {"old": True})
        self.assertEqual(os.listdir(work), ["out.json"])

    def test_http_404_is_cached_empty_and_does_not_trip_breaker(self):
        # source: permanent 404s must not trip the failure breaker on every run
        work, cache = self.work()
        ids = ["evt-m1", "evt-m2", "evt-m3", "evt-m4", "evt-m5", "evt-m6", "evt-keep"]
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": event_id, "description": ""} for event_id in ids
        ])
        calls = []
        self.stub_sleep()

        def fetch(url, timeout):
            calls.append(url)
            if url.endswith("evt-keep"):
                return payload_for("kept text", guest_count=4)
            raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertNotIn("LUMA-DETAILS: breaker", out)
        self.assertEqual(len(calls), 7)
        self.assertIn("event_api_id=evt-keep", calls[-1])
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=7 cached=0 fetched=1 empty=6 failed=0 deferred=0",
        )
        saved = self.load(cache)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(sorted(saved), sorted(ids))
        self.assertEqual(sorted(sidecar["details"]), sorted(ids))
        for event_id in ids[:-1]:
            self.assertEqual(saved[event_id]["description"], "")
            self.assertEqual(sidecar["details"][event_id]["description"], "")
        self.assertEqual(saved["evt-keep"]["description"], "kept text")
        self.assertEqual(sidecar["details"]["evt-keep"]["description"], "kept text")

    def test_http_410_is_cached_empty(self):
        # source: permanent 404s must not trip the failure breaker on every run
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-gone", "description": ""},
        ])
        self.stub_sleep()

        def fetch(url, timeout):
            raise urllib.error.HTTPError(url, 410, "Gone", Message(), None)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertNotIn("LUMA-DETAILS: breaker", out)
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=1 cached=0 fetched=0 empty=1 failed=0 deferred=0",
        )
        saved = self.load(cache)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(saved["evt-gone"]["description"], "")
        self.assertEqual(sidecar["details"]["evt-gone"]["description"], "")

    def test_http_403_still_trips_the_breaker(self):
        # source: permanent 404s must not trip the failure breaker on every run
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-d%d" % n, "description": ""} for n in range(1, 8)
        ])
        calls = []
        self.stub_sleep()

        def fetch(url, timeout):
            calls.append(url)
            raise urllib.error.HTTPError(url, 403, "Forbidden", Message(), None)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 3, out)
        self.assertEqual(len(calls), 5)
        self.assertEqual(out.count("LUMA-DETAILS: breaker after 5 consecutive failures"), 1)
        self.assertIn(
            "LUMA-DETAILS: ids=7 cached=0 fetched=0 empty=0 failed=5 deferred=2",
            out,
        )
        self.assertEqual(self.load(cache), {})
        self.assertEqual(self.load(os.path.join(work, "luma-details.json"))["details"], {})

    def test_ten_404s_with_no_good_answer_trip_the_gone_breaker(self):
        # source: a mass 404 must trip a breaker instead of caching every event blank
        work, cache = self.work()
        ids = ["evt-g%02d" % n for n in range(1, 12)]
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": event_id, "description": ""} for event_id in ids
        ])
        calls = []
        self.stub_sleep()

        def fetch(url, timeout):
            calls.append(url)
            if url.endswith("evt-g11"):
                return payload_for("later text", guest_count=4)
            raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 3, out)
        self.assertEqual(len(calls), 10)
        self.assertEqual(
            out.count("LUMA-DETAILS: breaker after 10 answers of 404 or 410 and no good answer"),
            1,
        )
        self.assertEqual(
            out,
            "LUMA-DETAILS: breaker after 10 answers of 404 or 410 and no good answer\n"
            "LUMA-DETAILS: ids=11 cached=0 fetched=0 empty=0 failed=10 deferred=1\n",
        )
        saved = self.load(cache)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        # the ten held ids are written to the cache as empty entries
        # so the next run starts with later ids; they stay out of the sidecar
        for event_id in ids[:10]:
            self.assertEqual(saved[event_id]["description"], "")
            self.assertIsNone(saved[event_id]["guest_count"])
            self.assertNotIn(event_id, sidecar["details"])
        self.assertNotIn("evt-g11", saved)
        self.assertNotIn("evt-g11", sidecar["details"])
        self.assertEqual(sidecar["details"], {})

    def test_second_run_after_the_breaker_starts_with_later_ids(self):
        # source: a tripped breaker re-asked the same ten ids every run
        work, cache = self.work()
        ids = ["evt-g%02d" % n for n in range(1, 12)]
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": event_id, "description": ""} for event_id in ids
        ])
        self.stub_sleep()

        def fetch_one(url, timeout):
            if url.endswith("evt-g11"):
                return payload_for("later text", guest_count=4)
            raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

        code, out = self.execute(work, cache, fetch_one)
        self.assertEqual(code, 3, out)
        calls = []

        def fetch_two(url, timeout):
            calls.append(url)
            return fetch_one(url, timeout)

        code, out = self.execute(work, cache, fetch_two)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 1, calls)
        self.assertTrue(calls[0].endswith("evt-g11"), calls)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(sidecar["details"]["evt-g11"]["description"], "later text")

    def test_nine_404s_then_a_good_answer_cache_all(self):
        # source: a mass 404 must trip a breaker instead of caching every event blank
        work, cache = self.work()
        ids = ["evt-a%02d" % n for n in range(1, 10)] + ["evt-aok"]
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": event_id, "description": ""} for event_id in ids
        ])
        self.stub_sleep()

        def fetch(url, timeout):
            if url.endswith("evt-aok"):
                return payload_for("real text", guest_count=6)
            raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertNotIn("LUMA-DETAILS: breaker", out)
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=10 cached=0 fetched=1 empty=9 failed=0 deferred=0",
        )
        saved = self.load(cache)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(sorted(saved), sorted(ids))
        self.assertEqual(sorted(sidecar["details"]), sorted(ids))
        for event_id in ids[:-1]:
            self.assertEqual(saved[event_id]["description"], "")
            self.assertIsNone(saved[event_id]["guest_count"])
            self.assertEqual(sidecar["details"][event_id]["description"], "")
            self.assertIsNone(sidecar["details"][event_id]["guest_count"])
        self.assertEqual(saved["evt-aok"]["description"], "real text")
        self.assertEqual(sidecar["details"]["evt-aok"]["description"], "real text")

    def test_good_answer_first_then_twelve_404s_cache_blank(self):
        # source: a mass 404 must trip a breaker instead of caching every event blank
        work, cache = self.work()
        ids = ["evt-first"] + ["evt-b%02d" % n for n in range(1, 13)]
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": event_id, "description": ""} for event_id in ids
        ])
        self.stub_sleep()

        def fetch(url, timeout):
            if url.endswith("evt-first"):
                return payload_for("real text", guest_count=8)
            raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertNotIn("LUMA-DETAILS: breaker", out)
        self.assertEqual(
            out.strip(),
            "LUMA-DETAILS: ids=13 cached=0 fetched=1 empty=12 failed=0 deferred=0",
        )
        saved = self.load(cache)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(sorted(saved), sorted(ids))
        self.assertEqual(sorted(sidecar["details"]), sorted(ids))
        self.assertEqual(saved["evt-first"]["description"], "real text")
        self.assertEqual(sidecar["details"]["evt-first"]["description"], "real text")
        for event_id in ids[1:]:
            self.assertEqual(saved[event_id]["description"], "")
            self.assertIsNone(saved[event_id]["guest_count"])
            self.assertEqual(sidecar["details"][event_id]["description"], "")

    def test_gone_breaker_with_a_warm_cache_keeps_cached_text(self):
        # source: a mass 404 must trip a breaker instead of caching every event blank
        work, cache = self.work()
        warm = ["evt-warm1", "evt-warm2"]
        cold = ["evt-c%02d" % n for n in range(1, 11)]
        write_json(cache, {
            "evt-warm1": {"description": "warm one", "guest_count": 3, "fetched_at": stamp(1)},
            "evt-warm2": {"description": "warm two", "guest_count": 4, "fetched_at": stamp(1)},
        })
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": event_id, "description": ""} for event_id in warm + cold
        ])
        calls = []
        self.stub_sleep()

        def fetch(url, timeout):
            calls.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 10)
        self.assertEqual(
            out.count("LUMA-DETAILS: breaker after 10 answers of 404 or 410 and no good answer"),
            1,
        )
        self.assertEqual(
            out,
            "LUMA-DETAILS: breaker after 10 answers of 404 or 410 and no good answer\n"
            "LUMA-DETAILS: ids=12 cached=2 fetched=0 empty=0 failed=10 deferred=0\n",
        )
        saved = self.load(cache)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(sidecar["details"]["evt-warm1"]["description"], "warm one")
        self.assertEqual(sidecar["details"]["evt-warm2"]["description"], "warm two")
        self.assertEqual(saved["evt-warm1"]["description"], "warm one")
        self.assertEqual(saved["evt-warm2"]["description"], "warm two")
        # the held cold ids are cached as empty entries, not in the sidecar
        for event_id in cold:
            self.assertEqual(saved[event_id]["description"], "")
            self.assertNotIn(event_id, sidecar["details"])

    def test_old_text_survives_a_failed_refetch(self):
        # source: a 30 day old description was dropped the day Luma failed
        work, cache = self.work()
        write_json(cache, {
            "evt-stale": {"description": "old words", "guest_count": 9, "fetched_at": stamp(30)},
        })
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-stale", "description": ""},
            {"id": "evt-good", "description": ""},
        ])
        self.stub_sleep()

        def fetch(url, timeout):
            if url.endswith("evt-stale"):
                raise urllib.error.HTTPError(url, 500, "Server Error", Message(), None)
            return payload_for("fresh words", guest_count=2)

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 0, out)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(sidecar["details"]["evt-stale"]["description"], "old words")
        self.assertEqual(sidecar["details"]["evt-stale"]["guest_count"], 9)
        self.assertEqual(sidecar["counts"]["failed"], 1)
        saved = self.load(cache)
        self.assertEqual(saved["evt-stale"]["description"], "old words")

    def test_old_text_survives_a_deferred_refetch(self):
        # source: deferred ids kept their text only while it was under 21 days old
        work, cache = self.work()
        write_json(cache, {
            "evt-stale": {"description": "old words", "guest_count": 9, "fetched_at": stamp(30)},
        })
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-stale", "description": ""},
        ])
        self.stub_sleep()
        code, out = self.execute(work, cache, forbid_network, max_fetch=0)
        self.assertEqual(code, 0, out)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(sidecar["details"]["evt-stale"]["description"], "old words")
        self.assertEqual(sidecar["counts"]["deferred"], 1)
        self.assertEqual(self.load(cache)["evt-stale"]["description"], "old words")

    def test_old_entry_whose_id_is_not_in_this_run_is_dropped(self):
        # source: the keep rule covers ids of this run only
        work, cache = self.work()
        write_json(cache, {
            "evt-stale": {"description": "old words", "guest_count": 9, "fetched_at": stamp(30)},
        })
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-other", "description": ""},
        ])
        self.stub_sleep()
        code, out = self.execute(work, cache, lambda url, timeout: payload_for("x"))
        self.assertEqual(code, 0, out)
        self.assertNotIn("evt-stale", self.load(cache))
        self.assertNotIn("evt-stale", self.load(os.path.join(work, "luma-details.json"))["details"])

    def test_prune_cache_keeps_only_old_text_of_this_runs_ids(self):
        # source: prune_cache gets the ids of this run as a third argument
        now = datetime.now(timezone.utc)
        cache = {
            "evt-a": {"description": "text", "fetched_at": stamp(40)},
            "evt-b": {"description": "", "fetched_at": stamp(40)},
            "evt-c": {"description": "text", "fetched_at": "not-a-time"},
            "evt-d": {"description": "text", "fetched_at": stamp(40)},
        }
        luma_details.prune_cache(cache, now, ["evt-a", "evt-b", "evt-c"])
        self.assertEqual(sorted(cache), ["evt-a", "evt-c"])

    def test_unplanned_error_saves_progress_before_exit_4(self):
        # source: an unexpected error lost every fetch since the last 25th
        work, cache = self.work()
        ids = ["evt-p%02d" % n for n in range(1, 11)]
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": event_id, "description": ""} for event_id in ids
        ])
        self.stub_sleep()
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            if len(calls) == 8:
                raise RuntimeError("eighth")
            return payload_for("text %d" % len(calls))

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 4, out)
        self.assertEqual(out, "LUMA-DETAILS: error RuntimeError: eighth\n")
        saved = self.load(cache)
        self.assertEqual(sorted(saved), ids[:7])
        for position, event_id in enumerate(ids[:7], 1):
            self.assertEqual(saved[event_id]["description"], "text %d" % position)
        sidecar = self.load(os.path.join(work, "luma-details.json"))
        self.assertEqual(sorted(sidecar["details"]), ids[:7])

    def test_max_fetch_zero_with_an_empty_cache_exits_0(self):
        # source: a run that asked Luma for nothing is not a failure
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-one", "description": ""},
        ])
        code, out = self.execute(work, cache, forbid_network, max_fetch=0)
        self.assertEqual(code, 0, out)
        self.assertEqual(
            out, "LUMA-DETAILS: ids=1 cached=0 fetched=0 empty=0 failed=0 deferred=1\n")

    def test_every_request_failing_still_exits_3(self):
        # source: exit 3 stays for a run that asked and got nothing usable
        work, cache = self.work()
        write_json(os.path.join(work, "items-city-luma.json"), [
            {"id": "evt-one", "description": ""},
        ])
        self.stub_sleep()

        def fetch(url, timeout):
            raise urllib.error.URLError("down")

        code, out = self.execute(work, cache, fetch)
        self.assertEqual(code, 3, out)

    def test_negative_or_zero_options_are_refused(self):
        # source: a negative delay crashed sleep and a zero timeout never waits
        for flags in (["--max-fetch", "-1"], ["--delay", "-0.5"], ["--delay", "nan"],
                      ["--timeout", "0"], ["--timeout", "-3"]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as caught:
                    luma_details.parse_args(["work-folder"] + flags)
            self.assertEqual(caught.exception.code, 2, flags)
        args = luma_details.parse_args(["work-folder", "--max-fetch", "0", "--delay", "0",
                                        "--timeout", "0.5"])
        self.assertEqual((args.max_fetch, args.delay, args.timeout), (0, 0, 0.5))


def paragraph_with_mark():
    return {"type": "doc", "content": [{
        "type": "paragraph",
        "content": [{
            "type": "text",
            "text": "link",
            "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}],
        }],
    }]}


def temp_names(folder):
    return [name for name in os.listdir(folder) if ".tmp" in name]


if __name__ == "__main__":
    unittest.main()
