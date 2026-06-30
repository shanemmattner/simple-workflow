"""Git worktree workspace management for the github_claude engine."""

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def create_workspace(repo_path: str, branch: str, base: str = "main") -> str:
    """Create a git worktree for the given branch, returning the worktree path.

    Branch naming convention: issue-<number>-<short-id>
    Worktree directory: <repo>/.sw-worktrees/<branch>

    Handles edge cases:
    - Worktree already exists (removes and recreates)
    - Branch already exists (deletes stale branch)
    - Repo not found (raises FileNotFoundError)
    """
    repo = Path(repo_path).resolve()
    if not repo.exists() or not (repo / ".git").exists():
        raise FileNotFoundError(f"Git repository not found at {repo_path}")

    worktrees_root = repo / ".sw-worktrees"
    worktrees_root.mkdir(parents=True, exist_ok=True)
    wt_path = worktrees_root / branch

    # Prune stale worktree references
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "prune"],
        capture_output=True, timeout=30, check=True,
    )

    # Clean up if worktree dir exists from a crashed run
    if wt_path.exists():
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(wt_path)],
            capture_output=True, timeout=30,
        )
        if wt_path.exists():
            shutil.rmtree(str(wt_path), ignore_errors=True)

    # Delete stale local branch from a prior crash
    branch_check = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", branch],
        capture_output=True, text=True, timeout=10,
    )
    if branch_check.stdout.strip():
        subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", branch],
            capture_output=True, timeout=30, check=True,
        )

    # Fetch latest base branch
    subprocess.run(
        ["git", "-C", str(repo), "fetch", "origin", base, "--quiet"],
        capture_output=True, timeout=60, check=True,
    )

    # Create worktree with new branch off base
    result = subprocess.run(
        [
            "git", "-C", str(repo), "worktree", "add",
            "-b", branch, str(wt_path), f"origin/{base}",
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create worktree: {result.stderr.strip()}")

    return str(wt_path)


def get_diff(workspace_path: str, base: str = "origin/main") -> str:
    """Return the combined diff of all changes in the workspace vs base branch."""
    wt = Path(workspace_path)
    if not wt.exists():
        raise FileNotFoundError(f"Workspace not found at {workspace_path}")

    result = subprocess.run(
        ["git", "diff", f"{base}..HEAD"],
        capture_output=True, text=True, cwd=workspace_path, timeout=30, check=True,
    )
    # Also include unstaged/staged changes not yet committed
    staged = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True, text=True, cwd=workspace_path, timeout=30, check=True,
    )
    unstaged = subprocess.run(
        ["git", "diff"],
        capture_output=True, text=True, cwd=workspace_path, timeout=30, check=True,
    )

    parts = [result.stdout]
    if staged.stdout:
        parts.append(staged.stdout)
    if unstaged.stdout:
        parts.append(unstaged.stdout)
    return "\n".join(parts)


def get_changed_files(workspace_path: str, base: str = "origin/main") -> list[str]:
    """Return list of file paths changed in workspace vs base branch."""
    wt = Path(workspace_path)
    if not wt.exists():
        raise FileNotFoundError(f"Workspace not found at {workspace_path}")

    # Committed changes vs base
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..HEAD"],
        capture_output=True, text=True, cwd=workspace_path, timeout=30,
    )
    files = set(result.stdout.strip().splitlines()) if result.stdout.strip() else set()

    # Staged but not yet committed
    staged = subprocess.run(
        ["git", "diff", "--name-only", "--cached"],
        capture_output=True, text=True, cwd=workspace_path, timeout=30,
    )
    if staged.stdout.strip():
        files.update(staged.stdout.strip().splitlines())

    # Unstaged modifications
    unstaged = subprocess.run(
        ["git", "diff", "--name-only"],
        capture_output=True, text=True, cwd=workspace_path, timeout=30,
    )
    if unstaged.stdout.strip():
        files.update(unstaged.stdout.strip().splitlines())

    return sorted(files)


def cleanup_workspace(workspace_path: str) -> None:
    """Remove the worktree directory and delete the branch.

    Handles the case where the worktree was already deleted.
    """
    if not workspace_path:
        return

    wt = Path(workspace_path)
    branch = wt.name  # branch name is the directory name

    # Find the parent repo from the worktree's .git file
    repo_root = None
    git_file = wt / ".git"
    if git_file.exists():
        content = git_file.read_text().strip()
        if content.startswith("gitdir:"):
            gitdir = Path(content.split(":", 1)[1].strip())
            # Walk up from .git/worktrees/<name> to the repo root
            repo_root = str(gitdir.parent.parent.parent)
    else:
        # Worktree dir might be gone; infer repo from parent structure
        # Expected layout: <repo>/.sw-worktrees/<branch>
        if wt.parent.name == ".sw-worktrees":
            candidate = wt.parent.parent
            if (candidate / ".git").exists():
                repo_root = str(candidate)

    # Remove worktree via git
    if repo_root:
        result = subprocess.run(
            ["git", "-C", repo_root, "worktree", "remove", "--force", workspace_path],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning("git worktree remove failed for %s: %s", workspace_path, result.stderr.strip())

    # Force-remove directory if still present
    if wt.exists():
        shutil.rmtree(str(wt), ignore_errors=True)

    # Prune and delete the branch
    if repo_root:
        subprocess.run(
            ["git", "-C", repo_root, "worktree", "prune"],
            capture_output=True, timeout=30,
        )
        # Delete the branch
        result = subprocess.run(
            ["git", "-C", repo_root, "branch", "-D", branch],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning("git branch -D failed for %s: %s", branch, result.stderr.strip())
