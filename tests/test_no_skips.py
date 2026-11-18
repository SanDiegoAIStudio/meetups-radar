"""A test that cannot run must fail, never skip.

This file scans the test folder for skip decorators and calls, and checks that the tools the
shell tests need are present, so their absence fails the suite with a clear message.
Run: python3 -m unittest discover -s tests
"""
from __future__ import annotations

import os
import re
import shutil
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
# Built from pieces so this file does not contain the words it searches for.
SKIP_WORDS = re.compile(r"\bskip" + r"(?:Unless|If)\b|\bunittest\.skip\b|@skip\b|\.skip" + r"Test\(|\braise SkipTest\b")


class NoSkips(unittest.TestCase):
    def test_no_test_file_skips(self):
        # source: StartLegTests skipped itself without bash or jq
        found = []
        for name in sorted(os.listdir(TESTS)):
            if not name.endswith(".py") or name == os.path.basename(__file__):
                continue
            with open(os.path.join(TESTS, name), encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    if SKIP_WORDS.search(line):
                        found.append("%s:%d: %s" % (name, number, line.strip()))
        self.assertEqual(found, [], "skip decorators or calls found")

    def test_the_tools_the_shell_tests_need_are_present(self):
        # source: a missing tool must fail the suite with its name
        missing = [tool for tool in ("bash", "curl", "jq", "perl") if shutil.which(tool) is None]
        self.assertEqual(missing, [], "required tools missing from PATH: %s" % ", ".join(missing))


if __name__ == "__main__":
    unittest.main()
