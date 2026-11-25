# git-wt — branches as worktrees

`git wt` keeps every branch in its own folder using
[git worktrees](https://git-scm.com/docs/git-worktree), so switching branches is
just a `cd` — no stashing, no rebuilding, and you can have several branches open
at once.

## Install

Put `shell_scripts/` on your `PATH`; git picks up `git-wt` as `git wt`.

```sh
export PATH="$HOME/code/anantdamle/quirky-scripts/shell_scripts:$PATH"
```

Then add the shell hook to `~/.zshrc` / `~/.bashrc` so `switch` and `new` can
`cd` your current shell (a git subcommand can't change its caller's directory on
its own):

```sh
eval "$(git wt init)"
```

The hook wraps `git` only for `git wt switch|new`; everything else passes
straight through to the real `git`.

## Layout

Recommended (via `git wt clone`):

```text
myproj/
  MAIN/        # default branch (the main clone)
  feat-login/  # git wt new feat/login
  fix-123/     # git wt new fix/123
```

For repos cloned normally, worktrees go in a sibling folder `<repo>.worktrees/`.
Slashes in branch names become dashes in folder names. Override the location
with `git config wt.root /path/to/dir`.

## Commands

| Command                                      | Description                                                                                                                                                                                                                                               |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `git wt clone [clone opts] <url> [<name>]`   | Clone into `<name>/MAIN` and set `wt.root` to `<name>`. All `git clone` options pass through (`--depth`, `-b`, `-o`, …).                                                                                                                                  |
| `git wt new [--no-switch] <branch> [<base>]` | Create a worktree, then `cd` into it (`--no-switch` to stay put). Uses the local branch if it exists, else tracks `<remote>/<branch>` if it exists, else creates a new branch from `<base>` (default: remote's default branch). Aliases: `add`, `create`. |
| `git wt rm [-f\|-D] <branch>`                | Remove the worktree folder and delete the local branch. Refuses dirty worktrees / unmerged branches unless `-f`; `-D` forces only the branch delete (dirty worktrees are still kept). Aliases: `remove`, `delete`, `del`.                                 |
| `git wt switch <branch>`                     | `cd` into the branch's folder. Without the hook, prints the path instead. Aliases: `sw`, `cd`.                                                                                                                                                            |
| `git wt path <branch>`                       | Print the branch's folder.                                                                                                                                                                                                                                |
| `git wt ls`                                  | List worktrees as `BRANCH  COMMIT  FOLDER`, folders relative to the repo root (`myproj/`).                                                                                                                                                                |
| `git wt init`                                | Print the shell hook (see Install).                                                                                                                                                                                                                       |
| `git wt help`                                | Show usage. (`git wt --help` is intercepted by git and looks for a man page.)                                                                                                                                                                             |

## Example

```sh
git wt clone git@github.com:me/myproj.git
cd myproj

git wt new feat/login          # new branch off origin/main, cd's into it
git wt new teammate/fix        # local branch tracking origin/teammate/fix
git wt switch feat/login       # cd ../feat-login

git wt rm feat/login           # after merge; -f to force
```

## Notes

- Remote is picked in order: `wt.remote` (recorded by `git wt clone`, so
  `clone -o upstream` just works), `origin`, the current branch's tracking
  remote, then the first configured remote. Set it explicitly with
  `git config wt.remote <name>`.
- `rm` won't remove the main worktree or the one you're currently inside.
- Running `new` for a branch that already has a worktree just switches to it.
- All commands also work from the container folder (`myproj/`): if the current
  folder isn't a git repo but contains a `MAIN` checkout, `git wt` uses it.

## Other scripts

`git refresh-main` and `git gh-sync` detect when `main` is checked out in
another worktree and use `git wt` instead of `git checkout`: they move into
main's folder (`git wt path main`) to pull, and `gh-sync` deletes merged/closed
branches with `git wt rm -D` so their folders go too. Your current worktree is
never touched. In a regular single-checkout repo they behave as before.

You can also run `git gh-sync`, `git refresh-main`, `git gh-prs`, and `git olog`
directly from the container folder (`myproj/`); they enter `MAIN` before reading
repository metadata. Inside a checkout or linked worktree, they retain that
repository context. `git olog` compares the current branch with `main`.

This fallback applies to these custom subcommands and `git wt`, not built-in Git
commands. From the container, use `git -C MAIN status` (or enter a checkout) for
ordinary Git operations.

## Choosing a remote

All custom subcommands (`git wt`, `git gh-sync`, `git gh-prs`,
`git refresh-main`, `git olog`) accept `-r|--remote <name>`. Default:
`wt.remote`, then `origin`. `git gh-sync` passes `-R <owner>/<repo>` from that
remote to `gh` (no `gh repo set-default` needed) and looks up branches tracking
a fork as `<fork-owner>:<branch>`.
