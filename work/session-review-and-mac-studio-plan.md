# simple-workflow: Session Review and Mac Studio Deployment Plan

Date: 2026-06-17

---

## Part 1: Project Review

### What Went Right

**Architecture is clean and well-factored.** The engine is split into six focused modules (source, runtime, storage, workspace, destination, gates) plus a thin orchestrator. Each file does one thing, imports are obvious, and the whole engine folder is readable in under an hour. This matches the PRD's "simple, minimal code" tenet.

**The PRD is excellent.** It is one of the best PRDs I have read for a project this size. The vision is clear, the data flow diagrams are accurate, the SQLite schema is well thought out, and the non-goals are explicit. The "why not interfaces" section saves future contributors hours of premature abstraction.

**Prompts are strong.** All six prompts (triage, plan, test-plan, wave-planner, execute, review) are prose-first, explain the agent's role in the pipeline, include escalation ladders, and give concrete examples. The extraction-after-prose pattern (`_extract_json`) is the right call -- agents think freely, structure is imposed cheaply afterward.

**Per-run .db files work.** The filename convention (`<repo>-<issue>-<YYYYMMDD-HHMM>.db`) is good. Four test runs exist and all have valid schema. The prior-run discovery (`find_prior_runs`) via glob matching is simple and correct.

**Gate system is solid.** Triage validation, DAG cycle detection, wave plan validation, test command security allowlist, and red/green/commit gates are all implemented and tested in run 3 and run 4. The fail-fast pattern in `run_phase_gates` is correct.

**Retry and error handling in runtime.py.** Exponential backoff on rate limits, timeout handling, FileNotFoundError for missing CLI -- all covered. The function never raises on CLI failures, which is defensive and appropriate for a pipeline.

**The orchestrator actually works.** Run 4 made it through all phases: triage (2 tasks), parallel plan + test-plan, wave-planner, parallel execute (2 tasks), and into review. That is the full pipeline working end-to-end.

### What Went Wrong

**Run 1: Empty DB.** Status "running", no phases, no events. Likely killed immediately or failed before triage started. The run row was created but nothing else happened. Not a code bug -- probably user abort.

**Run 2: JSON parse failure after triage.** The triage agent responded in prose (correct behavior), but `_extract_json` failed to parse the extraction response. Error: `Expecting value: line 1 column 1 (char 0)`. This means the extraction call returned empty or non-JSON content. The extraction prompt asks for "ONLY valid JSON" but the agent sometimes wraps it in markdown fences or adds preamble. The fence-stripping code exists but does not handle all cases (e.g., empty response, response that is just prose).

**Run 3: Wave plan format mismatch.** Error: `'list' object has no attribute 'get'`. The wave-planner returned `{"waves": [[1], [2]]}` (list of lists) but `validate_wave_plan` on line 143 of gates.py does `wave.get("tasks", [])` which fails when the wave is a plain list. The validation code handles both formats (line 143: `tasks = wave if isinstance(wave, list) else wave.get("tasks", [])`) so this is actually fine. The real bug is in the orchestrator: the `_extract_json` schema hint says `"waves": [[1, 2], [3, 4]]` (list of lists) which is correct, but the wave-planner prompt on line 76 says **"Output JSON only"** which contradicts the prose-first architecture and confuses the extraction step.

**Run 4: Stuck at "running" forever.** Made it through all phases including execute, but review has 0 cost and 0 tokens. The run status is still "running" with total_cost 0.0 (the orchestrator updates cost in `finish_run` but that never fired). This means the review phase or the push/PR step failed, but the error was not caught -- possibly a timeout or the process was killed during the review call.

### Bugs Found

#### 1. CRITICAL: `destination._read_total_cost()` queries a non-existent table

File: `engines/github_claude/destination.py`, line 162

```python
"SELECT spent_usd FROM pipeline_runs ORDER BY rowid DESC LIMIT 1"
```

The actual table is `run` with column `total_cost`, not `pipeline_runs` with `spent_usd`. This is a leftover from an earlier schema. The function silently returns `None` because the exception is caught, so the PR body just omits the cost line. Not a crash, but the PR body will never show total cost.

**Fix:** Change to `SELECT total_cost FROM run LIMIT 1`.

#### 2. CRITICAL: `stats.sh` queries the old monolithic schema

File: `scripts/stats.sh`

