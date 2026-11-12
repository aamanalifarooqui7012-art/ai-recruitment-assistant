"""Phase 3B has no workflow of its own any more: its steps are executors of the ONE workflow (workflow.py).
This module keeps the old import path alive:  from maf.evaluation import EvaluationRequest, run_evaluation"""
from .models import EvalState, EvaluationRequest
from .workflow import run_evaluation, run_evaluation_async

__all__ = ["EvaluationRequest", "EvalState", "run_evaluation", "run_evaluation_async"]
