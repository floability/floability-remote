import importlib.util
import os
import unittest
from unittest.mock import patch

from floability_remote.application.errors import OperationError
from floability_remote.agent.models import (
    DEFAULT_OLLAMA_BASE_URL,
    parse_model,
)


class ModelSelectionTests(unittest.TestCase):
    def test_default_and_bare_tags_select_ollama(self):
        default = parse_model("")
        tagged = parse_model("qwen3:8b", "http://ollama.example/v1")

        self.assertEqual(default.display_name, "ollama:qwen3")
        self.assertEqual(default.base_url, DEFAULT_OLLAMA_BASE_URL)
        self.assertEqual(tagged.provider, "ollama")
        self.assertEqual(tagged.name, "qwen3:8b")
        self.assertEqual(tagged.base_url, "http://ollama.example/v1")

    def test_hosted_provider_prefixes_are_preserved(self):
        self.assertEqual(
            parse_model("openai:gpt-5").display_name,
            "openai:gpt-5",
        )
        self.assertEqual(
            parse_model("anthropic:claude-sonnet-4-5").display_name,
            "anthropic:claude-sonnet-4-5",
        )

    def test_ollama_url_uses_environment(self):
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://localhost:9999/v1"}):
            self.assertEqual(
                parse_model("ollama:gemma3").base_url,
                "http://localhost:9999/v1",
            )

    def test_provider_without_model_is_rejected(self):
        with self.assertRaisesRegex(OperationError, "model name is required"):
            parse_model("openai:")

    @unittest.skipUnless(
        all(
            importlib.util.find_spec(name) is not None
            for name in ("anthropic", "openai", "pydantic_ai")
        ),
        "install the agent extra for hosted provider construction",
    )
    def test_hosted_specs_construct_provider_models_without_requests(self):
        from floability_remote.agent.models import create_model
        from floability_remote.agent.policy import ApprovalPolicy
        from floability_remote.agent.runtime import create_agent

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-only",
                "ANTHROPIC_API_KEY": "test-only",
            },
        ):
            openai_agent = create_agent(
                create_model(parse_model("openai:gpt-5")),
                ApprovalPolicy(assume_yes=True),
            )
            anthropic_agent = create_agent(
                create_model(parse_model("anthropic:claude-sonnet-4-5")),
                ApprovalPolicy(assume_yes=True),
            )

        self.assertEqual(type(openai_agent.model).__name__, "OpenAIResponsesModel")
        self.assertEqual(type(anthropic_agent.model).__name__, "AnthropicModel")


if __name__ == "__main__":
    unittest.main()
