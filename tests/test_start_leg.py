"""Tests for start_leg.sh, which starts one Apify actor run.

A gateway error used to lose a whole leg for the week.

A start that fails on the transport or with 429 or 5xx is retried. The WARN line names
the HTTP code and the number of tries. A 4xx other than 429 stops.
Run: python3 -m unittest discover -s tests
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASH = "/bin/bash"
JQ = shutil.which("jq")
TS = r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}"
LEG = "san-diego-meetup-hardware"
HTML = b"<html><title>502 Bad Gateway</title><body>502 Bad Gateway</body></html>"
ERR = b'{"error":{"type":"invalid-input"}}'
CURL_STUB = """#!/bin/bash
printf '%s\n' "$*" >> "$CURL_CALLS"
# The header file is read from standard input when the call says -H @-.
case " $* " in
  *" -H @- "*) /bin/cat >> "$CURL_STDIN" ;;
esac
code=000
body=""
status=0
if [ -s "$CURL_QUEUE" ]; then
  line=$(/usr/bin/head -n 1 "$CURL_QUEUE")
  /usr/bin/tail -n +2 "$CURL_QUEUE" > "$CURL_QUEUE.next"
  /bin/mv "$CURL_QUEUE.next" "$CURL_QUEUE"
  code=${line%%$'\t'*}
  rest=${line#*$'\t'}
  body=${rest%%$'\t'*}
  status=${rest#*$'\t'}
fi
out=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "-o" ]; then
    out="$arg"
  fi
  prev="$arg"
done
# Like curl 8.7.1: with no body (timeout, refused) the -o file is left as it was.
if [ -n "$out" ] && [ -n "$body" ] && [ -f "$body" ]; then
  /bin/cp "$body" "$out"
fi
printf '%s' "$code"
exit "$status"
"""
SLEEP_STUB = """#!/bin/bash
printf '%s\n' "$1" >> "$SLEEP_LOG"
"""


def _run_id(name):
    return ('{"data":{"id":"%s"}}' % name).encode("ascii")


class StartLegTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="start-leg-")
        self.stub = os.path.join(self.tmp, "bin")
        os.mkdir(self.stub)
        self.queue = os.path.join(self.tmp, "queue")
        self.calls_log = os.path.join(self.tmp, "calls")
        self.sleep_log = os.path.join(self.tmp, "sleeps")
        for path in (self.queue, self.calls_log, self.sleep_log):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("")
        self._write_stub("curl", CURL_STUB)
        self._write_stub("sleep", SLEEP_STUB)
        jq_dir = os.path.dirname(JQ)
        parts = [self.stub, "/usr/bin", "/bin"]
        if jq_dir not in parts:
            parts.append(jq_dir)
        self.env = dict(os.environ)
        self.env["PATH"] = ":".join(parts)
        self.env["CURL_QUEUE"] = self.queue
        self.env["CURL_CALLS"] = self.calls_log
        self.curl_stdin = os.path.join(self.tmp, "curl-stdin")
        with open(self.curl_stdin, "w", encoding="utf-8") as handle:
            handle.write("")
        self.env["CURL_STDIN"] = self.curl_stdin
        self.env["SLEEP_LOG"] = self.sleep_log
        self.env.pop("RADAR_START_TRIES", None)
        self.env.pop("RADAR_START_SLEEP", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_stub(self, name, text):
        path = os.path.join(self.stub, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(path, 0o755)

    def _enqueue(self, responses):
        lines = []
        for index, row in enumerate(responses):
            code, body, status = row
            body_path = ""
            if body is not None:
                body_path = os.path.join(self.tmp, "body-%d" % index)
                with open(body_path, "wb") as handle:
                    handle.write(body)
            lines.append("%s\t%s\t%s" % (code, body_path, status))
        with open(self.queue, "w", encoding="utf-8") as handle:
            if lines:
                handle.write("\n".join(lines) + "\n")

    def _calls(self):
        with open(self.calls_log, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def _sleeps(self):
        with open(self.sleep_log, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def invoke(self, responses, leg=LEG, slug="filip_cicvarek~meetup-scraper", extra=None):
        self._enqueue(responses)
        for path in (self.calls_log, self.sleep_log):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("")
        infile = os.path.join(self.tmp, "in.json")
        outfile = os.path.join(self.tmp, "out.json")
        with open(infile, "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        script = (
            "set -euo pipefail; "
            ". %s; "
            "APIFY_TOKEN=test-not-a-token; "
            "API=https://api.invalid/v2; "
            "start_leg %s %s %s %s; "
            'echo "RID=$START_ID"; '
            "echo AFTER"
        ) % (
            shlex.quote(os.path.join(HERE, "start_leg.sh")),
            shlex.quote(leg),
            shlex.quote(slug),
            shlex.quote(infile),
            shlex.quote(outfile),
        )
        env = dict(self.env)
        if extra:
            env.update(extra)
        proc = subprocess.run(
            [BASH, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc, infile

    def assert_outcome(self, proc, rid, line=None):
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertNotIn("test-not-a-token", proc.stdout)
        self.assertNotIn("test-not-a-token", proc.stderr)
        lines = proc.stdout.splitlines()
        if line is None:
            self.assertEqual(lines, ["RID=%s" % rid, "AFTER"])
            return
        self.assertEqual(len(lines), 3, proc.stdout)
        self.assertRegex(lines[0], "^" + TS + " " + re.escape(line) + "$")
        self.assertEqual(lines[1], "RID=%s" % rid)
        self.assertEqual(lines[2], "AFTER")

    def test_http_201_on_the_first_try_is_silent(self):
        """source: a 201 with data.id on try 1 prints no NOTE and no WARN."""
        proc, _infile = self.invoke([("201", _run_id("RUN1"), 0)])
        self.assert_outcome(proc, "RUN1")
        self.assertNotIn("NOTE:", proc.stdout)
        self.assertNotIn("WARN:", proc.stdout)
        self.assertEqual(len(self._calls()), 1)
        self.assertEqual(self._sleeps(), [])

    def test_http_502_then_201_notes_the_second_try(self):
        """source: a 502 then a 201 starts on try 2 after http 502 and sleeps once."""
        proc, _infile = self.invoke([
            ("502", HTML, 0),
            ("201", _run_id("RUN2"), 0),
        ])
        self.assert_outcome(
            proc,
            "RUN2",
            "NOTE: %s started on try 2 after http 502" % LEG,
        )
        self.assertEqual(len(self._calls()), 2)
        self.assertEqual(self._sleeps(), ["10"])

    def test_http_502_three_times_warns_and_shell_continues(self):
        """source: three 502s warn with http 502 and tries 3, and set -e still reaches AFTER."""
        proc, _infile = self.invoke([
            ("502", HTML, 0),
            ("502", HTML, 0),
            ("502", HTML, 0),
        ])
        self.assert_outcome(
            proc,
            "",
            "WARN: %s failed to start (http 502, tries 3)" % LEG,
        )
        self.assertIn("failed to start", proc.stdout)
        self.assertIn("AFTER", proc.stdout)
        self.assertEqual(len(self._calls()), 3)
        self.assertEqual(self._sleeps(), ["10", "10"])

    def test_http_400_is_not_retried(self):
        """source: a 400 stops at once and the WARN names http 400 and tries 1."""
        proc, _infile = self.invoke([("400", ERR, 0)])
        self.assert_outcome(
            proc,
            "",
            "WARN: %s failed to start (http 400, tries 1)" % LEG,
        )
        self.assertEqual(len(self._calls()), 1)
        self.assertEqual(self._sleeps(), [])

    def test_timeout_then_201_retries_after_http_000(self):
        """source: a transport timeout (http 000, curl exit 28) is retried."""
        proc, _infile = self.invoke([
            ("000", None, 28),
            ("201", _run_id("RUN5"), 0),
        ])
        self.assert_outcome(
            proc,
            "RUN5",
            "NOTE: %s started on try 2 after http 000" % LEG,
        )
        self.assertEqual(len(self._calls()), 2)
        self.assertEqual(self._sleeps(), ["10"])

    def test_stale_start_file_from_an_earlier_run_is_not_reused(self):
        """source: start-<leg>.json survives a same-day rerun, and a try that gets no answer
        must not read the earlier run's id from it."""
        with open(os.path.join(self.tmp, "out.json"), "wb") as handle:
            handle.write(_run_id("OLD"))
        proc, _infile = self.invoke([("000", None, 28)] * 3)
        self.assert_outcome(
            proc,
            "",
            "WARN: %s failed to start (http 000, tries 3)" % LEG,
        )
        self.assertEqual(len(self._calls()), 3)

    def test_http_429_is_retried(self):
        """source: 429 is transient and gets one more try."""
        proc, _infile = self.invoke([
            ("429", ERR, 0),
            ("201", _run_id("RUN6"), 0),
        ])
        self.assert_outcome(
            proc,
            "RUN6",
            "NOTE: %s started on try 2 after http 429" % LEG,
        )
        self.assertEqual(len(self._calls()), 2)
        self.assertEqual(self._sleeps(), ["10"])

    def test_http_201_without_data_id_warns(self):
        """source: a 201 body with no data.id is a failed start and is not retried."""
        proc, _infile = self.invoke([("201", b'{"data":{}}', 0)])
        self.assert_outcome(
            proc,
            "",
            "WARN: %s failed to start (http 201, tries 1)" % LEG,
        )
        self.assertEqual(len(self._calls()), 1)
        self.assertEqual(self._sleeps(), [])

    def test_one_try_budget_does_not_sleep_on_502(self):
        """source: RADAR_START_TRIES=1 does not retry a 502 and does not sleep."""
        proc, _infile = self.invoke(
            [("502", HTML, 0)],
            extra={"RADAR_START_TRIES": "1"},
        )
        self.assert_outcome(
            proc,
            "",
            "WARN: %s failed to start (http 502, tries 1)" % LEG,
        )
        self.assertEqual(len(self._calls()), 1)
        self.assertEqual(self._sleeps(), [])

    def test_sleep_pause_comes_from_the_environment(self):
        """source: RADAR_START_SLEEP=3 is the only pause between a 503 and the next try."""
        proc, _infile = self.invoke(
            [("503", HTML, 0), ("201", _run_id("RUN9"), 0)],
            extra={"RADAR_START_SLEEP": "3"},
        )
        self.assert_outcome(
            proc,
            "RUN9",
            "NOTE: %s started on try 2 after http 503" % LEG,
        )
        self.assertEqual(len(self._calls()), 2)
        self.assertEqual(self._sleeps(), ["3"])

    def test_post_target_and_auth_stays_out_of_stdout(self):
        """source: the POST hits API/acts/<slug>/runs with -d @input, and AUTH is not printed."""
        slug = "filip_cicvarek~meetup-scraper"
        proc, infile = self.invoke([("201", _run_id("RUN1"), 0)], slug=slug)
        self.assert_outcome(proc, "RUN1")
        self.assertEqual(len(self._calls()), 1)
        self.assertEqual(self._sleeps(), [])
        posted = self._calls()[0]
        self.assertIn("https://api.invalid/v2/acts/%s/runs" % slug, posted)
        self.assertIn("-d @%s" % infile, posted)
        self.assertNotIn("Authorization", proc.stdout)
        self.assertNotIn("test-not-a-token", proc.stdout)

    def test_token_travels_on_standard_input_never_in_an_argument(self):
        """source: the bearer header sat in curl's argument list for ps."""
        proc, _infile = self.invoke([("502", HTML, 0), ("201", _run_id("RUN2"), 0)])
        self.assert_outcome(proc, "RUN2", "NOTE: %s started on try 2 after http 502" % LEG)
        calls = self._calls()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn("-H @-", call)
            self.assertNotIn("test-not-a-token", call)
            self.assertNotIn("Bearer", call)
        with open(self.curl_stdin, encoding="utf-8") as handle:
            sent = handle.read()
        self.assertEqual(sent, "Authorization: Bearer test-not-a-token\n" * 2)

    def _tries_for(self, value):
        proc, _infile = self.invoke([("502", HTML, 0)] * 12, extra={"RADAR_START_TRIES": value})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return len(self._calls()), self._sleeps()

    def test_a_bad_tries_setting_falls_back_to_three(self):
        """source: RADAR_START_TRIES=0 or text skipped the loop and lost the leg."""
        for value in ("0", "abc", "", "-2", "1.5", "1234567890"):
            with self.subTest(value=value):
                calls, sleeps = self._tries_for(value)
                self.assertEqual(calls, 3)
                self.assertEqual(sleeps, ["10", "10"])

    def test_a_tries_setting_with_a_leading_zero_is_decimal(self):
        """source: 08 is not a valid octal number and stopped the script."""
        calls, _sleeps = self._tries_for("08")
        self.assertEqual(calls, 8)

    def test_a_good_tries_setting_is_used(self):
        # source: the fallback must not override a valid setting
        calls, _sleeps = self._tries_for("5")
        self.assertEqual(calls, 5)

    def test_a_bad_pause_setting_falls_back_to_ten(self):
        """source: a text pause made sleep fail and the retry spin."""
        for value in ("abc", "", "-1", "2.5", "1234567890"):
            with self.subTest(value=value):
                proc, _infile = self.invoke(
                    [("502", HTML, 0), ("201", _run_id("RUN3"), 0)],
                    extra={"RADAR_START_SLEEP": value})
                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                self.assertEqual(self._sleeps(), ["10"])

    def test_a_pause_with_a_leading_zero_is_decimal(self):
        # source: 08 is not valid octal in shell arithmetic
        proc, _infile = self.invoke(
            [("502", HTML, 0), ("201", _run_id("RUN4"), 0)],
            extra={"RADAR_START_SLEEP": "08"})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self._sleeps(), ["8"])

    def test_garbage_code_counts_as_000_and_retries(self):
        """source: a non-numeric curl status is treated as http 000 and retried."""
        proc, _infile = self.invoke([
            ("xyz", None, 0),
            ("201", _run_id("RUN11"), 0),
        ])
        self.assert_outcome(
            proc,
            "RUN11",
            "NOTE: %s started on try 2 after http 000" % LEG,
        )
        self.assertEqual(len(self._calls()), 2)
        self.assertEqual(self._sleeps(), ["10"])

    def test_http_402_is_not_retried(self):
        """source: a 402 (not enough usage on the account) stops at once and names http 402."""
        proc, _infile = self.invoke([("402", ERR, 0)])
        self.assert_outcome(
            proc,
            "",
            "WARN: %s failed to start (http 402, tries 1)" % LEG,
        )
        self.assertEqual(len(self._calls()), 1)
        self.assertEqual(self._sleeps(), [])

    def test_radar_sources_start_leg_and_posts_no_run_itself(self):
        """source: radar.sh sources start_leg.sh and does not post runs with its own curl."""
        path = os.path.join(HERE, "radar.sh")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        here = 'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        source = '. "$HERE/start_leg.sh"\n'
        call = (
            'start_leg "$leg" "${LEG_ACTOR[$i]}" '
            '"$WORK/in-$leg.json" "$WORK/start-$leg.json"'
        )
        self.assertIn(here, text)
        self.assertIn(source, text)
        self.assertIn(call, text)
        self.assertIn('LEG_RID[$i]="$START_ID"', text)
        self.assertNotIn('"$API/acts/', text)


if __name__ == "__main__":
    unittest.main()