Every query references tables that no longer exist: `pipeline_runs`, `phase_logs`, `reviews`. The DB path is hardcoded to `runs/runs.db` (the old single-DB approach). None of these queries work with the new per-run `.db` files.

**Fix:** Rewrite stats.sh to iterate over `engines/github_claude/runs/*.db` and query the current schema (`run`, `phase`, `message`, `tool_call`, `event`).

#### 3. HIGH: Phase rows never get `finished_at` or final status

File: `engines/github_claude/orchestrator.py`, line 70

The `_log` helper calls `storage.log_phase()` which INSERT a new phase row with status "running". But `storage.finish_phase()` is never called anywhere in the orchestrator. Every phase stays "running" forever in the DB, even when it completed successfully. This makes post-run analysis unreliable.

**Fix:** Call `storage.finish_phase(conn, phase_id, "success")` after each phase completes (or "failure" on exception). The `_log` function returns the phase_id, but the orchestrator ignores the return value.

#### 4. HIGH: `engines/__init__.py` is missing

The `engines/` directory has no `__init__.py`. This means `from engines.github_claude import ...` only works because PYTHONPATH is set and Python 3's namespace packages allow it. On stricter setups or with tools that require traditional packages, this will fail.

**Fix:** Add an empty `engines/__init__.py`.

#### 5. MEDIUM: Stale JSON demand in wave-planner.md line 76

```
Output JSON only. No prose, no markdown fences.
```

This contradicts the prose-first architecture (PRD section 6: "No JSON schema demands inside agent prompts"). The wave-planner should respond in prose like every other phase. The extraction happens separately via `_extract_json`. This line confuses the agent and may cause it to skip its analysis.

**Fix:** Remove line 76 entirely.

#### 6. MEDIUM: `_extract_json` is fragile

The extraction call uses `max_turns=1` and asks for "ONLY valid JSON". If the model wraps it in fences (which the stripping code handles) or adds a preamble (which it does not handle) or returns empty content (run 2), the whole pipeline crashes with a JSON parse error.

**Fix:** Add retry logic (try extraction up to 2 times), handle empty responses, and strip leading/trailing prose more aggressively (regex for `{...}` or `[...]` extraction).

#### 7. MEDIUM: `get_diff` uses local `main` not `origin/main`

File: `engines/github_claude/workspace.py`, line 79

```python
["git", "diff", f"{base}..HEAD"]
```

The worktree is created from `origin/main` (line 63), but the diff is computed against local `main`. If local `main` is behind `origin/main`, the diff will include commits that are not part of this run. On the Mac Studio where the repo is cloned fresh, this is fine. On a laptop where `main` might be stale, it produces wrong diffs.

**Fix:** Change default to `origin/main` or run `git fetch origin main` before diffing.

#### 8. LOW: `cleanup_workspace` deletes the branch

File: `engines/github_claude/workspace.py`, line 180

After a successful run, the orchestrator calls `destination.push_branch()` and then `workspace.cleanup_workspace()` in the `finally` block. The cleanup deletes the local branch (`git branch -D`). This is fine for the worktree, but if the push failed, the branch and all commits are gone forever.

**Fix:** Only delete the branch if the run succeeded, or move branch deletion out of cleanup.

#### 9. LOW: Run 4's review phase logged with 0 cost/tokens

The `_log` helper logs cost from `resp["cost"]` but review's row shows 0. Either the review call timed out and returned a zero-cost error response, or the process was killed. The orchestrator should log the response content even on failure so we can diagnose.

#### 10. INFO: `_file_exists_fuzzy` does recursive glob

File: `engines/github_claude/gates.py`, line 285

```python
return len(list(worktree.rglob(filename))) > 0
```

On large repos, `rglob` is expensive. This runs once per target file during triage validation. For a repo with 100k files and 5 tasks with 3 target files each, that is 15 recursive globs. Not a problem now, but worth noting.

### Prompt Quality Assessment

