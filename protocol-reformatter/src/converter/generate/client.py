"""LLM backends for the generate stage. fill.py only depends on LlmClient."""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

# Backend used when --llm is not given: "cli" (claude), "codex" or "api".
DEFAULT_BACKEND = "cli"
DEFAULT_MODEL = "sonnet"
CLI_TIMEOUT_SECONDS = 300
API_MAX_TOKENS = 4096
API_DEFAULT_MODEL = "claude-sonnet-5-5"


class LlmError(RuntimeError):
    pass


class LlmClient(Protocol):
    name: str
    model: str

    def complete(self, prompt: str) -> str: ...


class ClaudeCliClient:
    """Runs the local `claude` CLI in print mode with every tool disabled."""

    name = "claude"

    def __init__(self, model: str = DEFAULT_MODEL, timeout: int = CLI_TIMEOUT_SECONDS) -> None:
        self.model = model
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        command = [
            "claude", "-p",
            "--model", self.model,
            "--output-format", "json",
            "--tools", "",
            "--no-session-persistence",
            "--setting-sources", "",
        ]
        try:
            result = subprocess.run(
                command, input=prompt, capture_output=True, text=True, timeout=self.timeout,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as error:
            raise LlmError(f"claude CLI failed: {error}") from error
        if result.returncode != 0:
            raise LlmError(f"claude CLI exited {result.returncode}: {result.stderr.strip()[:300]}")
        return parse_cli_output(result.stdout)


def parse_cli_output(stdout: str) -> str:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise LlmError(f"claude CLI returned non-JSON output: {stdout[:200]!r}") from error
    if envelope.get("is_error"):
        raise LlmError(f"claude CLI reported an error: {str(envelope.get('result'))[:300]}")
    text = envelope.get("result")
    if not isinstance(text, str):
        raise LlmError("claude CLI JSON has no string 'result'")
    return text


class AnthropicApiClient:
    """Same interface as ClaudeCliClient, backed by the Anthropic SDK (optional dependency)."""

    name = "claude"

    def __init__(self, model: str = API_DEFAULT_MODEL, max_tokens: int = API_MAX_TOKENS) -> None:
        try:
            import anthropic
        except ImportError as error:
            raise LlmError("the 'anthropic' package is not installed") from error
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LlmError("ANTHROPIC_API_KEY is not set")
        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic()

    def complete(self, prompt: str) -> str:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in message.content if block.type == "text")


class CodexCliClient:
    """Runs the local `codex exec` in a read-only sandbox and reads back the final message.

    With no model given, codex uses the model from the user's own config.
    """

    name = "codex"

    def __init__(self, model: str | None = None, timeout: int = CLI_TIMEOUT_SECONDS) -> None:
        self.model = model or "codex-default"
        self._model_flag = ["--model", model] if model else []
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        with tempfile.TemporaryDirectory() as work_dir:
            reply_path = Path(work_dir) / "reply.txt"
            command = [
                "codex", "exec", *self._model_flag,
                "--sandbox", "read-only",
                "--ephemeral",
                "--skip-git-repo-check",
                "--cd", work_dir,
                "--output-last-message", str(reply_path),
                "-",
            ]
            try:
                result = subprocess.run(
                    command, input=prompt, capture_output=True, text=True, timeout=self.timeout,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as error:
                raise LlmError(f"codex CLI failed: {error}") from error
            if result.returncode != 0:
                raise LlmError(f"codex CLI exited {result.returncode}: {result.stderr.strip()[:300]}")
            if not reply_path.exists():
                raise LlmError("codex CLI wrote no final message")
            return reply_path.read_text()


def make_client(backend: str, model: str | None = None) -> LlmClient:
    if backend == "cli":
        return ClaudeCliClient(model) if model else ClaudeCliClient()
    if backend == "api":
        return AnthropicApiClient(model) if model else AnthropicApiClient()
    if backend == "codex":
        return CodexCliClient(model)
    raise ValueError(f"unknown LLM backend: {backend!r} (use 'cli', 'codex' or 'api')")
