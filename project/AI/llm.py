from django.conf import settings
from langchain_openai import ChatOpenAI


def get_openai_api_key():
    key = getattr(settings, "OPENAI_API_KEY", None)
    if not key or not str(key).strip():
        raise ValueError(
            "OPENAI_API_KEY is not set. Add it to your .env file for Prompt Driven Development (PDD) to work."
        )
    return key


def get_openai_model(model="gpt-4o"):
    """LLM for PDD: natural-language prompts → tool calls → code generation."""
    if model is None:
        model = "gpt-4o-mini"
    return ChatOpenAI(
        model=model,
        temperature=0,
        max_retries=2,
        api_key=get_openai_api_key(),
    )