| Prompt | Grade | Notes |
|--------|-------|-------|
| triage.md | A | Clear role, anti-hallucination rules, escalation ladder, good examples. The "granularity rules" section prevents task explosion. |
| plan.md | A- | Good structure. The "WHAT not HOW" instruction is key. Missing: explicit instruction to verify file existence before listing in writes[]. |
| test-plan.md | A- | The "test strategy by change type" table is excellent. Missing: guidance on what to do when the repo has no existing tests. |
| wave-planner.md | B | Good logic, but line 76 ("Output JSON only") violates prose-first and will confuse agents. Remove it. |
| execute.md | A | Clear TDD procedure (red then green). Good anti-hallucination rules. The commit protocol section prevents pipeline-reference leaks. |
| review.md | A | The mandatory checklist ("for every claim, show evidence") is strong. The severity levels are well-defined. |

### Summary of Run History

| Run | Status | Cost | Phases Completed | Failure Point |
|-----|--------|------|-----------------|---------------|
| 1 (0607) | running (stuck) | $0.00 | 0 | Never started |
| 2 (0608) | error | $0.21 | 1 (triage) | JSON extraction failed after triage |
| 3 (0611) | error | $0.78 | 4 (triage, plan, test-plan, wave-planner) | Wave plan format mismatch in extraction |
| 4 (0614) | running (stuck) | $1.63* | 9 (all phases) | Review phase or push/PR step |

*Run 4 cost is sum of phase costs; the run-level total_cost was never updated because finish_run was not reached.

The trajectory is positive: each run got further. The extraction step is the recurring failure point (runs 2 and 3). Run 4 made it through the full pipeline.

---

## Part 2: Mac Studio Deployment Plan

### Goal

Run pipeline jobs on the Mac Studio so they survive laptop sleep/close. Kick off from laptop, execute on Mac Studio, inspect results from either machine.

### 1. Getting the Repo on Mac Studio

**Approach: Git clone + remote tracking.**

```bash
# On Mac Studio (via SSH)
ssh studio
mkdir -p ~/repos
cd ~/repos
git clone git@github.com:personal-assistant-system/simple-workflow.git
```

Do NOT sync from laptop. The Mac Studio should have its own clone with its own origin tracking. This avoids path dependency and means the Mac Studio can pull independently.

For the **target repos** (the repos the pipeline operates on), those also need to be cloned on the Mac Studio:

```bash
# Clone each target repo the pipeline will work on
cd ~/repos
git clone git@github.com:owner/target-repo.git
```

The `--repo-path` flag on the CLI tells the pipeline where the target repo lives locally.

### 2. Triggering Runs

**Primary: SSH command from laptop.**

```bash
# From laptop
ssh studio "cd ~/repos/simple-workflow && ./scripts/run.sh owner/repo#123 --budget 2.00 --repo-path ~/repos/target-repo"
```

For fire-and-forget (survives SSH disconnect):

```bash
ssh studio "cd ~/repos/simple-workflow && nohup ./scripts/run.sh owner/repo#123 --repo-path ~/repos/target-repo > ~/logs/sw-run-\$(date +%Y%m%d-%H%M).log 2>&1 &"
```

**Alternative: Wrapper script on Mac Studio.**

Create `~/bin/sw-run` on the Mac Studio:

```bash
#!/usr/bin/env bash
set -euo pipefail
LOG_DIR="$HOME/logs/simple-workflow"
mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/$(date +%Y%m%d-%H%M%S).log"

cd "$HOME/repos/simple-workflow"
git pull --quiet origin main

echo "Starting: $@" | tee "$LOGFILE"
echo "Log: $LOGFILE"

PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" \
  python3 engines/github_claude/__main__.py "$@" \
  >> "$LOGFILE" 2>&1

echo "Done. Exit: $?" | tee -a "$LOGFILE"
```

Then from laptop: `ssh studio "sw-run owner/repo#123 --repo-path ~/repos/target-repo"`

**Future: GitHub webhook / label trigger.** A launchd service that polls for `ready-for-agent` labels (see section 7 below).

### 3. Authentication

Two CLI tools need auth on the Mac Studio:

**gh CLI:**
```bash
ssh studio
gh auth login   # Interactive, one-time. Use HTTPS + token.
gh auth status  # Verify
```

**Claude CLI:**
```bash
ssh studio
claude          # First run triggers auth flow
# Follow the browser-based auth. Since Mac Studio is headless,
# use: claude --auth-token <token> or the device code flow.
```

Note: If Claude CLI requires a browser for initial auth, do it over VNC or copy the auth token from the laptop:
```bash
# On laptop
cat ~/.config/claude/credentials.json
# On Mac Studio
mkdir -p ~/.config/claude
scp laptop:~/.config/claude/credentials.json ~/.config/claude/
```

