from newsdesk.llm.base import BackendUnavailable, LLMBackend


def backend_name(llm_cfg: dict, role: str = "enrich", backend: str | None = None) -> str:
    return backend or (llm_cfg["backend"] if role == "enrich" else llm_cfg["digest_backend"])


def get_backend(llm_cfg: dict, role: str = "enrich", backend: str | None = None, model: str | None = None) -> LLMBackend:
    """Build the configured backend for `role` ("enrich" or "digest"); `model` overrides the configured one."""
    name = backend_name(llm_cfg, role, backend)
    model = model or llm_cfg["models"][name][role]
    if not model:
        raise BackendUnavailable(f"no {role} model configured for backend '{name}'")
    if name == "anthropic":
        import anthropic

        from newsdesk.llm.anthropic_backend import AnthropicBackend

        try:
            return AnthropicBackend(model, effort=llm_cfg.get("effort") if role == "enrich" else None)
        except anthropic.AnthropicError as e:  # e.g. no credentials configured
            raise BackendUnavailable(str(e)) from e
    if name == "ollama":
        from newsdesk.llm.openai_compat import OpenAICompatBackend

        return OpenAICompatBackend(model, llm_cfg["ollama_base_url"])
    raise ValueError(f"unknown LLM backend: {name}")
