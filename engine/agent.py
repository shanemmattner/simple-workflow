"""claude -p wrapper. Pipes prompts via stdin (never file indirection)."""

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

MODEL_MAP = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}


@dataclass
class AgentResult:
    raw_output: str = ""
    parsed_json: dict | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_s: float = 0.0
    stop_reason: str = ""
    prompt_hash: str = ""
    output_hash: str = ""
    output_path: str = ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_json(text: str) -> dict | None:
    """Extract first valid JSON object from text."""
    if not text:
        return None

    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    first_brace = stripped.find("{")
    if first_brace != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(first_brace, len(stripped)):
            c = stripped[i]
            if escape:
                escape = False
                continue
            if c == "\\" and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(stripped[first_brace : i + 1])
                    except json.JSONDecodeError:
                        break

    for fence in ("```json", "```"):
        if fence in stripped:
            inner = stripped.split(fence, 1)[1]
            if "```" in inner:
                inner = inner.split("```", 1)[0]
            try:
                return json.loads(inner.strip())
            except json.JSONDecodeError:
                pass

    return None


def run_agent(
    prompt: str,
    *,
    model: str = "sonnet",
    worktree_path: str = "",
    max_turns: int = 30,
    timeout: int = 600,
    run_dir: str = "",
    phase_label: str = "",
) -> AgentResult:
    model_id = MODEL_MAP.get(model, model)
    prompt_hash = _sha256(prompt)

    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        model_id,
        "--max-turns",
        str(max_turns),
        "--permission-mode",
        "auto",
    ]

    cwd = worktree_path if worktree_path else None

    if run_dir and phase_label:
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / f"{phase_label}-prompt.txt").write_text(prompt, encoding="utf-8")

    start = time.monotonic()
    raw_output = ""
    hit_timeout = False

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        raw_output = proc.stdout or ""
    except subprocess.TimeoutExpired:
        hit_timeout = True
    except Exception as exc:
        duration_s = time.monotonic() - start
        result = AgentResult(
            raw_output=str(exc),
            stop_reason="error",
            prompt_hash=prompt_hash,
            output_hash=_sha256(str(exc)),
            duration_s=duration_s,
        )
        _save_response(run_dir, phase_label, result)
        return result

    duration_s = time.monotonic() - start

    if hit_timeout:
        result = AgentResult(
            stop_reason="timeout",
            prompt_hash=prompt_hash,
            output_hash=_sha256(""),
            duration_s=duration_s,
        )
        _save_response(run_dir, phase_label, result)
        return result

    envelope = {}
    result_text = ""

    if raw_output.strip():
        try:
            payload = json.loads(raw_output.strip())
            if isinstance(payload, list):
                envelope = next(
                    (e for e in reversed(payload) if e.get("type") == "result"),
                    {},
                )
            elif isinstance(payload, dict):
                envelope = payload
        except json.JSONDecodeError:
            result_text = raw_output[:10000]

    if envelope:
        result_text = envelope.get("result", "")
        usage = envelope.get("usage", {})
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        cost_usd = envelope.get("total_cost_usd") or envelope.get("cost_usd") or 0.0
        num_turns = envelope.get("num_turns", 0)
        stop_reason = envelope.get("stop_reason", "")
    else:
        tokens_in = 0
        tokens_out = 0
        cost_usd = 0.0
        num_turns = 0
        stop_reason = "error" if proc.returncode != 0 else "unknown"

    output_hash = _sha256(result_text)
    parsed = _parse_json(result_text)

    result = AgentResult(
        raw_output=result_text,
        parsed_json=parsed,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        num_turns=num_turns,
        duration_s=duration_s,
        stop_reason=stop_reason,
        prompt_hash=prompt_hash,
        output_hash=output_hash,
    )

    _save_response(run_dir, phase_label, result)
    return result


def _save_response(run_dir: str, phase_label: str, result: AgentResult) -> None:
    if not run_dir or not phase_label:
        return
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    response_path = run_path / f"{phase_label}-response.txt"
    response_path.write_text(result.raw_output, encoding="utf-8")
    result.output_path = str(response_path)
