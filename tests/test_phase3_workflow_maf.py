"""Runs the same scenario through the real Microsoft Agent Framework graph.
Skipped automatically when agent-framework is not installed."""
import pytest

pytest.importorskip("agent_framework")

from interview.schemas import ScaffoldStatus  # noqa: E402
from maf.workflow import run_scaffold_async as run_maf  # noqa: E402   (Phase 3A runs inside the ONE workflow)

from phase3_helpers import make_svc, run, sample_request  # noqa: E402
from test_phase3_pipeline import _FabricatesFirstRound  # noqa: E402
from phase3_helpers import demo_llm  # noqa: E402


def test_maf_workflow_matches_plain_pipeline(tmp_path):
    sc = run(run_maf(sample_request(), make_svc(tmp_path)))
    assert sc.status == ScaffoldStatus.complete
    assert len(sc.questions) == 20


def test_maf_retry_edge_regenerates_failed_slot(tmp_path):
    llm = _FabricatesFirstRound(demo_llm())
    sc = run(run_maf(sample_request(), make_svc(tmp_path, llm=llm)))
    assert llm.calls == 5 and sc.status == ScaffoldStatus.complete
