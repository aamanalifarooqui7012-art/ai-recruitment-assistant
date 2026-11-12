"""Optional backend API (no UI). Run:  uvicorn interview.api:app --reload  (run from the src/ folder)
In the main project Phase 3A runs inside the MAF workflow (scripts/run_workflow.py --interview, or the Streamlit app)."""
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException

from .engine import run_scaffold
from .errors import InputError, LLMCallError, LLMOutputError, PipelineError, ScaffoldError
from .schemas import InterviewScaffold, ScaffoldRequest
from .services import Services, build_services


@lru_cache(maxsize=1)
def _default_services() -> Services:
    return build_services()


def get_services() -> Services:
    return _default_services()


def create_app() -> FastAPI:
    app = FastAPI(title="Hiring Assistant - Phase 3A: Interview Scaffold", version="0.2.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/v1/interview-scaffolds", response_model=InterviewScaffold)
    async def create_scaffold(body: ScaffoldRequest, svc: Services = Depends(get_services)):
        try:
            return await run_scaffold(body, svc)
        except (InputError, PipelineError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        except (LLMOutputError, LLMCallError) as e:
            raise HTTPException(status_code=502, detail=str(e))
        except ScaffoldError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/v1/interview-scaffolds/{scaffold_id}", response_model=InterviewScaffold)
    def get_scaffold(scaffold_id: str, svc: Services = Depends(get_services)):
        found = svc.store.get(scaffold_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Scaffold not found")
        return found

    return app


app = create_app()
