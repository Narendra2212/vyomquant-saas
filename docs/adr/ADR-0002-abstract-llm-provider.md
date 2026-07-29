# ADR-0002: Abstract LLM Provider Architecture

## Status
Accepted

## Context
The VyomQuant AI Copilot engine originally hardcoded OpenAI as the exclusive LLM provider. This created vendor lock-in, exposed the platform to OpenAI-specific API outages, and prevented us from utilizing highly specialized financial open-source models (like localized Llama deployments) or Anthropic's Claude for context-heavy prompt tasks.

## Decision
We implemented a unified LLM Provider Architecture using an Abstract Base Class (ABC) for the `copilot_engine.py`. This abstracts text generation, embeddings, and token counting behind a standard `LLMProvider` interface.

## Consequences
- **Positive**: We can hot-swap providers via environment variables (`LLM_PROVIDER=anthropic` or `LLM_PROVIDER=openai`) without altering business logic.
- **Positive**: Enhanced resilience; if OpenAI goes down, the system can gracefully fall back to an alternate provider.
- **Negative**: Feature parity constraints. We can only utilize LLM features that exist across all supported providers, preventing us from using highly specific features like OpenAI's Assistants API natively unless we abstract them.
