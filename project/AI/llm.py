from django.conf import settings
from langchain_groq import ChatGroq


def get_groq_api_key():
    return settings.GROQ_API_KEY


def get_groq_model(model="llama-3.1-8b-instant"):
    if model is None:
        model = "llama-3.1-8b-instant"
    return ChatGroq(
        model=model,
        temperature=0,
        max_retries=2,
        api_key=get_groq_api_key(), 
    )