Verify both work non-interactively:
```bash
ssh studio "gh auth status && claude --version"
```

### 4. Monitoring Runs

**Check run status from laptop:**

```bash
# List all .db files with run status
ssh studio "for db in ~/repos/simple-workflow/engines/github_claude/runs/*.db; do echo \"\$(basename \$db): \$(sqlite3 \$db 'SELECT status, total_cost FROM run;')\"; done"
```

**Tail logs in real time:**

```bash
ssh studio "tail -f ~/logs/simple-workflow/latest.log"
```

**Copy .db files to laptop for inspection:**

```bash
# Copy specific run DB
scp studio:~/repos/simple-workflow/engines/github_claude/runs/owner-repo-42-*.db ./

# Or mount via SSHFS for live access
# (if sshfs is installed)
mkdir -p ~/mnt/studio-runs
sshfs studio:~/repos/simple-workflow/engines/github_claude/runs ~/mnt/studio-runs
```

**Notification on completion (add to the wrapper script):**

```bash
# At end of sw-run script:
STATUS=$(sqlite3 "$DB_PATH" "SELECT status FROM run LIMIT 1;" 2>/dev/null || echo "unknown")
osascript -e "display notification \"Run finished: $STATUS\" with title \"simple-workflow\""
# Or use a webhook to post to Slack/Discord
```

Since the Mac Studio already runs launchd services, terminal-notifier or osascript can push notifications that show up when you VNC in. For remote notification, post a GitHub comment on the issue (the pipeline already does this on failure via `_post_failure`).

### 5. Worktree Handling

The pipeline creates worktrees under `<target-repo>/.sw-worktrees/<branch>`. This requires:

1. **The target repo must be cloned on the Mac Studio.** The `--repo-path` flag points to it.
2. **The target repo must be a full clone** (not shallow). Worktrees need the full git history for `origin/main` tracking.
3. **The target repo's `origin` must be writable** (push access). The pipeline pushes branches and creates PRs.

```bash
# On Mac Studio: clone target repos with push access
cd ~/repos
git clone git@github.com:owner/target-repo.git

# Verify push access
cd target-repo
git push --dry-run origin main  # Should succeed (no-op)
```

**Worktree cleanup after crashes:** If the Mac Studio reboots or a run is killed, stale worktrees may remain. Add a cleanup cron:

```bash
# Cron: clean stale worktrees every 6 hours
0 */6 * * * find ~/repos/*/. -name ".sw-worktrees" -exec sh -c 'cd "$(dirname "{}")" && git worktree prune' \; 2>/dev/null
```

### 6. Environment Differences

| Item | Laptop | Mac Studio | Action |
|------|--------|------------|--------|
| Python | 3.11+ | Verify version | `ssh studio "python3 --version"` |
| pip packages | PyYAML | Same needed | `ssh studio "pip3 install pyyaml"` |
| gh CLI | Installed | Verify | `ssh studio "gh --version"` |
| claude CLI | Installed | Verify | `ssh studio "claude --version"` |
| git | 2.x | Verify | `ssh studio "git --version"` |
| PATH | Standard | May differ | Ensure ~/bin is in PATH for launchd |
| PYTHONPATH | Set by run.sh | Same | run.sh handles this |
| GUI/browser | Available | None (headless) | Auth must be done once interactively or via token copy |

**Critical: The pipeline has NO browser or GUI dependencies.** All GitHub interaction is via `gh` CLI. All Claude interaction is via `claude` CLI. Both work headless. The only issue is initial auth setup (one-time).

**Python dependencies:** The project only imports `yaml` (PyYAML) beyond stdlib. No requirements.txt exists. Create one:

```
# requirements.txt
PyYAML>=6.0
```

### 7. Launchd Watcher Service

A watcher that polls GitHub for issues with the `ready-for-agent` label and triggers runs automatically.

**Watcher script: `~/bin/sw-watcher`**

