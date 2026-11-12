"""The only Phase 3A file that talks to a model. Its one LLM step is a Microsoft Agent Framework agent:

    role "generate"  -> agent `question-writer`      (planned slots -> interview questions)

Skills and resume evidence are NOT extracted here: they come from Phase 1 and Phase 2 (see from_phases.py).

The model is Groq `openai/gpt-oss-120b` through its OpenAI-compatible API (settings: config/config.yaml, section phase3a).
Everything else depends on the small LLMClient protocol, so tests plug in a fake."""
import json
import os
import re
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .config import LLMCfg
from .errors import LLMCallError, LLMOutputError

T = TypeVar("T", bound=BaseModel)
Role = Literal["generate"]


class LLMClient(Protocol):
    async def generate(self, schema: type[T], *, role: Role, instructions: str, prompt: str) -> T: ...


# ---------------------------------------------------------------- pure helpers (unit-tested)
def parse_json_object(text: str | None) -> dict:
    """Pull one JSON object out of model output (tolerates ```json fences and stray prose)."""
    if not text or not text.strip():
        raise LLMOutputError("model returned empty output")
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            raise LLMOutputError("no JSON object found in model output")
        try:
            data = json.loads(t[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMOutputError(f"invalid JSON in model output: {e}")
    if not isinstance(data, dict):
        raise LLMOutputError("model output is not a JSON object")
    return data


def strictify(schema: dict) -> dict:
    """Make a Pydantic JSON schema acceptable to strict structured-output mode:
    every object gets additionalProperties=false and all properties required."""

    def walk(node, in_properties: bool = False):
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for k, v in node.items():
            if not in_properties and k in ("default", "title"):
                continue
            out[k] = walk(v, in_properties=(k == "properties" and not in_properties))
        if not in_properties and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"].keys())
        return out

    return walk(schema)


class _BadRequest(LLMCallError):
    """HTTP 400 from the provider (e.g. schema mode not accepted)."""


AGENT_NAMES = {"generate": "question-writer"}
AGENT_DESCRIPTIONS = {
    "generate": "Writes one interview question per planned slot, grounded in the candidate's resume evidence.",
}


def _cause_chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or getattr(exc, "inner_exception", None)


def translate_error(exc: Exception) -> Exception:
    """MAF wraps provider errors in ChatClientException. Turn them back into this package's clear messages."""
    try:
        import openai
    except ImportError:
        return LLMCallError(f"Model call failed: {exc}")
    for e in _cause_chain(exc):
        if "api key" in str(getattr(e, "message", e)).lower():
            return LLMCallError("Groq rejected the API key. Check GROQ_API_KEY_2 in your .env file.")
        if isinstance(e, openai.BadRequestError):
            return _BadRequest(f"Groq rejected the request: {e.message}")
        if isinstance(e, openai.AuthenticationError):
            return LLMCallError("Groq rejected the API key. Check GROQ_API_KEY_2 in your .env file.")
        if isinstance(e, openai.RateLimitError):
            return LLMCallError(f"Groq rate limit reached ({e.message}). Wait a minute and retry, or shorten the input.")
        if isinstance(e, openai.APIConnectionError):
            return LLMCallError("Could not reach Groq. Check your internet connection.")
        if isinstance(e, openai.APIStatusError):
            return LLMCallError(f"Groq error {e.status_code}: {e.message}")
    return LLMCallError(f"Model call failed: {exc}")


def _was_cut_off(resp) -> bool:
    reason = getattr(resp, "finish_reason", None)
    return str(getattr(reason, "value", reason)) == "length"


# ---------------------------------------------------------------- MAF agent client
class MafLLMClient:
    """LLMClient built on Microsoft Agent Framework agents (Groq through the OpenAI-compatible chat client).

    `chat_client` can be any MAF chat client; by default one is created per call from config + .env, so every
    run (each with its own event loop) gets a fresh connection that is closed afterwards."""

    def __init__(self, cfg: LLMCfg, chat_client=None):
        self.cfg = cfg
        self._chat_client = chat_client
        self._use_schema = True  # flips to False if the provider rejects json_schema mode

    def _make_chat_client(self):
        """Returns (maf chat client, async closer or None)."""
        if self._chat_client is not None:
            return self._chat_client, None
        key = os.environ.get(self.cfg.api_key_env)
        if not key:
            raise LLMCallError(
                f"{self.cfg.api_key_env} is not set. Create a .env file next to requirements.txt "
                f"with the line: {self.cfg.api_key_env}=your-second-groq-key"
            )
        try:
            from agent_framework.openai import OpenAIChatCompletionClient
            from openai import AsyncOpenAI
        except ImportError:
            raise LLMCallError("Microsoft Agent Framework is missing. Run: pip install -r requirements.txt")
        raw = AsyncOpenAI(api_key=key, base_url=self.cfg.base_url, max_retries=4, timeout=180)
        return OpenAIChatCompletionClient(model=self.cfg.model, async_client=raw), raw

    def _options(self, schema: type[T], schema_dict: dict, use_schema: bool) -> dict:
        fmt = (
            {"type": "json_schema", "json_schema": {"name": schema.__name__, "strict": True, "schema": schema_dict}}
            if use_schema else {"type": "json_object"}
        )
        return {"response_format": fmt, "extra_body": {"reasoning_effort": self.cfg.reasoning_effort}}

    async def _run(self, agent, messages, options: dict):
        try:
            return await agent.run(messages, options=options)
        except Exception as e:
            raise translate_error(e) from e

    async def _complete(self, agent, messages, schema: type[T], schema_dict: dict):
        if self._use_schema:
            try:
                return await self._run(agent, messages, self._options(schema, schema_dict, True))
            except _BadRequest:
                self._use_schema = False  # fall back to plain JSON mode for the rest of the run
        return await self._run(agent, messages, self._options(schema, schema_dict, False))

    async def generate(self, schema: type[T], *, role: Role, instructions: str, prompt: str) -> T:
        from agent_framework import Agent, Message

        schema_dict = strictify(schema.model_json_schema())
        system = (
            f"{instructions}\n\nOUTPUT FORMAT: return ONLY one JSON object (no prose, no code fences) "
            f"that matches this JSON Schema exactly:\n{json.dumps(schema_dict, separators=(',', ':'))}"
        )
        chat, closer = self._make_chat_client()
        agent = Agent(
            chat, instructions=system, name=AGENT_NAMES[role], description=AGENT_DESCRIPTIONS[role],
            default_options={"temperature": self.cfg.temperature, "max_tokens": self.cfg.max_output_tokens},
        )
        messages = [Message("user", [prompt])]
        try:
            last_error: Exception | None = None
            for _ in range(self.cfg.max_schema_retries + 1):
                resp = await self._complete(agent, messages, schema, schema_dict)
                content = resp.text or ""
                if _was_cut_off(resp):
                    raise LLMOutputError(
                        "The model output was cut off. Raise phase3a.llm.max_output_tokens in config/config.yaml."
                    )
                try:
                    return schema.model_validate(parse_json_object(content))
                except (ValidationError, LLMOutputError) as e:
                    last_error = e
                    messages = messages[:1] + [
                        Message("assistant", [content]),
                        Message("user", [f"That output was invalid: {str(e)[:600]}\nReturn only the corrected JSON object."]),
                    ]
            raise LLMOutputError(f"Model output did not match {schema.__name__} after retries: {last_error}")
        finally:
            if closer is not None:
                await closer.close()
