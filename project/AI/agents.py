from langgraph.prebuilt import create_react_agent

from AI.llm import get_groq_model
from AI.tools.tools import (
    cursor_tools 
)

def get_cursor_agent(model=None, checkpointer=None):
    llm_model = get_groq_model(model=model)

    agent = create_react_agent(
        model=llm_model,  
        tools=cursor_tools,  
        checkpointer=checkpointer,
    )
    return agent