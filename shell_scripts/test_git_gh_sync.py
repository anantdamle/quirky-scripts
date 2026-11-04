"""Offline regression tests: python3 -m unittest discover -s shell_scripts -p 'test_*.py'."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent
GIT = shutil.which("git")


class GitGhSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="git gh-sync tests ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("git-wt", "git-gh-sync"):
            (self.bin / name).symlink_to(SCRIPTS / name)
        gh = self.bin / "gh"
        gh.write_text('#!/bin/sh\nprintf "%s\\n" "${TEST_PR_STATE:-OPEN}"\n')
        gh.chmod(0o755)
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.env.update({
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        })
        self.source = self.root / "source"
        self.container = self.root / "worktree container"
        self.main = self.container / "MAIN"

    def git(self, cwd, *args, check=True):
        return subprocess.run(
            [GIT, *args], cwd=cwd, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
        )

    def clone(self, branch="main"):
        self.git(self.root, "init", "--initial-branch", branch, str(self.source))
        (self.source / "tracked.txt").write_text("initial\n")
        self.git(self.source, "add", "tracked.txt")
        self.git(self.source, "commit", "-m", "Initial fixture")
        self.git(self.root, "wt", "clone", str(self.source), str(self.container))
        (self.source / "remote.txt").write_text("remote update\n")
        self.git(self.source, "add", "remote.txt")
        self.git(self.source, "commit", "-m", "Remote fixture update")

    def assert_updated(self):
        self.assertEqual(
            self.git(self.main, "rev-parse", "HEAD").stdout,
            self.git(self.source, "rev-parse", "HEAD").stdout,
        )

    def test_container_syncs_remote_default_branch(self):
        self.clone(branch="trunk")
        result = self.git(self.container, "gh-sync")
        self.assert_updated()
        self.assertNotIn("fatal:", result.stderr)
        self.assertIn("Updating 'trunk'", result.stdout)

    def test_container_preserves_dirty_main(self):
        self.clone()
        (self.main / "tracked.txt").write_text("local edit\n")
        self.git(self.container, "gh-sync")
        self.assert_updated()
        self.assertEqual((self.main / "tracked.txt").read_text(), "local edit\n")
        self.assertEqual(self.git(self.main, "stash", "list").stdout, "")

    def test_main_checkout_still_works(self):
        self.clone()
        self.git(self.main, "gh-sync")
        self.assert_updated()

    def test_linked_worktree_stays_on_original_branch(self):
        self.clone()
        feature = self.container / "feature"
        self.git(self.main, "worktree", "add", "-b", "feature", str(feature))
        (feature / "tracked.txt").write_text("feature edit\n")
        self.env["TEST_PR_STATE"] = "MERGED"
        self.git(feature, "gh-sync")
        self.assert_updated()
        self.assertEqual(self.git(feature, "branch", "--show-current").stdout, "feature\n")
        self.assertEqual((feature / "tracked.txt").read_text(), "feature edit\n")

    def test_container_removes_closed_pr_worktree(self):
        self.clone()
        feature = self.container / "feature"
        self.git(self.main, "worktree", "add", "-b", "feature", str(feature))
        self.env["TEST_PR_STATE"] = "CLOSED"
        self.git(self.container, "gh-sync")
        self.assert_updated()
        self.assertFalse(feature.exists())
        self.assertNotEqual(
            self.git(self.main, "show-ref", "--verify", "--quiet", "refs/heads/feature", check=False).returncode,
            0,
        )

    def test_inaccessible_gh_repo_fails_before_sync(self):
        self.clone()
        (self.bin / "gh").write_text(
            '#!/bin/sh\necho "Could not resolve to a Repository" >&2\nexit 1\n'
        )
        result = self.git(self.container, "gh-sync", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gh cannot access this repository", result.stderr)
        self.assertIn("Could not resolve to a Repository", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_pr_lookup_errors_are_logged(self):
        self.clone()
        self.git(self.main, "branch", "feature")
        for message, logged in (("HTTP 502 Bad Gateway", True),
                                ('no pull requests found for branch "feature"', False)):
            with self.subTest(message=message):
                (self.bin / "gh").write_text(
                    '#!/bin/sh\n[ "$1" = repo ] && exit 0\n'
                    f"echo '{message}' >&2\nexit 1\n"
                )
                result = self.git(self.container, "gh-sync")
                self.assertEqual("could not check PR for 'feature'" in result.stderr, logged)
                self.assertEqual(self.git(self.main, "branch", "--list", "feature").stdout.strip(), "feature")

    def fork_layout(self):
        """origin=.../Upstream/repo.git, fork=.../me/repo; 'feature' tracks the fork."""
        self.clone()
        upstream = self.root / "Upstream" / "repo.git"
        self.git(self.root, "clone", "--bare", str(self.source), str(upstream))
        self.git(self.main, "remote", "set-url", "origin", str(upstream))
        self.git(self.root, "clone", "--bare", str(self.source), str(self.root / "me" / "repo"))
        self.git(self.main, "remote", "add", "fork", str(self.root / "me" / "repo"))
        self.git(self.main, "branch", "feature")
        self.git(self.main, "config", "branch.feature.remote", "fork")
        self.git(self.main, "config", "branch.feature.merge", "refs/heads/feature")
        log = self.root / "gh.log"
        (self.bin / "gh").write_text(
            f'#!/bin/sh\necho "$*" >> "{log}"\n[ "$1" = repo ] && exit 0\n'
            '[ "$3" = "me:feature" ] && [ "$5" = Upstream/repo ] && echo MERGED || echo OPEN\n'
        )
        return log

    def test_fork_branch_matched_as_owner_branch_on_upstream(self):
        log = self.fork_layout()
        result = self.git(self.container, "gh-sync")
        self.assertIn("repo view Upstream/repo", log.read_text())
        self.assertIn("pr view me:feature -R Upstream/repo", log.read_text())
        self.assertIn("Deleting local branch connected to MERGED PR:feature", result.stdout)

    def test_remote_option_selects_repo(self):
        log = self.fork_layout()
        result = self.git(self.container, "gh-sync", "--remote", "fork", check=False)
        self.assertIn("Using remote 'fork' (me/repo)", result.stdout)
        self.assertIn("pr view feature -R me/repo", log.read_text())
        bad = self.git(self.container, "gh-sync", "-r", "nope", check=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("no such remote 'nope'", bad.stderr)

    def test_invalid_directory_fails_before_sync(self):
        for create_main in (False, True):
            with self.subTest(main_directory=create_main):
                if create_main:
                    (self.root / "MAIN").mkdir()
                result = self.git(self.root, "gh-sync", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("not a git repository or a worktree container", result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
