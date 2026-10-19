import unittest
from unittest.mock import Mock, patch

from agent import (
    FREE_OPENROUTER_FALLBACK_MODELS,
    get_llm_configuration,
    invoke_with_free_model_fallbacks,
    llm_error_message,
)


class LLMConfigurationTests(unittest.TestCase):
    def test_missing_or_placeholder_key_shows_setup_message(self):
        for environ in ({}, {"LLM_API_KEY": "replace_me", "LLM_MODEL": "model"}):
            with self.subTest(environ=environ), self.assertRaisesRegex(RuntimeError, "LLM_API_KEY"):
                get_llm_configuration(environ)

    def test_existing_provider_and_model_settings_are_preserved(self):
        config = get_llm_configuration({
            "LLM_API_KEY": "configured-key-value",
            "LLM_MODEL": "provider-model",
            "LLM_BASE_URL": "https://provider.invalid/v1",
            "LLM_TEMPERATURE": "0.2",
        })
        self.assertEqual(config["model"], "provider-model")
        self.assertEqual(config["base_url"], "https://provider.invalid/v1")
        self.assertEqual(config["temperature"], 0.2)

    def test_openrouter_key_uses_provider_defaults_when_endpoint_is_unset(self):
        config = get_llm_configuration({"LLM_API_KEY": "sk-or-test", "LLM_MODEL": "gpt-4o-mini"})
        self.assertEqual(config["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(config["model"], "openai/gpt-4o-mini")

    @patch("agent._streamlit_secrets", return_value={
        "LLM_API_KEY": "sk-or-cloud-test",
        "LLM_MODEL": "openai/gpt-4o-mini",
        "LLM_BASE_URL": "https://openrouter.ai/api/v1",
    })
    @patch("agent.load_dotenv")
    def test_streamlit_secrets_configure_openrouter(self, load_dotenv, secrets):
        with patch.dict("agent.os.environ", {}, clear=True):
            config = get_llm_configuration()

        self.assertEqual(config["api_key"], "sk-or-cloud-test")
        self.assertEqual(config["model"], "openai/gpt-4o-mini")
        self.assertEqual(config["base_url"], "https://openrouter.ai/api/v1")
        load_dotenv.assert_called_once()
        secrets.assert_called_once()

    def test_missing_model_uses_free_tool_capable_model(self):
        config = get_llm_configuration({"LLM_API_KEY": "sk-or-test"})
        self.assertEqual(config["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(config["model"], "nvidia/nemotron-3-super-120b-a12b:free")

    def test_provider_auth_error_message_never_echoes_credentials(self):
        class ProviderError(Exception):
            status_code = 401

        error = ProviderError("secret-key-value rejected")
        message = llm_error_message(error)
        self.assertIn("LLM_API_KEY", message)
        self.assertNotIn("secret-key-value", message)

    def test_rate_limit_message_never_suggests_paid_fallback(self):
        class RateLimitError(Exception):
            status_code = 429

        message = llm_error_message(RateLimitError())
        self.assertIn("rate limited", message)
        self.assertIn("fallback routes", message)
        self.assertIn("will not switch to a paid model", message)

    @patch("agent.get_llm_configuration")
    @patch("agent.create_banking_agent")
    def test_openrouter_rate_limit_retries_only_free_models(self, create_agent, get_config):
        class RateLimitError(Exception):
            status_code = 429

        get_config.return_value = {"model": "configured/free:model", "base_url": "https://openrouter.ai/api/v1"}
        initial_agent = Mock()
        initial_agent.invoke.side_effect = RateLimitError("busy")
        fallback_agent = Mock()
        fallback_agent.invoke.return_value = {"messages": ["success"]}
        create_agent.return_value = fallback_agent

        result = invoke_with_free_model_fallbacks(
            initial_agent, "CUS-123", Mock(), Mock(), {"messages": []}
        )

        self.assertEqual(result, {"messages": ["success"]})
        self.assertEqual(create_agent.call_count, 1)
        self.assertEqual(create_agent.call_args.kwargs["model_override"], FREE_OPENROUTER_FALLBACK_MODELS[0])
        self.assertTrue(all(model == "openrouter/free" or model.endswith(":free") for model in FREE_OPENROUTER_FALLBACK_MODELS))

    @patch("agent.get_llm_configuration")
    @patch("agent.create_banking_agent")
    def test_exhausted_free_routes_raise_rate_limit_without_paid_fallback(self, create_agent, get_config):
        class RateLimitError(Exception):
            status_code = 429

        get_config.return_value = {"model": "configured/free:model", "base_url": "https://openrouter.ai/api/v1"}
        initial_agent = Mock()
        initial_agent.invoke.side_effect = RateLimitError("busy")
        fallback_agent = Mock()
        fallback_agent.invoke.side_effect = RateLimitError("busy")
        create_agent.return_value = fallback_agent

        with self.assertRaises(RateLimitError):
            invoke_with_free_model_fallbacks(
                initial_agent, "CUS-123", Mock(), Mock(), {"messages": []}
            )
        self.assertEqual(create_agent.call_count, len(FREE_OPENROUTER_FALLBACK_MODELS))

    @patch("agent.get_llm_configuration")
    @patch("agent.create_banking_agent")
    def test_non_openrouter_rate_limit_does_not_switch_provider(self, create_agent, get_config):
        class RateLimitError(Exception):
            status_code = 429

        get_config.return_value = {"model": "provider-model", "base_url": "https://provider.invalid/v1"}
        initial_agent = Mock()
        initial_agent.invoke.side_effect = RateLimitError("busy")
        with self.assertRaises(RateLimitError):
            invoke_with_free_model_fallbacks(
                initial_agent, "CUS-123", Mock(), Mock(), {"messages": []}
            )
        create_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()