# Unified LLM Provider Architecture

To satisfy Phase 7 requirements, the Copilot engine must not hardcode OpenAI. It must utilize an abstract provider layer.

## Architecture Pattern
A Strategy Pattern via `LLMProvider(ABC)`.

```python
class LLMProvider(ABC):
    @abstractmethod
    async def stream_chat(self, system_prompt: str, messages: list, **kwargs) -> AsyncIterator[str]:
        ...
```

## Implementations Required during Merge
1. **`OpenAIProvider`**: Implemented using `openai` SDK.
2. **`AnthropicProvider`**: Needs to be implemented using `anthropic` SDK (translating `system_prompt` properly into Anthropic's message format).
3. **`OpenRouterProvider`**: Uses OpenAI SDK but overriding `base_url="https://openrouter.ai/api/v1"`.
4. **`LocalLLMProvider`**: Existing in the ZIP. Uses vLLM/Ollama via OpenAI compatible endpoints.

## Provider Router
The active provider will be determined by user settings or environment variables (`COPILOT_PROVIDER="anthropic"`), instantiated via a factory pattern `get_llm_provider()`.
