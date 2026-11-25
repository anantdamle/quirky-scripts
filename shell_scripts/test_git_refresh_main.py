"""Offline regression tests for git refresh-main."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
GIT = shutil.which("git")


class GitRefreshMainTests(unittest.TestCase):
    """git refresh-main against throwaway repositories."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="git refresh-main tests "
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("git-wt", "git-refresh-main"):
            (self.bin / name).symlink_to(SCRIPTS / name)
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.env.update(
            {
                "PATH": os.pathsep.join([str(self.bin), os.environ["PATH"]]),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "Test User",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "Test User",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
            }
        )
        self.source = self.root / "source"
        self.container = self.root / "worktree container"
        self.main = self.container / "MAIN"

    def git(self, cwd, *args, check=True):
        return subprocess.run(
            [GIT, *args],
            cwd=cwd,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def clone(self):
        self.git(
            self.root, "init", "--initial-branch", "main", str(self.source)
        )
        (self.source / "tracked.txt").write_text("initial\n")
        self.git(self.source, "add", "tracked.txt")
        self.git(self.source, "commit", "-m", "Initial fixture")
        self.git(
            self.root, "wt", "clone", str(self.source), str(self.container)
        )
        (self.source / "remote.txt").write_text("remote update\n")
        self.git(self.source, "add", "remote.txt")
        self.git(self.source, "commit", "-m", "Remote fixture update")

    def assert_updated(self):
        self.assertEqual(
            self.git(self.main, "rev-parse", "HEAD").stdout,
            self.git(self.source, "rev-parse", "HEAD").stdout,
        )

    def test_container_refreshes_main_without_errors(self):
        self.clone()
        result = self.git(self.container, "refresh-main")
        self.assert_updated()
        self.assertNotIn("fatal:", result.stderr)

    def test_container_preserves_dirty_main(self):
        self.clone()
        (self.main / "tracked.txt").write_text("local edit\n")
        (self.main / "untracked.txt").write_text("untracked\n")
        self.git(self.container, "refresh-main")
        self.assert_updated()
        self.assertEqual(
            (self.main / "tracked.txt").read_text(), "local edit\n"
        )
        self.assertTrue((self.main / "untracked.txt").exists())
        self.assertEqual(self.git(self.main, "stash", "list").stdout, "")

    def test_main_checkout_still_works(self):
        self.clone()
        self.git(self.main, "refresh-main")
        self.assert_updated()

    def test_linked_worktree_is_untouched(self):
        self.clone()
        feature = self.container / "feature"
        self.git(self.main, "worktree", "add", "-b", "feature", str(feature))
        (feature / "tracked.txt").write_text("feature edit\n")
        self.git(feature, "refresh-main")
        self.assert_updated()
        self.assertEqual(
            self.git(feature, "branch", "--show-current").stdout, "feature\n"
        )
        self.assertEqual(
            (feature / "tracked.txt").read_text(), "feature edit\n"
        )

    def test_invalid_directory_fails_cleanly(self):
        for create_main in (False, True):
            with self.subTest(main_directory=create_main):
                if create_main:
                    (self.root / "MAIN").mkdir()
                result = self.git(self.root, "refresh-main", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("fatal:", result.stderr)
                self.assertIn(
                    "not a git repository or a worktree container",
                    result.stderr,
                )
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
