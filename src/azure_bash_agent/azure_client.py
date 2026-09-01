"""Construct the Azure-authenticated OpenAI Responses adapter."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, cast

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI, OpenAIError

from azure_bash_agent.agent import ModelRequestError, ModelResponse, ResponsesClient
from azure_bash_agent.config import LlmSettings

AZURE_SCOPE = "https://ai.azure.com/.default"


class _SdkResponse(Protocol):
    output: Sequence[object]
    output_text: str


class _OpenAIResponsesClient:
    def __init__(self, create: Callable[..., object]) -> None:
        self._create = create

    def create(
        self,
        *,
        model: str,
        input: list[object],
        instructions: str,
        tools: list[dict[str, object]],
        store: bool,
    ) -> ModelResponse:
        try:
            response = cast(
                _SdkResponse,
                self._create(
                    model=model,
                    input=input,
                    instructions=instructions,
                    tools=tools,
                    store=store,
                ),
            )
        except OpenAIError:
            raise ModelRequestError("model request failed") from None
        return ModelResponse(output=list(response.output), output_text=response.output_text)


def create_responses_client(settings: LlmSettings) -> ResponsesClient:
    """Create a refreshable Entra-authenticated Responses client."""
    credential = DefaultAzureCredential()
    credential.get_token(AZURE_SCOPE)
    token_provider = get_bearer_token_provider(credential, AZURE_SCOPE)
    client = OpenAI(
        base_url=settings.base_url,
        api_key=token_provider,
        timeout=settings.request_timeout_seconds,
    )
    return _OpenAIResponsesClient(client.responses.create)
