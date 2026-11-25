"""Offline regression tests for git olog in worktree layouts."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
GIT = shutil.which("git")


class GitOlogTests(unittest.TestCase):
    """git olog in plain and worktree-container layouts."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="git olog tests ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("git-wt", "git-olog"):
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
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        self.source = self.root / "source"
        self.container = self.root / "worktree container"
        self.main = self.container / "MAIN"
        self.git(
            self.root, "init", "--initial-branch", "main", str(self.source)
        )
        self.git(
            self.source, "commit", "--allow-empty", "-m", "Initial fixture"
        )
        self.git(
            self.root, "wt", "clone", str(self.source), str(self.container)
        )

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

    def test_container_displays_main_history(self):
        result = self.git(self.container, "olog")
        self.assertEqual(
            result.stdout,
            self.git(
                self.main, "log", "--oneline", "HEAD", "main", "-5"
            ).stdout,
        )
        self.assertIn("Initial fixture", result.stdout)
        self.assertNotIn("fatal:", result.stderr)

    def test_checkout_and_subdirectory_display_history(self):
        subdir = self.main / "nested"
        subdir.mkdir()
        for cwd in (self.main, subdir, self.source):
            with self.subTest(cwd=cwd):
                self.assertIn("Initial fixture", self.git(cwd, "olog").stdout)

    def test_arguments_are_preserved(self):
        self.git(self.main, "commit", "--allow-empty", "-m", "Second fixture")
        result = self.git(self.container, "olog", "-1", "--format=%s")
        self.assertEqual(result.stdout, "Second fixture\n")

    def test_linked_worktree_preserves_branch_and_changes(self):
        feature = self.container / "feature"
        self.git(self.main, "worktree", "add", "-b", "feature", str(feature))
        self.git(feature, "commit", "--allow-empty", "-m", "Feature fixture")
        (feature / "untracked.txt").write_text("local changes\n")
        result = self.git(feature, "olog")
        self.assertEqual(
            result.stdout,
            self.git(feature, "log", "--oneline", "HEAD", "main", "-5").stdout,
        )
        self.assertEqual(
            self.git(feature, "branch", "--show-current").stdout, "feature\n"
        )
        self.assertEqual(
            (feature / "untracked.txt").read_text(), "local changes\n"
        )

    def test_diverged_worktree_uses_graph(self):
        feature = self.container / "feature"
        self.git(self.main, "worktree", "add", "-b", "feature", str(feature))
        self.git(feature, "commit", "--allow-empty", "-m", "Feature fixture")
        self.git(self.main, "commit", "--allow-empty", "-m", "Main fixture")
        result = self.git(feature, "olog")
        self.assertEqual(
            result.stdout,
            self.git(
                feature,
                "log",
                "--oneline",
                "--graph",
                "--boundary",
                "HEAD...main",
                "-5",
            ).stdout,
        )
        self.assertIn("Feature fixture", result.stdout)
        self.assertIn("Main fixture", result.stdout)

    def test_invalid_directory_fails_cleanly(self):
        for create_main in (False, True):
            with self.subTest(main_directory=create_main):
                if create_main:
                    (self.root / "MAIN").mkdir()
                result = self.git(self.root, "olog", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "git-olog: not a git repository or a worktree container",
                    result.stderr,
                )
                self.assertNotIn("fatal:", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_wt_read_commands_work_from_container(self):
        self.assertIn("main", self.git(self.container, "wt", "ls").stdout)
        self.assertEqual(
            self.git(self.container, "wt", "path", "main").stdout.strip(),
            str(self.main.resolve()),
        )
        self.assertEqual(
            self.git(self.container, "wt", "switch", "main").stdout.strip(),
            str(self.main.resolve()),
        )


if __name__ == "__main__":
    unittest.main()
