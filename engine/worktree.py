"""Git worktree lifecycle management."""

import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path


def create_worktree(
    repo_path: str, branch_name: str, base_branch: str = "main"
) -> str:
    repo = Path(repo_path).resolve()
    worktrees_root = repo.parent / ".sw-worktrees"
    worktrees_root.mkdir(parents=True, exist_ok=True)
    wt_path = worktrees_root / branch_name
    full_branch = f"sw/{branch_name}"

    # Prune stale worktree references
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "prune"],
        capture_output=True,
        timeout=30,
        check=True,
    )

    # Clean up if worktree dir exists from a crashed run
    if wt_path.exists():
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(wt_path)],
            capture_output=True,
            timeout=30,
        )
        if wt_path.exists():
            shutil.rmtree(str(wt_path), ignore_errors=True)

    # Delete stale local branch from a prior crash
    branch_check = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", full_branch],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if branch_check.stdout.strip():
        subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", full_branch],
            capture_output=True,
            timeout=30,
            check=True,
        )

    # Fetch latest base branch
    subprocess.run(
        ["git", "-C", str(repo), "fetch", "origin", base_branch, "--quiet"],
        capture_output=True,
        timeout=60,
    )

    # Create worktree with new branch off base
    result = subprocess.run(
        [
            "git", "-C", str(repo), "worktree", "add",
            "-b", full_branch, str(wt_path), f"origin/{base_branch}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create worktree: {result.stderr.strip()}"
        )

    return str(wt_path)


def cleanup_worktree(worktree_path: str) -> None:
    if not worktree_path:
        return

    wt = Path(worktree_path)
    if not wt.exists():
        return

    # Find the parent repo from the worktree's .git file
    git_file = wt / ".git"
    repo_root = None
    if git_file.exists():
        content = git_file.read_text().strip()
        # .git file in worktrees contains: gitdir: /path/to/repo/.git/worktrees/<name>
        if content.startswith("gitdir:"):
            gitdir = Path(content.split(":", 1)[1].strip())
            # Walk up from .git/worktrees/<name> to the repo root
            repo_root = str(gitdir.parent.parent.parent)

    if repo_root:
        subprocess.run(
            ["git", "-C", repo_root, "worktree", "remove", "--force", worktree_path],
            capture_output=True,
            timeout=30,
        )

    if wt.exists():
        shutil.rmtree(str(wt), ignore_errors=True)

    # Prune after removal
    if repo_root:
        subprocess.run(
            ["git", "-C", repo_root, "worktree", "prune"],
            capture_output=True,
            timeout=30,
        )


def cleanup_all_worktrees(repo_path: str) -> None:
    repo = Path(repo_path).resolve()
    worktrees_root = repo.parent / ".sw-worktrees"

    if not worktrees_root.exists():
        return

    # Remove each worktree via git
    for child in worktrees_root.iterdir():
        if child.is_dir():
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(child)],
                capture_output=True,
                timeout=30,
            )

    # Nuke the directory
    shutil.rmtree(str(worktrees_root), ignore_errors=True)

    subprocess.run(
        ["git", "-C", str(repo), "worktree", "prune"],
        capture_output=True,
        timeout=30,
        check=True,
    )


@contextmanager
def worktree(repo_path: str, branch_name: str, base_branch: str = "main"):
    wt_path = create_worktree(repo_path, branch_name, base_branch)
    try:
        yield wt_path
    finally:
        cleanup_worktree(wt_path)
