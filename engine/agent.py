"""Claude CLI wrapper. Single-turn subscription calls via claude CLI."""

import hashlib
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds; exponential: 2, 4, 8
_RETRYABLE_PATTERNS = re.compile(r"rate limit|429|500|503|overloaded", re.IGNORECASE)

MODEL_MAP = {
    "haiku": "haiku",
    "sonnet": "sonnet",
    "opus": "opus",
}


@dataclass
class AgentResult:
    raw_output: str = ""
    parsed_json: dict | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
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
    model_name = MODEL_MAP.get(model, model)
    prompt_hash = _sha256(prompt)

    if run_dir and phase_label:
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        (run_path / f"{phase_label}-prompt.txt").write_text(prompt, encoding="utf-8")

    cmd = ["claude", prompt, "--output-format", "json", "--model", model_name]

    start = time.monotonic()

    for attempt in range(_MAX_RETRIES):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            elapsed = time.monotonic() - start

            if proc.returncode != 0:
                stderr = proc.stderr.strip()
                # Retry on transient server errors (rate limit, 429, 500, 503, overloaded)
                if attempt < _MAX_RETRIES - 1 and _RETRYABLE_PATTERNS.search(stderr):
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("run_agent retryable error (attempt %d/%d), retrying in %.0fs: %s",
                                attempt + 1, _MAX_RETRIES, delay, stderr[:200])
                    time.sleep(delay)
                    continue

                result = AgentResult(
                    raw_output=stderr,
                    stop_reason="error",
                    prompt_hash=prompt_hash,
                    output_hash=_sha256(stderr),
                    duration_s=elapsed,
                )
                _save_response(run_dir, phase_label, result)
                return result

            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                result = AgentResult(
                    raw_output=proc.stdout[:500],
                    stop_reason="error",
                    prompt_hash=prompt_hash,
                    output_hash=_sha256(proc.stdout[:500]),
                    duration_s=elapsed,
                )
                _save_response(run_dir, phase_label, result)
                return result

            # claude --output-format json returns a list of events;
            # extract the result envelope from the last "result" event
            if isinstance(data, list):
                result_event = None
                for event in reversed(data):
                    if isinstance(event, dict) and event.get("type") == "result":
                        result_event = event
                        break
                if result_event is None:
                    result = AgentResult(
                        raw_output=f"No result event in claude output ({len(data)} events)",
                        stop_reason="error",
                        prompt_hash=prompt_hash,
                        output_hash=_sha256(""),
                        duration_s=elapsed,
                    )
                    _save_response(run_dir, phase_label, result)
                    return result
                data = result_event

            # Don't retry clean application errors from Claude
            if data.get("is_error"):
                error_text = data.get("result", "")
                result = AgentResult(
                    raw_output=error_text,
                    stop_reason="error",
                    prompt_hash=prompt_hash,
                    output_hash=_sha256(error_text),
                    duration_s=elapsed,
                )
                _save_response(run_dir, phase_label, result)
                return result

            usage = data.get("usage", {})
            tokens_in = usage.get("input_tokens", 0)
            tokens_out = usage.get("output_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_creation = usage.get("cache_creation_input_tokens", 0)
            cost_usd = data.get("total_cost_usd", 0.0)
            result_text = data.get("result", "")
            output_hash = _sha256(result_text)
            parsed = _parse_json(result_text)

            result = AgentResult(
                raw_output=result_text,
                parsed_json=parsed,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_creation,
                cost_usd=cost_usd,
                num_turns=1,
                duration_s=elapsed,
                stop_reason="end_turn",
                prompt_hash=prompt_hash,
                output_hash=output_hash,
            )
            _save_response(run_dir, phase_label, result)
            return result

        except subprocess.TimeoutExpired:
            # Never retry timeouts
            elapsed = time.monotonic() - start
            result = AgentResult(
                stop_reason="timeout",
                prompt_hash=prompt_hash,
                output_hash=_sha256(""),
                duration_s=elapsed,
            )
            _save_response(run_dir, phase_label, result)
            return result

        except FileNotFoundError:
            # Never retry missing binary
            elapsed = time.monotonic() - start
            result = AgentResult(
                raw_output="claude CLI not found in PATH",
                stop_reason="error",
                prompt_hash=prompt_hash,
                output_hash=_sha256(""),
                duration_s=elapsed,
            )
            _save_response(run_dir, phase_label, result)
            return result

        except Exception as e:
            elapsed = time.monotonic() - start
            result = AgentResult(
                raw_output=str(e),
                stop_reason="error",
                prompt_hash=prompt_hash,
                output_hash=_sha256(str(e)),
                duration_s=elapsed,
            )
            _save_response(run_dir, phase_label, result)
            return result

    # All retries exhausted (shouldn't reach here, but defensive)
    elapsed = time.monotonic() - start
    result = AgentResult(
        raw_output="All retries exhausted",
        stop_reason="error",
        prompt_hash=prompt_hash,
        output_hash=_sha256(""),
        duration_s=elapsed,
    )
    _save_response(run_dir, phase_label, result)
    return result


def save_error(
    run_dir: str,
    phase: str,
    error_type: str,
    error_message: str,
    traceback_str: str = "",
) -> None:
    """Write structured error info to {run_dir}/error.json."""
    if not run_dir:
        return
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    error_data = {
        "phase": phase,
        "error_type": error_type,
        "error_message": error_message,
        "traceback": traceback_str,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    (run_path / "error.json").write_text(
        json.dumps(error_data, indent=2), encoding="utf-8"
    )


def _save_response(run_dir: str, phase_label: str, result: AgentResult) -> None:
    if not run_dir or not phase_label:
        return
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    response_path = run_path / f"{phase_label}-response.txt"
    response_path.write_text(result.raw_output, encoding="utf-8")
    result.output_path = str(response_path)
