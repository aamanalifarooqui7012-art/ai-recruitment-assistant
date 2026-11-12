class ScaffoldError(Exception):
    """Base class for all expected errors of this package."""


class InputError(ScaffoldError):
    """The JD or resume text is unusable (too short, too long)."""


class PipelineError(ScaffoldError):
    """The pipeline cannot produce a useful scaffold (e.g. no JD skills verified)."""


class LLMOutputError(ScaffoldError):
    """The model returned output that could not be parsed into the schema."""


class LLMCallError(ScaffoldError):
    """The model provider could not be reached or rejected the call (key, rate limit, network)."""
