from langgraph.prebuilt import create_react_agent

from AI.llm import get_openai_model
from AI.prompts import AI_CURSOR_SYSTEM_PROMPT
from AI.tools.tools import cursor_tools


def get_cursor_agent(model=None, checkpointer=None):
    llm_model = get_openai_model(model=model)
    agent = create_react_agent(
        model=llm_model,
        tools=cursor_tools,
        prompt=AI_CURSOR_SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )
    return agent