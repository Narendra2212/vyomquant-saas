# LLM Configuration Contract Audit

## 1. Expected Attributes (from `core/copilot_engine.py`)

* **`OpenAIProvider`**: 
  * `settings.openai_api_key`
  * `settings.openai_model`
* **`AnthropicProvider`**: *Not implemented in copilot_engine.py*
* **`OpenRouterProvider`**: *Not implemented in copilot_engine.py*
* **`OllamaProvider`**: *Not implemented by name (uses generic `LocalLLMProvider` which relies on hardcoded URLs or passed arguments, not Settings).*

## 2. Actual Available Attributes (from `core/config.py`)

Under the `AI COPILOT LLM PROVIDERS` section:
* `COPILOT_PROVIDER`
* `OPENAI_API_KEY`
* `ANTHROPIC_API_KEY`
* `OPENROUTER_API_KEY`
* `OLLAMA_URL`

## 3. Configuration Contract Mismatches

| Expected Attribute | Actual Attribute | Provider | Compatibility Status |
|--------------------|------------------|----------|----------------------|
| `openai_api_key` | `OPENAI_API_KEY` | OpenAIProvider | DIRECT REPLACEMENT SAFE |
| `openai_model` | *Missing from Config* | OpenAIProvider | ARCHITECTURE CONFLICT |
| *Anthropic expected* | `ANTHROPIC_API_KEY` | AnthropicProvider | ARCHITECTURE CONFLICT (Class missing) |
| *OpenRouter expected* | `OPENROUTER_API_KEY` | OpenRouterProvider | ARCHITECTURE CONFLICT (Class missing) |
| *Ollama expected* | `OLLAMA_URL` | OllamaProvider | ARCHITECTURE CONFLICT (Class missing, generic LocalLLM exists) |

## 4. Remaining Mismatches in `core/copilot_engine.py`
The following invalid attributes are actively used in `core/copilot_engine.py` and will block execution:

1. **`settings.openai_api_key`** (Line 50) -> Replace with `settings.OPENAI_API_KEY`.
2. **`settings.openai_model`** (Lines 51, 189, 191, 196) -> This attribute does not exist in `core/config.py`. It requires either adding `OPENAI_MODEL` to the core settings, or hardcoding a fallback string (e.g., `"gpt-4o"`) directly into the provider to prevent a crash.
