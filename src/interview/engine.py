import sys

from .errors import ScaffoldError
from .schemas import InterviewScaffold, ScaffoldRequest
from .services import Services


async def run_scaffold(req: ScaffoldRequest, svc: Services, engine: str | None = None) -> InterviewScaffold:
    """Single entry point used by the API: one candidate. engine "maf" (default) runs the project's ONE workflow
    (src/maf), engine "plain" runs the same steps in a simple loop (no Microsoft Agent Framework needed)."""
    engine = engine or svc.cfg.workflow.engine
    if engine == "maf":
        try:
            from maf.workflow import run_scaffold_async
        except ImportError as e:  # agent-framework missing or its API moved
            print(f"[note] Microsoft Agent Framework not usable ({e}); running the same steps without it.", file=sys.stderr)
            engine = "plain"
        else:
            return await run_scaffold_async(req, svc)
    if engine == "plain":
        from .pipeline import run_plain
        return await run_plain(req, svc)
    raise ScaffoldError(f"Unknown engine '{engine}' (use 'maf' or 'plain').")
