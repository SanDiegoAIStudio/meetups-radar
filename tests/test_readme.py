"""The two blocks pasted under "What you get" in README.md must equal real output.

The sorting step runs on fixtures/ with the date 2026-11-02 into a temporary folder. The
first fenced block must equal the first lines of the LATEST.md it writes, and the second
fenced block must equal the first line it prints.
Run: python3 -m unittest discover -s tests
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "fixtures")
SCRIPT = os.path.join(ROOT, "normalize_events.py")
README = os.path.join(ROOT, "README.md")
TODAY = "2026-11-02"


def what_you_get_blocks():
    with open(README, encoding="utf-8") as fh:
        text = fh.read()
    start = text.index("\n## What you get\n")
    # The first block holds markdown headings of its own, so the section ends at the next
    # heading of the README itself.
    end = text.index("\n## Quick start without an account\n", start)
    return re.findall(r"```\n(.*?)\n```", text[start:end], re.S)


class ReadmeBlocks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="radar-readme-")
        env = dict(os.environ, RADAR_OUT=cls.out, PYTHONDONTWRITEBYTECODE="1")
        cls.proc = subprocess.run([sys.executable, SCRIPT, FIXTURES, TODAY], env=env,
                                  capture_output=True, text=True, timeout=120)
        cls.blocks = what_you_get_blocks()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_the_run_succeeded(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-400:])

    def test_there_are_two_blocks_under_what_you_get(self):
        self.assertEqual(len(self.blocks), 2)

    def test_first_block_is_the_top_of_latest_md(self):
        with open(os.path.join(self.out, "LATEST.md"), encoding="utf-8") as fh:
            written = fh.read().split("\n")
        shown = self.blocks[0].split("\n")
        self.assertEqual(shown, written[:len(shown)])

    def test_second_block_is_the_first_line_the_step_printed(self):
        printed = self.proc.stdout.split("\n")[0]
        self.assertEqual(self.blocks[1], printed)


if __name__ == "__main__":
    unittest.main()
