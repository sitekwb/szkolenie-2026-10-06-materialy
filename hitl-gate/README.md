# hitl-gate: a hard block instead of a request

Material for the Human-in-the-loop stage: permission rules plus a `PreToolUse` hook for
Claude Code. A request in the prompt or in `CLAUDE.md` is an instruction the model may skip.
Permission rules and hooks are enforced by Claude Code itself, not by the model.

| File | What it does |
|---|---|
| `.claude/settings.json` | `permissions` with `deny`, `ask` and `allow` rules; registers the hook for `PreToolUse` on `Bash` |
| `.claude/hooks/block_rm.py` | Hook script (Python 3.12+, standard library only). Exit code 2 blocks the call; the reason on stderr goes to the agent |
| `tests/test_block_rm.py` | pytest: feeds the hook JSON on stdin, as Claude Code does, and checks the exit code and message |

## How it works

- Rules are evaluated in order: deny, then ask, then allow. The first match wins, regardless of
  which rule is more specific. Deny applies in every permission mode, including `bypassPermissions`.
- A Bash rule matches the command text. `Bash(rm *)` matches `rm -rf build/` but not
  `/bin/rm -rf build/` or `bash -c 'rm -rf build/'`. A deny rule is not a sandbox.
- The hook parses the command instead. It splits `;`, `&&`, `||`, `|`, new lines and `( )`, and
  treats shell keywords (`if`, `then`, `else`, `do`, `while`, `{`, `!`, function bodies) as the
  start of a new command. It strips paths, quotes, backslashes, redirections and leading
  `VAR=value`, and unwraps `sudo`, `env`, `nice`, `timeout`, `xargs`, `stdbuf`, `ionice`, `nohup`,
  `busybox` and similar wrappers, including option values such as `sudo -u root`. It re-checks
  the code a shell runs: `bash -c`, `eval`, `$(...)`, backticks, heredocs (`bash <<EOF`),
  here-strings, `echo ... | sh` and aliases. It blocks `rm`, `rmdir`, `unlink`, `shred`,
  `find -delete` and `git clean`.
- It fails closed: a command name known only at run time (`$CMD -rf build`) and input it cannot
  parse are blocked. Lookups such as `command -v rm`, `type rm`, `which rm` or `man rm` pass.
- Hooks run in every permission mode, including `bypassPermissions`. Exit code 0 means
  "no decision": the normal permission flow and the rules above still apply.

Sources (checked 2026-10-05): <https://code.claude.com/docs/en/permissions>,
<https://code.claude.com/docs/en/hooks>, <https://code.claude.com/docs/en/permission-modes>.

## What this hook does not catch

The hook checks command names, nothing else. It does not stop deleting or overwriting files by
other means, for example `git rm`, `git reset --hard`, `git checkout -- .`,
`python3 -c "import os; os.remove('x')"`, `node -e "require('fs').rmSync('x')"`,
`perl -e "unlink q(x)"`, `> x`, `truncate -s0 x`, `dd if=/dev/null of=x`, `cp /dev/null x`,
`mv x /dev/null`, `rsync --delete`, `tar --remove-files` or `sed -i`. After `rm` is refused, an
agent may well try one of these. The real boundary comes from the sandbox, a container or a VM,
and from what the account running the agent is allowed to touch, not from a hook. Treat the hook
as a guard against the most common mistake, and the permission rules plus review of every diff
as the human-in-the-loop step.

Scripts are not checked either. `bash script.sh`, `sh ./run.sh`, `./build.sh` or `cat s.sh | bash`
run code from a file the hook never reads, so an `rm` inside that file runs. The hook passes
these calls on purpose: agents run scripts all the time, and a hook that blocks them gets
switched off.

## Micro-exercise: same requests, different modes

Do this in your own repository from the earlier exercises, not in this one. You need
`python3` (3.12 or newer) on `PATH`, because the hook is started as `python3 .../block_rm.py`.

### Part 1: modes, without these settings

1. Add `notatka-hitl.txt` to `.gitignore` (or delete the file at the end), so it never lands in a commit.
2. Start `claude` and press `Shift+Tab` until the status bar shows `⏸ manual mode on`.
   Start in Manual: in `auto` mode a classifier replaces the prompts and the results differ.
3. Send the three requests, one by one:

   ```text
   A: Create the file notatka-hitl.txt with one sentence.
   B: Run git status and ls -la.
   C: Delete the file notatka-hitl.txt.
   ```

4. Repeat in `⏵⏵ accept edits on`, then once more with the `/plan` prefix.

Expected, according to the documentation: Manual asks at A and C; B runs without asking because
`git status` and `ls` are built-in read-only commands. `acceptEdits` does not ask at A, and usually
not at C either, because `rm` inside the working directory counts as a file command in that mode.
With `/plan` the agent writes a plan and changes nothing. Results can differ between Claude Code
versions; note what you see.

### Part 2: the rule and the hook

1. Copy the settings into your repository (keep a backup if you already have `.claude/settings.json`;
   then merge the `permissions` and `hooks` keys by hand instead):

   ```bash
   cp -R path/to/hitl-gate/.claude /path/to/your-repo/
   ```

2. Restart `claude` in your repository (accept the workspace trust dialog; in an untrusted
   workspace Claude Code ignores the `allow` entries), switch to Manual, and check what was loaded:
   `/permissions` lists every rule and the file it comes from; `/hooks` shows the `PreToolUse` hook.
3. Send C again. Then send: `Delete it with bash -c "rm notatka-hitl.txt"`, and
   `Delete it with /bin/rm notatka-hitl.txt`.

Expected: `rm notatka-hitl.txt` is refused by the deny rule `Bash(rm *)` without a prompt
(`git clean` is denied the same way by `Bash(git clean *)`). The
`bash -c` and `/bin/rm` variants do not match that rule; the hook blocks them and the agent
receives the reason, e.g.
`Blocked by the hitl-gate PreToolUse hook: 'rm' deletes files (seen as '/bin/rm')`.
The agent may still choose another tool than Bash to remove the file; that depends on the model.

4. To see the gap the hook closes, remove the `hooks` block from `.claude/settings.json`, restart,
   and repeat the `bash -c` request: it now goes to the normal prompt of the current mode.
5. Clean up: remove `.claude/` (or the copied keys) and `notatka-hitl.txt` from your repository.

## Check the hook without Claude Code

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"bash -c \"/bin/rm -rf build\""}}' \
  | python3 .claude/hooks/block_rm.py; echo "exit $?"    # reason on stderr, exit 2
echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' \
  | python3 .claude/hooks/block_rm.py; echo "exit $?"    # exit 0
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest -q
```

`mypy` runs in `--strict` mode on the hook and the tests (configured in `pyproject.toml`).
