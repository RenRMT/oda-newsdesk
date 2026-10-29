from newsdesk.llm.base import BackendUnavailable, LLMBackend


def get_backend(llm_cfg: dict, role: str = "enrich", backend: str | None = None) -> LLMBackend:
    """Build the configured backend for `role` ("enrich" or "digest")."""
    name = backend or (llm_cfg["backend"] if role == "enrich" else llm_cfg["digest_backend"])
    model = llm_cfg["models"][name][role]
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