```bash
#!/usr/bin/env bash
set -euo pipefail

REPOS=("owner/target-repo")
POLL_INTERVAL=300  # 5 minutes
SIMPLE_WORKFLOW="$HOME/repos/simple-workflow"
LOG="$HOME/logs/simple-workflow/watcher.log"

log() { echo "$(date -Iseconds) $*" >> "$LOG"; }

while true; do
    for repo in "${REPOS[@]}"; do
        # Find issues with ready-for-agent label
        issues=$(gh issue list --repo "$repo" --label "ready-for-agent" --json number --jq '.[].number' 2>/dev/null)

        for num in $issues; do
            # Check if we already have a running/successful run for this issue
            safe_repo="${repo//\//-}"
            existing=$(find "$SIMPLE_WORKFLOW/engines/github_claude/runs/" -name "${safe_repo}-${num}-*.db" -newer /tmp/sw-last-poll 2>/dev/null | head -1)
            if [ -n "$existing" ]; then
                continue
            fi

            log "Triggering run for $repo#$num"

            # Update label to in-progress
            gh issue edit "$num" --repo "$repo" --remove-label "ready-for-agent" --add-label "in-progress" 2>/dev/null || true

            # Run pipeline in background
            REPO_PATH="$HOME/repos/$(basename "$repo")"
            nohup "$HOME/bin/sw-run" "$repo#$num" --repo-path "$REPO_PATH" >> "$LOG" 2>&1 &
        done
    done

    touch /tmp/sw-last-poll
    sleep "$POLL_INTERVAL"
done
```

**Launchd plist: `~/Library/LaunchAgents/com.simple-workflow.watcher.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.simple-workflow.watcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/shanemattner/bin/sw-watcher</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/shanemattner/logs/simple-workflow/watcher-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/shanemattner/logs/simple-workflow/watcher-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:/Users/shanemattner/bin</string>
        <key>HOME</key>
        <string>/Users/shanemattner</string>
    </dict>
</dict>
</plist>
```

Load it:
```bash
launchctl load ~/Library/LaunchAgents/com.simple-workflow.watcher.plist
```

**Recommendation:** Start with manual SSH triggers (section 2). Add the watcher service once the pipeline has had 5-10 successful manual runs and you trust the reliability. The watcher adds complexity (polling, duplicate detection, label management) that is not worth debugging while the pipeline itself is still being stabilized.

### 8. Deployment Checklist

```
[ ] Clone simple-workflow on Mac Studio
[ ] Clone target repo(s) on Mac Studio
[ ] Install Python 3.11+ and PyYAML
[ ] Install and auth gh CLI
[ ] Install and auth claude CLI (headless)
[ ] Create ~/bin/sw-run wrapper script
[ ] Create ~/logs/simple-workflow/ directory
[ ] Verify: ssh studio "sw-run owner/repo#123 --repo-path ~/repos/target-repo --budget 0.10"
[ ] (Small budget test run to confirm end-to-end)
[ ] Fix the bugs listed in Part 1 before deploying
```

### 9. Bugs to Fix Before Deployment

Priority order:

1. **destination.py stale table reference** -- PR body will never show cost. Quick fix.
2. **Phase rows never finished** -- Makes all post-run analysis wrong. Add finish_phase calls.
3. **Wave-planner.md line 76** -- Remove "Output JSON only" line. Violates architecture.
4. **_extract_json fragility** -- Add retry and better error handling. This caused 2 of 4 runs to fail.
5. **stats.sh rewrite** -- Currently non-functional. Rewrite for per-run .db schema.
6. **Add engines/__init__.py** -- Empty file, prevents import issues on stricter setups.
7. **Add requirements.txt** -- PyYAML is the only dependency but it should be declared.

---

## Appendix: Quick Reference Commands

```bash
# Run from laptop (fire-and-forget on Mac Studio)
ssh studio "sw-run owner/repo#123 --repo-path ~/repos/target-repo"

# Check latest run status
ssh studio "ls -lt ~/repos/simple-workflow/engines/github_claude/runs/*.db | head -1 | xargs -I{} sqlite3 {} 'SELECT status, total_cost FROM run;'"

# Get all run summaries
ssh studio "for db in ~/repos/simple-workflow/engines/github_claude/runs/*.db; do echo \"\$(basename \$db)\"; sqlite3 \$db 'SELECT status, total_cost FROM run;' 2>/dev/null; echo; done"

# Copy run DB to laptop
scp studio:~/repos/simple-workflow/engines/github_claude/runs/FILENAME.db .

# Tail live log
ssh studio "tail -f ~/logs/simple-workflow/*.log"
```
