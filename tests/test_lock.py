"""Tests for lock.sh, the single-run lock that radar.sh sources.

Each test runs a short bash snippet that sources lock.sh. Ages are set with os.utime, so
nothing sleeps and nothing depends on timing.
Run: python3 -m unittest discover -s tests
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK_SH = os.path.join(HERE, "lock.sh")
BASH = "/bin/bash"


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lock-test-")
        self.lock = os.path.join(self.tmp, "radar.lock.d")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sh(self, body):
        """Run a snippet with lock.sh sourced and DIR set; return the finished process."""
        script = "set -euo pipefail; . %s; DIR=%s; %s" % (
            shlex.quote(LOCK_SH), shlex.quote(self.lock), body)
        return subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=30)

    def age(self, minutes):
        old = time.time() - minutes * 60
        os.utime(self.lock, (old, old))

    def pid_file(self):
        with open(os.path.join(self.lock, "pid"), encoding="utf-8") as fh:
            return fh.read().strip()

    def mtime_age_minutes(self):
        return (time.time() - os.stat(self.lock).st_mtime) / 60.0

    def test_take_writes_the_pid_of_the_shell(self):
        """source: the lock names its owner so refresh and release can check it."""
        proc = self.sh('rc=0; lock_take "$DIR" || rc=$?; echo "rc=$rc pid=$$"')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        shell_pid = proc.stdout.split("pid=")[1].strip()
        self.assertIn("rc=0", proc.stdout)
        self.assertEqual(self.pid_file(), shell_pid)

    def test_a_second_take_on_a_fresh_lock_returns_1(self):
        # source: two runs must not work on the same folders
        self.assertEqual(self.sh('lock_take "$DIR"').returncode, 0)
        proc = self.sh('rc=0; lock_take "$DIR" || rc=$?; echo "rc=$rc"')
        self.assertEqual(proc.stdout.strip(), "rc=1", proc.stderr)
        self.assertTrue(os.path.isdir(self.lock))

    def test_a_lock_50_minutes_old_is_reclaimed(self):
        # source: a crashed run must not block every later run
        self.assertEqual(self.sh('lock_take "$DIR"').returncode, 0)
        self.age(50)
        proc = self.sh('rc=0; lock_take "$DIR" || rc=$?; echo "rc=$rc pid=$$"')
        self.assertIn("rc=0", proc.stdout)
        self.assertIn("reclaiming stale radar lock", proc.stdout)
        self.assertEqual(self.pid_file(), proc.stdout.split("pid=")[1].strip())
        self.assertLess(self.mtime_age_minutes(), 5)

    def test_a_lock_40_minutes_old_is_still_held(self):
        # source: the limit is 45 minutes and a young lock must stay
        self.assertEqual(self.sh('lock_take "$DIR"').returncode, 0)
        self.age(40)
        proc = self.sh('rc=0; lock_take "$DIR" || rc=$?; echo "rc=$rc"')
        self.assertEqual(proc.stdout.strip(), "rc=1")

    def test_a_stale_lock_holding_a_stray_file_returns_2(self):
        # source: a lock that cannot be removed must stop the run, not loop
        os.mkdir(self.lock)
        with open(os.path.join(self.lock, "stray.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        self.age(50)
        proc = self.sh('rc=0; lock_take "$DIR" || rc=$?; echo "rc=$rc"')
        self.assertTrue(proc.stdout.strip().endswith("rc=2"), proc.stdout + proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.lock, "stray.txt")))

    def test_release_with_another_id_leaves_the_lock(self):
        # source: a run must never remove the lock of a run that owns it
        os.mkdir(self.lock)
        with open(os.path.join(self.lock, "pid"), "w", encoding="utf-8") as fh:
            fh.write("1\n")
        proc = self.sh('lock_release "$DIR"; echo after')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isdir(self.lock))
        self.assertEqual(self.pid_file(), "1")

    def test_release_with_its_own_id_removes_the_lock(self):
        # source: a finished run must free the lock at once
        proc = self.sh('lock_take "$DIR"; lock_release "$DIR"; echo after')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.exists(self.lock))

    def test_refresh_moves_a_50_minute_old_time_to_now(self):
        # source: a slow run lost its lock after 45 minutes
        old = "$(perl -e 'print time - 3000')"
        proc = self.sh('lock_take "$DIR"; perl -e \'utime $ARGV[0], $ARGV[0], $ARGV[1]\' '
                       '"%s" "$DIR"; lock_refresh "$DIR"; echo ok' % old)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLess(self.mtime_age_minutes(), 5)

    def test_refresh_with_another_id_leaves_the_time_alone(self):
        # source: only the owner may keep a lock alive
        os.mkdir(self.lock)
        with open(os.path.join(self.lock, "pid"), "w", encoding="utf-8") as fh:
            fh.write("1\n")
        self.age(50)
        proc = self.sh('lock_refresh "$DIR"; echo ok')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertGreater(self.mtime_age_minutes(), 45)


if __name__ == "__main__":
    unittest.main()
