"""A tiny OpenAI-compatible /v1/chat/completions server for tests (no internet, no API key).

The real stack runs against it:  MAF Agent -> OpenAIChatCompletionClient -> openai SDK -> HTTP.
`responder(body) -> str | dict` decides the answer for each request:
    str                         -> assistant message content
    {"status": 400, "code": ..}  -> an HTTP error such as Groq's json_validate_failed
Every request body is recorded in `.requests`, so tests can check what was really sent."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOpenAIServer:
    def __init__(self, responder):
        self.responder = responder
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):          # keep test output clean
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                with outer._lock:
                    outer.requests.append(body)
                out = outer.responder(body)
                if isinstance(out, dict) and out.get("status", 200) != 200:
                    payload = {"error": {"message": out.get("message", "error"), "type": "invalid_request_error",
                                         "code": out.get("code")}}
                    data, status = json.dumps(payload).encode(), out["status"]
                else:
                    finish = "stop"
                    if isinstance(out, dict) and "content" in out:          # {"content": ..., "finish_reason": "length"}
                        finish, out = out.get("finish_reason", "stop"), out["content"]
                    content = out if isinstance(out, str) else json.dumps(out)
                    payload = {"id": "chatcmpl-test", "object": "chat.completion", "created": 0,
                               "model": body.get("model", "fake"),
                               "choices": [{"index": 0, "finish_reason": finish,
                                            "message": {"role": "assistant", "content": content}}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                    data, status = json.dumps(payload).encode(), 200
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
