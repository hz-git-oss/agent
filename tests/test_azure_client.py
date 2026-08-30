from types import SimpleNamespace

import pytest
from openai import OpenAIError

import azure_bash_agent.azure_client as azure_client
from azure_bash_agent.agent import ModelRequestError
from azure_bash_agent.config import LlmSettings


def test_factory_gets_token_before_provider_and_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    credential = SimpleNamespace(get_token=lambda scope: events.append(f"token:{scope}"))

    monkeypatch.setattr(
        azure_client,
        "DefaultAzureCredential",
        lambda: events.append("credential") or credential,
    )
    monkeypatch.setattr(
        azure_client,
        "get_bearer_token_provider",
        lambda received, scope: events.append(f"provider:{scope}") or (lambda: "token"),
    )
    monkeypatch.setattr(
        azure_client,
        "OpenAI",
        lambda **kwargs: (
            events.append("client")
            or SimpleNamespace(responses=SimpleNamespace(create=lambda **request: None))
        ),
    )

    azure_client.create_responses_client(
        LlmSettings("https://example.openai.azure.com/openai/v1/", "model", 30)
    )

    assert events == [
        "credential",
        f"token:{azure_client.AZURE_SCOPE}",
        f"provider:{azure_client.AZURE_SCOPE}",
        "client",
    ]


def test_factory_passes_same_credential_to_token_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = SimpleNamespace(get_token=lambda scope: None)
    received: list[object] = []
    monkeypatch.setattr(azure_client, "DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr(
        azure_client,
        "get_bearer_token_provider",
        lambda value, scope: received.append(value) or (lambda: "token"),
    )
    monkeypatch.setattr(
        azure_client,
        "OpenAI",
        lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=lambda **request: None)),
    )

    azure_client.create_responses_client(
        LlmSettings("https://example.openai.azure.com/openai/v1/", "model", 30)
    )

    assert received == [credential]


def test_openai_receives_callable_url_and_timeout_without_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = SimpleNamespace(get_token=lambda scope: None)

    def provider() -> str:
        return "token"

    received: list[dict[str, object]] = []
    monkeypatch.setattr(azure_client, "DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr(
        azure_client,
        "get_bearer_token_provider",
        lambda value, scope: provider,
    )
    monkeypatch.setattr(
        azure_client,
        "OpenAI",
        lambda **kwargs: (
            received.append(kwargs)
            or SimpleNamespace(responses=SimpleNamespace(create=lambda **request: None))
        ),
    )
    settings = LlmSettings("https://example.services.ai.azure.com/openai/v1/", "model", 17.5)

    azure_client.create_responses_client(settings)

    assert received == [
        {
            "base_url": settings.base_url,
            "api_key": provider,
            "timeout": settings.request_timeout_seconds,
        }
    ]


def test_adapter_calls_responses_create_and_retains_public_output_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = SimpleNamespace(get_token=lambda scope: None)
    output_item = SimpleNamespace(type="unknown")
    requests: list[dict[str, object]] = []

    def create(**request: object) -> object:
        requests.append(request)
        return SimpleNamespace(output=[output_item], output_text="answer")

    monkeypatch.setattr(azure_client, "DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr(
        azure_client,
        "get_bearer_token_provider",
        lambda value, scope: lambda: "token",
    )
    monkeypatch.setattr(
        azure_client,
        "OpenAI",
        lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    client = azure_client.create_responses_client(
        LlmSettings("https://example.openai.azure.com/openai/v1/", "model", 30)
    )

    response = client.create(
        model="model",
        input=[{"role": "user", "content": "task"}],
        instructions="instructions",
        tools=[],
        store=False,
    )

    assert response.output == [output_item]
    assert response.output[0] is output_item
    assert response.output_text == "answer"
    assert requests == [
        {
            "model": "model",
            "input": [{"role": "user", "content": "task"}],
            "instructions": "instructions",
            "tools": [],
            "store": False,
        }
    ]


def test_adapter_translates_provider_request_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = SimpleNamespace(get_token=lambda scope: None)

    def create(**request: object) -> object:
        raise OpenAIError("provider-secret")

    monkeypatch.setattr(azure_client, "DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr(
        azure_client,
        "get_bearer_token_provider",
        lambda value, scope: lambda: "token",
    )
    monkeypatch.setattr(
        azure_client,
        "OpenAI",
        lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    client = azure_client.create_responses_client(
        LlmSettings("https://example.openai.azure.com/openai/v1/", "model", 30)
    )

    with pytest.raises(ModelRequestError, match="model request failed"):
        client.create(
            model="model",
            input=[{"role": "user", "content": "task"}],
            instructions="instructions",
            tools=[],
            store=False,
        )
