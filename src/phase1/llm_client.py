"""One place that talks to the LLM. Works with any OpenAI-compatible API (Groq etc.).
To change provider or model, edit config/config.yaml (phase1) and .env, not this file."""
import json
import os

from dotenv import load_dotenv
from openai import BadRequestError, OpenAI
from pydantic import BaseModel

from .settings import ROOT, SETTINGS

load_dotenv(ROOT / ".env", override=True)


def get_client() -> OpenAI:
    cfg = SETTINGS["llm"]
    key = os.getenv(cfg["api_key_env"], "").strip()
    if not key or "paste_your" in key:
        raise RuntimeError(
            f"API key not found. Open {ROOT / '.env'}, write "
            f"{cfg['api_key_env']}=<your key>, save the file, then run again."
        )
    return OpenAI(api_key=key, base_url=cfg["base_url"], max_retries=3)  # retries 429 automatically


def _extract_json(text: str) -> dict:
    """Take the first {...} block (also works if the model wrapped it in ```json)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in the model reply")
    return json.loads(text[start : end + 1])


def _chat(client: OpenAI, messages: list, json_mode: bool):
    cfg = SETTINGS["llm"]
    kwargs = dict(
        model=cfg["model"],
        messages=messages,
        temperature=cfg["temperature"],
        max_completion_tokens=cfg["max_output_tokens"],
    )
    if cfg.get("reasoning_effort"):
        kwargs["extra_body"] = {"reasoning_effort": cfg["reasoning_effort"]}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return client.chat.completions.create(**kwargs)


def call_json(system: str, user: str, model_cls: type[BaseModel]) -> BaseModel:
    """Ask the LLM for JSON and validate it with Pydantic.
    If the reply is not valid, tell the model what was wrong and retry (max_retries)."""
    client = get_client()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_error = None

    for _ in range(SETTINGS["llm"]["max_retries"] + 1):
        try:
            resp = _chat(client, messages, json_mode=True)
        except BadRequestError:  # provider/model without JSON mode: try plain
            resp = _chat(client, messages, json_mode=False)

        content = resp.choices[0].message.content or ""
        try:
            return model_cls.model_validate(_extract_json(content))
        except ValueError as e:  # JSON errors and Pydantic errors both land here
            last_error = e
            messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"Your reply was invalid: {str(e)[:600]}\n"
                                            "Return ONLY the corrected JSON object."},
            ]

    raise RuntimeError(f"Model output was still invalid after retries: {last_error}")
