"""Phase I/O contracts for simple_workflow pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel


@dataclass
class PhaseContext:
    worktree_path: str
    issue_body: str
    issue_number: int
    prior_phases: dict[str, Any] = field(default_factory=dict)
    workflow_config: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    run_dir: str = ""
    db: Any = None


# --- Triage ---


class TriageTask(BaseModel):
    id: int
    title: str
    description: str
    target_files: list[str]
    depends_on: list[int]


class TriageOutput(BaseModel):
    tasks: list[TriageTask]
    proof_type: str
    escalate: bool


# --- Plan ---


class PlanStep(BaseModel):
    id: int
    title: str
    description: str
    writes: list[str]
    reads: list[str]
    depends_on: list[int]
    acceptance_test: str


class PlanOutput(BaseModel):
    steps: list[PlanStep]


# --- Test Plan ---


class TestPlanOutput(BaseModel):
    test_file: str
    test_command: str
    test_description: str
    assertions: list[str]


# --- Wave Planner ---


class Wave(BaseModel):
    tasks: list[int]
    reason: str


class WavePlannerOutput(BaseModel):
    waves: list[Wave]
    warnings: list[str]


# --- Execute ---


class ExecuteOutput(BaseModel):
    commits: list[str]
    test_passed: bool
    gate_results: dict[str, Any]


# --- Review ---


class Finding(BaseModel):
    severity: str
    category: str
    description: str
    suggestion: str


class ReviewOutput(BaseModel):
    verdict: Literal["pass", "warn", "fail"]
    score: float
    findings: list[Finding]
