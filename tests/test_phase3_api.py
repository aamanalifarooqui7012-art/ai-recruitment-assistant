import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from interview.api import create_app, get_services  # noqa: E402

from phase3_helpers import make_svc, sample_request  # noqa: E402


def _client(tmp_path):
    app = create_app()
    svc = make_svc(tmp_path)
    svc.cfg.workflow.engine = "plain"
    app.dependency_overrides[get_services] = lambda: svc
    return TestClient(app)


def test_create_then_fetch(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/v1/interview-scaffolds", json=sample_request().model_dump())
    assert r.status_code == 200
    body = r.json()
    assert body["review_status"] == "pending_human_review"
    assert c.get(f"/api/v1/interview-scaffolds/{body['scaffold_id']}").status_code == 200


def test_short_input_is_422_and_bad_id_is_404(tmp_path):
    c = _client(tmp_path)
    assert c.post("/api/v1/interview-scaffolds", json={"jd_text": "x", "resume_text": "y"}).status_code == 422
    assert c.get("/api/v1/interview-scaffolds/not-a-valid-id").status_code == 404
