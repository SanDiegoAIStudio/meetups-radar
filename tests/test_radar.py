"""End-to-end tests for radar.sh against a local stand-in for Apify.

Each run uses temporary RADAR_OUT and RADAR_WORK folders, no pause between status checks and
no Luma description fetches. Nothing leaves the machine. Run: python3 -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stub_apify import RUN_COST, StubApify  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "radar.sh")
TOKEN = "test-token-value"
LUMA = "matyascimbulka~luma-event-scraper"
MEETUP = "filip_cicvarek~meetup-scraper"
EVENTBRITE = "aitorsm~eventbrite"

with open(os.path.join(ROOT, "cities.json"), encoding="utf-8") as _fh:
    ACTIVE = json.load(_fh)["active"]


def small_cities():
    """Two cities with few keywords, for the tests that do not need all 44 legs."""
    by_slug = {c["slug"]: c for c in ACTIVE}
    sd = dict(by_slug["san-diego"], kws=["AI", "python"])
    oc = dict(by_slug["orange-county"], kws=["AI"])
    return [sd, oc]


def expected_starts(day_of_month, active=None):
    """The (actor, body) pairs radar.sh must send, derived from cities.json alone."""
    out = []
    for c in (ACTIVE if active is None else active):
        if c.get("luma_city"):
            out.append((LUMA, {"slugs": ["ai", "tech"], "cities": [c["luma_city"]],
                               "maxEventsPerCity": 80}))
        for kw in c["kws"]:
            m = c["meetup"]
            out.append((MEETUP, {"mode": "events", "searchKeyword": kw, "city": m["city"],
                                 "state": m["state"], "country": "us", "radius": m["radius"],
                                 "sortBy": "DATETIME", "maxResults": 40}))
        if isinstance(c.get("eventbrite"), dict) and day_of_month <= 7:
            out.append((EVENTBRITE, {"country": "united-states", "city": c["eventbrite"]["city"],
                                     "category": "custom", "keyword": "artificial intelligence",
                                     "maxResults": 50, "enrichOrganizers": False}))
    return out


def canon(pairs):
    return sorted((a, json.dumps(b, sort_keys=True)) for a, b in pairs)


def snapshot(folder):
    seen = []
    for base, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for n in names:
            if not n.endswith(".pyc"):
                seen.append(os.path.relpath(os.path.join(base, n), folder))
    return sorted(seen)


class Runner:
    """Starts a stub, runs radar.sh once and keeps everything the tests look at."""

    def __init__(self, today="2026-11-02", token=TOKEN, setup=None, stub_setup=None,
                 env_extra=None, path_dir=None, small=False, cities_doc=None,
                 curl_log=None, wait=True):
        self.tmp = tempfile.mkdtemp(prefix="radar-run-")
        script = SCRIPT
        if small or cities_doc is not None:
            script = self._small_copy(cities_doc)
        self.script = script
        self.out = os.path.join(self.tmp, "out")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        self.stub = StubApify().start()
        if stub_setup:
            stub_setup(self.stub)
        if setup:
            setup(self)
        self.bin = path_dir or self._tool_dir(curl_log)
        env = {"PATH": self.bin, "HOME": self.tmp,
               "RADAR_OUT": self.out, "RADAR_WORK": self.work,
               "APIFY_API_BASE": self.stub.base,
               "RADAR_POLL_SECONDS": "0", "RADAR_START_SLEEP": "0",
               "RADAR_LUMA_MAX_FETCH": "0", "RADAR_TODAY": today,
               "PYTHONDONTWRITEBYTECODE": "1"}
        if token is not None:
            env["APIFY_TOKEN"] = token
        env.update(env_extra or {})
        self.env = env
        self.before = snapshot(ROOT)
        self.popen = None
        if wait:
            self.proc = subprocess.run(["/bin/bash", script], env=env, capture_output=True,
                                       text=True, timeout=240)
            self._after()
        else:
            self.popen = subprocess.Popen(["/bin/bash", script], env=env, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True)

    def _after(self):
        self.repo_changed = snapshot(ROOT) != self.before
        self.stub.stop()

    def finish(self):
        """Wait for a run started with wait=False and collect its result."""
        stdout, stderr = self.popen.communicate(timeout=240)
        self.proc = subprocess.CompletedProcess(self.popen.args, self.popen.returncode,
                                                stdout, stderr)
        self._after()

    def second_run(self):
        """Another radar.sh with the same folders and settings, run to its end."""
        return subprocess.run(["/bin/bash", self.script], env=self.env, capture_output=True,
                              text=True, timeout=60)

    def _small_copy(self, cities_doc=None):
        """A copy of the scripts beside a two-city cities.json, so a run needs few legs."""
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        for name in ("radar.sh", "start_leg.sh", "lock.sh", "luma_details.py",
                     "normalize_events.py", "groups.json"):
            shutil.copy(os.path.join(ROOT, name), os.path.join(repo, name))
        with open(os.path.join(repo, "cities.json"), "w", encoding="utf-8") as fh:
            json.dump(cities_doc if cities_doc is not None else {"active": small_cities()}, fh)
        return os.path.join(repo, "radar.sh")

    def _tool_dir(self, curl_log=None):
        d = os.path.join(self.tmp, "bin")
        os.makedirs(d)
        for tool in ("bash", "curl", "jq", "perl", "sed", "grep", "date", "find", "ls", "tail",
                     "mkdir", "rmdir", "rm", "mv", "cat", "head", "sleep", "printf", "dirname",
                     "tr", "sort", "env", "uname", "wc", "awk", "basename", "cp"):
            found = shutil.which(tool)
            if tool == "curl" and curl_log:
                # A shim that writes its arguments to a file and then runs the real curl with
                # the same arguments and the same standard input.
                shim = os.path.join(d, "curl")
                with open(shim, "w", encoding="utf-8") as fh:
                    fh.write("#!/bin/bash\nprintf '%%s\\n' \"$*\" >> '%s'\nexec '%s' \"$@\"\n"
                             % (curl_log, found))
                os.chmod(shim, 0o755)
                continue
            if found and not os.path.exists(os.path.join(d, tool)):
                os.symlink(found, os.path.join(d, tool))
        os.symlink(sys.executable, os.path.join(d, "python3"))
        return d

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def partial_legs(self, today="2026-11-02"):
        p = os.path.join(self.work, today, "partial-legs.txt")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as fh:
            return sorted(line.strip() for line in fh if line.strip())

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def summary(self):
        lines = [x for x in self.proc.stdout.splitlines() if x.strip()]
        return lines[-1] if lines else ""


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class HappyPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rr = Runner()
        cls.legs = len(expected_starts(2))

    @classmethod
    def tearDownClass(cls):
        cls.rr.close()

    def test_exit_zero_and_summary_line(self):
        self.assertEqual(self.rr.proc.returncode, 0, self.rr.proc.stdout + self.rr.proc.stderr)
        self.assertRegex(self.rr.summary, r"total_radar=\d+ partial_legs=0/%d cost=\$" % self.legs)
        self.assertTrue(self.rr.summary.startswith("san-diego:radar="))

    def test_cost_is_the_sum_of_the_run_costs(self):
        self.assertTrue(self.rr.summary.endswith("cost=$%.5f" % (self.legs * RUN_COST)),
                        self.rr.summary)

    def test_every_start_carries_the_token(self):
        starts = self.rr.stub.starts()
        self.assertEqual(len(starts), self.legs)
        for r in self.rr.stub.requests:
            self.assertEqual(r["token"], TOKEN, r["path"])

    def test_each_city_sends_the_expected_bodies(self):
        got = canon(self.rr.stub.start_bodies())
        self.assertEqual(got, canon(expected_starts(2)))

    def test_leg_count_matches_the_city_file(self):
        self.assertEqual(len(self.rr.stub.starts()), self.legs)
        self.assertEqual(len(self.rr.stub.start_bodies(LUMA)),
                         sum(1 for c in ACTIVE if c.get("luma_city")))
        self.assertEqual(len(self.rr.stub.start_bodies(MEETUP)),
                         sum(len(c["kws"]) for c in ACTIVE))

    def test_a_city_without_luma_city_gets_no_luma_leg(self):
        cities = [b["cities"][0] for _a, b in self.rr.stub.start_bodies(LUMA)]
        self.assertNotIn("Irvine", cities)
        self.assertEqual(len(cities), len(set(cities)))

    def test_items_files_are_written_for_every_leg(self):
        folder = os.path.join(self.rr.work, "2026-11-02")
        items = [n for n in os.listdir(folder) if n.startswith("items-") and n.endswith(".json")]
        self.assertEqual(len(items), self.legs)
        for n in items:
            self.assertIsInstance(load(os.path.join(folder, n)), list)
        self.assertGreater(len(load(os.path.join(folder, "items-san-diego-meetup-AI.json"))), 10)

    def test_one_status_and_one_dataset_request_per_leg(self):
        gets = [r["path"] for r in self.rr.stub.requests if r["method"] == "GET"]
        self.assertEqual(len([g for g in gets if "/actor-runs/" in g]), self.legs)
        self.assertEqual(len([g for g in gets if "/datasets/" in g and "clean=true" in g]),
                         self.legs)

    def test_manifest_lists_every_leg(self):
        with open(os.path.join(self.rr.work, "2026-11-02", "manifest.tsv"), encoding="utf-8") as fh:
            rows = [line.split("\t") for line in fh.read().splitlines()]
        self.assertEqual(len(rows), self.legs)
        self.assertTrue(all(r[3] == "SUCCEEDED" for r in rows))

    def test_no_partial_legs_file_when_every_leg_works(self):
        self.assertEqual(self.rr.partial_legs(), [])

    def test_latest_page_and_feed_exist_under_radar_out(self):
        for name in ("LATEST.md", "ALL-CITIES.json", "LATEST.json"):
            self.assertTrue(os.path.getsize(os.path.join(self.rr.out, name)) > 0, name)
        feed = load(os.path.join(self.rr.out, "ALL-CITIES.json"))
        self.assertIs(feed["partial"], False)
        self.assertGreater(sum(len(c["events"]) for c in feed["cities"]), 20)

    def test_lock_folder_is_released(self):
        self.assertFalse(os.path.exists(os.path.join(self.rr.work, ".radar.lock.d")))

    def test_the_run_writes_only_inside_its_two_folders(self):
        self.assertFalse(self.rr.repo_changed)
        self.assertEqual(sorted(os.listdir(self.rr.tmp)), ["bin", "out", "work"])

    def test_no_token_in_output_or_logs(self):
        self.assertNotIn(TOKEN, self.rr.proc.stdout + self.rr.proc.stderr)
        log = os.path.join(self.rr.work, "2026-11-02", "radar.log")
        if os.path.exists(log):
            with open(log, encoding="utf-8") as fh:
                self.assertNotIn(TOKEN, fh.read())


class PartialLegs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sd_python = {"actor": MEETUP, "body": {"searchKeyword": "python", "city": "San Diego"}}
        la_luma = {"actor": LUMA, "body": {"cities": ["Los Angeles"]}}
        lv_robotics = {"actor": MEETUP, "body": {"searchKeyword": "robotics", "city": "Las Vegas"}}
        oc_ai = {"actor": MEETUP, "body": {"searchKeyword": "AI", "city": "Irvine"}}

        def stub_setup(stub):
            stub.start_402 = [sd_python]
            stub.failed_run = [la_luma]
            stub.bad_dataset = [lv_robotics]
            stub.start_500_once = [oc_ai]

        cls.rr = Runner(stub_setup=stub_setup)
        cls.legs = len(expected_starts(2))

    @classmethod
    def tearDownClass(cls):
        cls.rr.close()

    def test_exit_zero_and_three_partial_legs(self):
        self.assertEqual(self.rr.proc.returncode, 0, self.rr.proc.stdout + self.rr.proc.stderr)
        self.assertIn("partial_legs=3/%d" % self.legs, self.rr.summary)

    def test_each_failure_lands_in_partial_legs(self):
        self.assertEqual(self.rr.partial_legs(), sorted([
            "san-diego-meetup-python", "los-angeles-luma", "las-vegas-meetup-robotics"]))

    def test_a_402_start_is_not_retried(self):
        hits = [b for _a, b in self.rr.stub.start_bodies(MEETUP)
                if b["searchKeyword"] == "python" and b["city"] == "San Diego"]
        self.assertEqual(len(hits), 1)

    def test_a_failed_run_is_not_downloaded(self):
        fetched = [r for r in self.rr.stub.requests if "/datasets/" in r["path"]]
        # 402 start: no run, so no status and no dataset. FAILED run: status, but no dataset.
        self.assertEqual(len(fetched), self.legs - 2)
        statuses = [r for r in self.rr.stub.requests if "/actor-runs/" in r["path"]]
        self.assertEqual(len(statuses), self.legs - 1)

    def test_a_non_array_dataset_leaves_no_items_file(self):
        folder = os.path.join(self.rr.work, "2026-11-02")
        self.assertFalse(os.path.exists(os.path.join(folder, "items-las-vegas-meetup-robotics.json")))
        self.assertFalse(os.path.exists(os.path.join(folder, ".items-las-vegas-meetup-robotics.tmp")))

    def test_a_500_start_is_retried_and_then_works(self):
        hits = [b for _a, b in self.rr.stub.start_bodies(MEETUP)
                if b["searchKeyword"] == "AI" and b["city"] == "Irvine"]
        self.assertEqual(len(hits), 2)
        self.assertNotIn("orange-county-meetup-AI", self.rr.partial_legs())
        folder = os.path.join(self.rr.work, "2026-11-02")
        self.assertTrue(os.path.exists(os.path.join(folder, "items-orange-county-meetup-AI.json")))
        self.assertIn("NOTE: orange-county-meetup-AI started on try 2 after http 500",
                      self.rr.proc.stdout)

    def test_the_feed_is_stamped_partial(self):
        feed = load(os.path.join(self.rr.out, "ALL-CITIES.json"))
        self.assertIs(feed["partial"], True)
        self.assertEqual(sorted(feed["partial_legs"]), self.rr.partial_legs())
        self.assertTrue(os.path.getsize(os.path.join(self.rr.out, "LATEST.md")) > 0)


class Eventbrite(unittest.TestCase):
    def test_day_7_sends_the_eventbrite_leg(self):
        run = Runner(today="2026-11-07", small=True)
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            bodies = run.stub.start_bodies(EVENTBRITE)
            self.assertEqual(len(bodies), 1)
            self.assertEqual(bodies[0][1]["city"], "san-diego")
            self.assertEqual(canon(run.stub.start_bodies()), canon(expected_starts(7, small_cities())))
            self.assertIn("partial_legs=0/%d" % len(expected_starts(7, small_cities())), run.summary)
        finally:
            run.close()

    def test_day_8_does_not(self):
        run = Runner(today="2026-11-08", small=True)
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            self.assertEqual(run.stub.start_bodies(EVENTBRITE), [])
            self.assertEqual(canon(run.stub.start_bodies()), canon(expected_starts(8, small_cities())))
        finally:
            run.close()


class Guards(unittest.TestCase):
    def test_no_token_exits_2_without_a_request(self):
        run = Runner(token=None)
        try:
            self.assertEqual(run.proc.returncode, 2)
            self.assertIn("APIFY_TOKEN", run.proc.stderr)
            self.assertEqual(run.stub.requests, [])
            self.assertEqual(os.listdir(run.work), [])
        finally:
            run.close()

    def test_empty_token_exits_2(self):
        run = Runner(token="")
        try:
            self.assertEqual(run.proc.returncode, 2)
            self.assertEqual(run.stub.requests, [])
        finally:
            run.close()

    def test_missing_tool_exits_2_and_names_it(self):
        run = Runner(path_dir=_tool_dir_without("jq"))
        try:
            self.assertEqual(run.proc.returncode, 2)
            self.assertIn("missing required tool: jq", run.proc.stderr)
            self.assertEqual(run.stub.requests, [])
        finally:
            shutil.rmtree(os.path.dirname(run.bin), ignore_errors=True)
            run.close()

    def test_malformed_date_exits_2_without_a_request(self):
        run = Runner(today="tomorrow")
        try:
            self.assertEqual(run.proc.returncode, 2)
            self.assertIn("RADAR_TODAY", run.proc.stderr)
            self.assertEqual(run.stub.requests, [])
        finally:
            run.close()

    def test_a_fresh_lock_exits_0_without_a_request(self):
        def lock(run):
            os.makedirs(os.path.join(run.work, ".radar.lock.d"))

        run = Runner(setup=lock)
        try:
            self.assertEqual(run.proc.returncode, 0)
            self.assertIn("another radar run is active, exiting", run.proc.stdout)
            self.assertEqual(run.stub.requests, [])
            self.assertTrue(os.path.isdir(os.path.join(run.work, ".radar.lock.d")))
            self.assertFalse(os.path.exists(run.out))
        finally:
            run.close()

    def test_a_stale_lock_is_reclaimed(self):
        def lock(run):
            d = os.path.join(run.work, ".radar.lock.d")
            os.makedirs(d)
            old = time.time() - 3 * 3600
            os.utime(d, (old, old))

        run = Runner(setup=lock, today="2026-11-08", small=True)
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            self.assertIn("reclaiming stale radar lock", run.proc.stdout)
            self.assertGreater(len(run.stub.starts()), 0)
        finally:
            run.close()

    def test_old_run_folders_are_pruned_to_eight(self):
        def old_runs(run):
            for n in range(1, 12):
                d = os.path.join(run.work, "2026-10-%02d" % n)
                os.makedirs(d)
                stamp = time.time() - (40 - n) * 86400
                os.utime(d, (stamp, stamp))

        run = Runner(setup=old_runs, today="2026-11-08", small=True)
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            folders = [n for n in os.listdir(run.work)
                       if os.path.isdir(os.path.join(run.work, n)) and not n.startswith(".")]
            self.assertEqual(len(folders), 8)
            self.assertIn("2026-11-08", folders)
            self.assertNotIn("2026-10-01", folders)
        finally:
            run.close()

    def _run_with_other_things_in_the_work_folder(self):
        def setup(run):
            # Nine dated folders, oldest first, then things that are not run folders.
            for n in range(1, 10):
                d = os.path.join(run.work, "2026-10-%02d" % n)
                os.makedirs(d)
                stamp = time.time() - (40 - n) * 86400
                os.utime(d, (stamp, stamp))
            keep = os.path.join(run.work, "keep-me")
            os.makedirs(keep)
            with open(os.path.join(keep, "notes.txt"), "w", encoding="utf-8") as fh:
                fh.write("mine\n")
            with open(os.path.join(run.work, "plain-file.txt"), "w", encoding="utf-8") as fh:
                fh.write("mine\n")
            for path in (keep, os.path.join(keep, "notes.txt"), os.path.join(run.work, "plain-file.txt")):
                old = time.time() - 400 * 86400
                os.utime(path, (old, old))

        return Runner(setup=setup, today="2026-11-08", small=True)

    def test_cleanup_only_removes_dated_run_folders(self):
        run = self._run_with_other_things_in_the_work_folder()
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            self.assertTrue(os.path.isfile(os.path.join(run.work, "keep-me", "notes.txt")))
            self.assertTrue(os.path.isfile(os.path.join(run.work, "plain-file.txt")))
            dated = sorted(n for n in os.listdir(run.work)
                           if os.path.isdir(os.path.join(run.work, n))
                           and len(n) == 10 and n[4] == "-" and n[7] == "-")
            self.assertEqual(len(dated), 8, dated)
            self.assertIn("2026-11-08", dated)
        finally:
            run.close()

    def test_cleanup_removes_the_oldest_dated_folders(self):
        run = self._run_with_other_things_in_the_work_folder()
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            names = os.listdir(run.work)
            self.assertNotIn("2026-10-01", names)
            self.assertNotIn("2026-10-02", names)
            for n in range(3, 10):
                self.assertIn("2026-10-%02d" % n, names)
        finally:
            run.close()

    def test_normalize_failure_exits_1(self):
        # RADAR_OUT below a file cannot be created, so normalize_events.py fails.
        run = Runner(env_extra={"RADAR_OUT": "/dev/null/not-a-folder"})
        try:
            self.assertEqual(run.proc.returncode, 1, run.proc.stdout + run.proc.stderr)
            self.assertIn("normalize_events.py failed", run.proc.stdout)
            self.assertNotIn("partial_legs=", run.proc.stdout)
        finally:
            run.close()


def wait_for(condition, seconds=30):
    """Poll a condition until it holds; fail the test when it never does."""
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


def one_city(**changes):
    city = {"slug": "solo", "label": "Solo", "tz": "America/Los_Angeles", "luma_city": None,
            "meetup": {"city": "Solo", "state": "ca", "radius": 10}, "kws": ["AI"]}
    city.update(changes)
    return {"active": [city]}


class ZeroDataKeepsTheFeed(unittest.TestCase):
    """When no leg returns data, the existing feed stays as it was."""

    @staticmethod
    def _feed_in_place(run):
        os.makedirs(run.out)
        for name, text in (("ALL-CITIES.json", '{"old": "feed"}\n'), ("LATEST.md", "old page\n")):
            with open(os.path.join(run.out, name), "w", encoding="utf-8") as fh:
                fh.write(text)

    @staticmethod
    def _old_runs(run, count=11):
        for n in range(1, count + 1):
            d = os.path.join(run.work, "2026-10-%02d" % n)
            os.makedirs(d)
            stamp = time.time() - (40 - n) * 86400
            os.utime(d, (stamp, stamp))
        keep = os.path.join(run.work, "keep-me")
        os.makedirs(keep)
        with open(os.path.join(keep, "notes.txt"), "w", encoding="utf-8") as fh:
            fh.write("mine\n")
        with open(os.path.join(run.work, "plain-file.txt"), "w", encoding="utf-8") as fh:
            fh.write("mine\n")

    def test_every_dataset_not_an_array_leaves_the_feed_as_it_was(self):
        # source: a body that is not a JSON array is not data
        def stub_setup(stub):
            stub.bad_dataset = [{}]

        run = Runner(small=True, today="2026-11-08", setup=self._feed_in_place,
                     stub_setup=stub_setup)
        try:
            self.assertEqual(run.proc.returncode, 1, run.proc.stdout + run.proc.stderr)
            self.assertIn("ERROR: zero legs returned data, the feed was left as it was", run.proc.stdout)
            self.assertEqual(sorted(os.listdir(run.out)), ["ALL-CITIES.json", "LATEST.md"])
            with open(os.path.join(run.out, "ALL-CITIES.json"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), '{"old": "feed"}\n')
        finally:
            run.close()

    def test_every_leg_refused_at_start_leaves_the_feed_as_it_was(self):
        # source: a night with no data overwrote a good feed with an empty one
        def stub_setup(stub):
            stub.start_401 = [{}]

        run = Runner(small=True, today="2026-11-08", setup=self._feed_in_place,
                     stub_setup=stub_setup)
        try:
            self.assertEqual(run.proc.returncode, 1, run.proc.stdout + run.proc.stderr)
            self.assertRegex(run.proc.stdout, r"\d\d:\d\d:\d\d ERROR: zero legs returned data, "
                                              r"the feed was left as it was")
            self.assertEqual(sorted(os.listdir(run.out)), ["ALL-CITIES.json", "LATEST.md"])
            with open(os.path.join(run.out, "ALL-CITIES.json"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), '{"old": "feed"}\n')
            with open(os.path.join(run.out, "LATEST.md"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "old page\n")
            self.assertEqual(run.stub.requests and {r["kind"] for r in run.stub.requests}, {"start"})
            # neither Python step ran: they log into radar.log
            self.assertFalse(os.path.exists(os.path.join(run.work, "2026-11-08", "radar.log")))
            self.assertFalse(os.path.exists(os.path.join(run.work, ".radar.lock.d")))
        finally:
            run.close()

    def test_an_empty_items_list_is_still_data(self):
        # source: items files that hold [] keep exit 0 and write the feed
        def stub_setup(stub):
            stub.bad_dataset = []

        run = Runner(cities_doc=one_city(), today="2026-11-08", stub_setup=stub_setup)
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            self.assertTrue(os.path.getsize(os.path.join(run.out, "ALL-CITIES.json")) > 0)
        finally:
            run.close()

    def test_the_prune_step_runs_when_zero_legs_returned_data(self):
        # source: old run folders piled up on every failed night
        def stub_setup(stub):
            stub.start_401 = [{}]

        run = Runner(small=True, today="2026-11-08", setup=self._old_runs, stub_setup=stub_setup)
        try:
            self.assertEqual(run.proc.returncode, 1, run.proc.stdout + run.proc.stderr)
            self._assert_pruned(run)
        finally:
            run.close()

    def test_the_prune_step_runs_when_the_sorting_step_fails(self):
        # source: a failed sorting step skipped the prune step
        run = Runner(small=True, today="2026-11-08", setup=self._old_runs,
                     env_extra={"RADAR_OUT": "/dev/null/not-a-folder"})
        try:
            self.assertEqual(run.proc.returncode, 1, run.proc.stdout + run.proc.stderr)
            self.assertIn("normalize_events.py failed", run.proc.stdout)
            self._assert_pruned(run)
        finally:
            run.close()

    def _assert_pruned(self, run):
        names = os.listdir(run.work)
        dated = sorted(n for n in names if len(n) == 10 and n[4] == "-" and n[7] == "-")
        self.assertEqual(len(dated), 8, dated)
        self.assertIn("2026-11-08", dated)
        for gone in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"):
            self.assertNotIn(gone, names)
        self.assertTrue(os.path.isfile(os.path.join(run.work, "keep-me", "notes.txt")))
        self.assertTrue(os.path.isfile(os.path.join(run.work, "plain-file.txt")))


class SettingsAreChecked(unittest.TestCase):
    """Nothing starts on a bad token, setting or configuration."""

    def _refused(self, run, needle):
        try:
            self.assertEqual(run.proc.returncode, 2, run.proc.stdout + run.proc.stderr)
            self.assertIn(needle, run.proc.stderr)
            self.assertEqual(run.stub.requests, [])
            self.assertEqual(os.listdir(run.work), [])
            self.assertFalse(os.path.exists(run.out))
        finally:
            run.close()

    def test_a_token_with_a_space_is_refused(self):
        # source: a pasted token with a trailing newline broke every request
        self._refused(Runner(token="abc def"), "whitespace or control character")

    def test_a_token_with_a_newline_is_refused(self):
        self._refused(Runner(token="abc\n"), "whitespace or control character")

    def test_a_setting_that_is_not_a_whole_number_is_refused(self):
        # source: a bad setting turned into an endless or instant loop
        for name, value in (("RADAR_POLL_SECONDS", "abc"), ("RADAR_DEADLINE_SECONDS", "-5"),
                            ("RADAR_START_SLEEP", "1.5"), ("RADAR_LUMA_MAX_FETCH", "x"),
                            ("RADAR_START_TRIES", "two"), ("RADAR_POLL_SECONDS", "1234567890")):
            with self.subTest(name=name, value=value):
                self._refused(Runner(env_extra={name: value}),
                              "%s must be a whole number of 1 to 9 digits" % name)

    def test_start_tries_of_zero_is_refused(self):
        self._refused(Runner(env_extra={"RADAR_START_TRIES": "0"}),
                      "RADAR_START_TRIES must be at least 1")

    def test_an_empty_setting_counts_as_unset(self):
        # source: an exported but empty variable must not stop the run
        run = Runner(cities_doc=one_city(), today="2026-11-08",
                     env_extra={"RADAR_START_TRIES": "", "RADAR_DEADLINE_SECONDS": ""})
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
        finally:
            run.close()

    def test_a_configuration_problem_exits_2_before_any_request(self):
        # source: a bad cities.json was found only after paid runs started
        self._refused(Runner(cities_doc=one_city(slug="Solo City")), "cities.json")

    def test_zero_legs_to_run_exits_2(self):
        # source: a configuration without a leg printed a summary of nothing
        run = Runner(cities_doc=one_city(kws=[]))
        try:
            self.assertEqual(run.proc.returncode, 2, run.proc.stdout + run.proc.stderr)
            self.assertIn("zero legs to run", run.proc.stderr)
            self.assertEqual(run.stub.requests, [])
            self.assertFalse(os.path.exists(os.path.join(run.work, ".radar.lock.d")))
        finally:
            run.close()


class LockInTheRun(unittest.TestCase):
    """The run lock as radar.sh uses it."""

    def test_a_stale_lock_that_cannot_be_removed_exits_1(self):
        # source: return code 2 of lock_take must stop the run
        def lock(run):
            d = os.path.join(run.work, ".radar.lock.d")
            os.makedirs(d)
            with open(os.path.join(d, "stray.txt"), "w", encoding="utf-8") as fh:
                fh.write("x\n")
            old = time.time() - 3 * 3600
            os.utime(d, (old, old))

        run = Runner(setup=lock, small=True)
        try:
            self.assertEqual(run.proc.returncode, 1, run.proc.stdout + run.proc.stderr)
            self.assertIn("ERROR: the stale lock", run.proc.stdout)
            self.assertIn("could not be removed", run.proc.stdout)
            self.assertEqual(run.stub.requests, [])
        finally:
            run.close()

    def test_the_lock_names_the_run_and_stays_fresh_while_it_works(self):
        # source: a slow run lost its lock after 45 minutes and a second run joined it
        def setup(run):
            hold = os.path.join(run.tmp, "hold")
            with open(hold, "w", encoding="utf-8") as fh:
                fh.write("x\n")
            run.hold = hold
            run.stub.status_plans = [{"actor": LUMA, "hold": hold}]

        run = Runner(small=True, today="2026-11-08", setup=setup, wait=False,
                     env_extra={"RADAR_POLL_SECONDS": "1"})
        lockdir = os.path.join(run.work, ".radar.lock.d")
        try:
            self.assertTrue(wait_for(lambda: any(r["kind"] == "status" for r in run.stub.requests)))
            with open(os.path.join(lockdir, "pid"), encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip(), str(run.popen.pid))
            old = time.time() - 50 * 60
            os.utime(lockdir, (old, old))
            self.assertTrue(wait_for(lambda: os.stat(lockdir).st_mtime > time.time() - 120),
                            "the poll loop did not refresh the lock")
            other = run.second_run()
            self.assertEqual(other.returncode, 0, other.stdout + other.stderr)
            self.assertIn("another radar run is active, exiting", other.stdout)
            self.assertTrue(os.path.isdir(lockdir))
            with open(os.path.join(lockdir, "pid"), encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip(), str(run.popen.pid))
            os.remove(run.hold)
            run.finish()
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            self.assertFalse(os.path.exists(lockdir))
        finally:
            if run.popen.poll() is None:
                os.path.exists(run.hold) and os.remove(run.hold)
                run.popen.kill()
                run.popen.communicate()
                run.stub.stop()
            run.close()


class TokenStaysOutOfArguments(unittest.TestCase):
    """The token never appears in a curl argument list."""

    def test_the_token_is_in_no_curl_argument_list_and_every_request_carries_it(self):
        # source: the bearer token sat in curl's argument list for ps to show
        log_dir = tempfile.mkdtemp(prefix="radar-curl-")
        log = os.path.join(log_dir, "curl-args.txt")
        run = Runner(cities_doc=one_city(luma_city="Solo"), today="2026-11-08", curl_log=log)
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            with open(log, encoding="utf-8") as fh:
                args = fh.read()
            self.assertTrue(args.strip(), "the curl shim recorded nothing")
            self.assertNotIn(TOKEN, args)
            self.assertNotIn("Bearer", args)
            kinds = {r["kind"] for r in run.stub.requests}
            self.assertEqual(kinds, {"start", "status", "dataset"})
            for r in run.stub.requests:
                self.assertEqual(r["auth"], "Bearer " + TOKEN, r["path"])
        finally:
            shutil.rmtree(log_dir, ignore_errors=True)
            run.close()


class StatusAndCost(unittest.TestCase):
    """The status answer and the cost sum."""

    def test_a_bad_status_answer_keeps_the_last_good_one(self):
        # source: an HTML 502 page replaced a good status and blanked the cost
        def stub_setup(stub):
            stub.status_plans = [{"actor": MEETUP, "body": {"searchKeyword": "python"},
                                  "sequence": ["RUNNING", "HTML502"], "cost": 0.25}]

        # The deadline is shared by every leg, so it must leave time for the python leg's second
        # poll even on a loaded machine; with one second the run could stop before the bad page
        # was ever served, and the checks below would pass without testing anything.
        run = Runner(small=True, today="2026-11-08", stub_setup=stub_setup,
                     env_extra={"RADAR_POLL_SECONDS": "1", "RADAR_DEADLINE_SECONDS": "5"})
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            python_runs = [r for r in run.stub._runs.values()
                           if r["actor"] == MEETUP and r["body"].get("searchKeyword") == "python"]
            self.assertEqual(len(python_runs), 1)
            self.assertGreaterEqual(python_runs[0]["polls"], 2, "the HTML 502 page was never served")
            legs = len(expected_starts(8, small_cities()))
            self.assertEqual(run.partial_legs("2026-11-08"), ["san-diego-meetup-python"])
            self.assertIn("poll deadline", run.proc.stdout)
            self.assertIn("partial_legs=1/%d" % legs, run.summary)
            self.assertTrue(run.summary.endswith("cost=$%.5f" % ((legs - 1) * RUN_COST + 0.25)),
                            run.summary)
            leftovers = [n for n in os.listdir(os.path.join(run.work, "2026-11-08"))
                         if n.startswith(".st-")]
            self.assertEqual(leftovers, [])
        finally:
            run.close()

    def test_a_status_file_left_by_an_earlier_run_is_cleaned_up(self):
        # source: old .st-*.tmp files were never removed
        def setup(run):
            folder = os.path.join(run.work, "2026-11-08")
            os.makedirs(folder)
            with open(os.path.join(folder, ".st-old-leg.tmp"), "w", encoding="utf-8") as fh:
                fh.write("junk\n")

        run = Runner(cities_doc=one_city(), today="2026-11-08", setup=setup)
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            self.assertFalse(os.path.exists(os.path.join(run.work, "2026-11-08", ".st-old-leg.tmp")))
        finally:
            run.close()

    def test_exponent_costs_are_summed_and_printed_with_five_decimals(self):
        # source: jq prints 1e-05 and the summary showed ?
        def stub_setup(stub):
            stub.status_plans = [
                {"actor": LUMA, "cost": 0.00001},
                {"actor": MEETUP, "body": {"searchKeyword": "python"}, "cost": 0.5},
                {"actor": MEETUP, "body": {"searchKeyword": "AI"}, "no_cost": True},
            ]

        run = Runner(small=True, today="2026-11-08", stub_setup=stub_setup)
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            self.assertTrue(run.summary.endswith("cost=$0.50001"), run.summary)
        finally:
            run.close()

    def test_a_sum_that_jq_prints_in_exponent_form_is_not_a_question_mark(self):
        # source: a sum of 0.000004 and 0.000008 reaches the summary as 1.2e-05
        def stub_setup(stub):
            stub.status_plans = [
                {"actor": LUMA, "cost": 0.000004},
                {"actor": MEETUP, "body": {"searchKeyword": "python"}, "cost": 0.000008},
                {"actor": MEETUP, "no_cost": True},
            ]

        run = Runner(small=True, today="2026-11-08", stub_setup=stub_setup)
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            self.assertTrue(run.summary.endswith("cost=$0.00001"), run.summary)
        finally:
            run.close()

    def test_a_cost_that_is_a_string_is_left_out_of_the_sum(self):
        # source: one string value made the whole cost a question mark
        def stub_setup(stub):
            stub.status_plans = [
                {"actor": MEETUP, "body": {"searchKeyword": "python"}, "cost": "7.5"},
            ]

        run = Runner(small=True, today="2026-11-08", stub_setup=stub_setup)
        try:
            self.assertEqual(run.proc.returncode, 0, run.proc.stdout + run.proc.stderr)
            legs = len(expected_starts(8, small_cities()))
            self.assertTrue(run.summary.endswith("cost=$%.5f" % ((legs - 1) * RUN_COST)),
                            run.summary)
        finally:
            run.close()


def _tool_dir_without(missing):
    d = tempfile.mkdtemp(prefix="radar-bin-")
    inner = os.path.join(d, "bin")
    os.makedirs(inner)
    for tool in ("bash", "curl", "jq", "perl", "sed", "grep", "date", "find", "ls", "tail",
                 "mkdir", "rmdir", "rm", "mv", "cat", "head", "sleep", "printf", "dirname"):
        found = shutil.which(tool)
        if tool != missing and found:
            os.symlink(found, os.path.join(inner, tool))
    os.symlink(sys.executable, os.path.join(inner, "python3"))
    return inner


if __name__ == "__main__":
    unittest.main()
