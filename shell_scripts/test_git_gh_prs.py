"""Offline regression tests for repository grouping in git gh-prs."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent
GIT = shutil.which("git")


class GitGhPrsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="git gh-prs tests ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("git-wt", "git-gh-prs"):
            (self.bin / name).symlink_to(SCRIPTS / name)
        gh = self.bin / "gh"
        gh.write_text('''#!/bin/sh
case "$1 $2" in
    "auth status") exit 0 ;;
    "api user") printf '%s\\n' viewer ;;
    "repo view")
        git rev-parse --git-dir >/dev/null 2>&1 || exit 1
        printf '%s\\n' owner/current
        ;;
    "api graphql")
        printf '%s\\n' "$@" > "$TEST_GH_ARGS"
        printf 'owner/other\\t#2\\tother\\tready\\tclean\\t1m ago\\tOther PR\\n'
        printf 'owner/current\\t#1\\tfeature\\tready\\tclean\\t2m ago\\tCurrent PR\\n'
        ;;
    *) exit 1 ;;
esac
''')
        gh.chmod(0o755)
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.args_log = self.root / "gh-args"
        self.env.update({
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "TEST_GH_ARGS": str(self.args_log),
        })

    def git(self, cwd, *args):
        return subprocess.run(
            [GIT, *args], cwd=cwd, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )

    def clone(self):
        source = self.root / "source"
        self.git(self.root, "init", "--initial-branch", "main", str(source))
        self.git(source, "commit", "--allow-empty", "-m", "Initial fixture")
        self.container = self.root / "worktree container"
        self.main = self.container / "MAIN"
        self.git(self.root, "wt", "clone", str(source), str(self.container))

    def assert_current_first(self, cwd, *args):
        result = self.git(cwd, "gh-prs", *args)
        self.assertTrue(result.stdout.startswith("owner/current"), result.stdout)
        self.assertLess(result.stdout.index("Current PR"), result.stdout.index("Other PR"))
        self.assertIn("Other repositories", result.stdout)
        self.assertIn("2 open pull request(s) total.", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_container_groups_main_repository_first(self):
        self.clone()
        self.assert_current_first(self.container)

    def test_main_checkout_still_works(self):
        self.clone()
        self.assert_current_first(self.main)

    def test_linked_worktree_still_works(self):
        self.clone()
        feature = self.container / "feature"
        self.git(self.main, "worktree", "add", "-b", "feature", str(feature))
        self.assert_current_first(feature)

    def test_outside_repository_retains_all_repository_listing(self):
        for create_main in (False, True):
            with self.subTest(main_directory=create_main):
                if create_main:
                    (self.root / "MAIN").mkdir()
                result = self.git(self.root, "gh-prs")
                self.assertTrue(result.stdout.startswith("All repositories"), result.stdout)
                self.assertLess(result.stdout.index("Other PR"), result.stdout.index("Current PR"))
                self.assertEqual(result.stderr, "")

    def test_container_preserves_search_arguments(self):
        self.clone()
        for args, qualifier in (
            ((), "author:viewer"),
            (("review-requested",), "review-requested:viewer"),
            (("another-user",), "author:another-user"),
        ):
            with self.subTest(args=args):
                self.assert_current_first(self.container, *args)
                self.assertIn(
                    f"q=is:pr is:open {qualifier} sort:updated-desc",
                    self.args_log.read_text(),
                )


if __name__ == "__main__":
    unittest.main()
