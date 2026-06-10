"""Graph-based orchestration engine for Brain Engine.

Provides a LangGraph-inspired ``StateGraph`` for building directed
execution graphs with conditional routing, parallel nodes, and
checkpointing support.

Example::

    from brain_engine.graph import StateGraph, START, END, MessagesState

    def chatbot(state: MessagesState) -> dict:
        return {"messages": [{"role": "assistant", "content": "Hi!"}]}

    graph = StateGraph(MessagesState)
    graph.add_node("chatbot", chatbot)
    graph.add_edge(START, "chatbot")
    graph.add_edge("chatbot", END)
    app = graph.compile()
    result = await app.ainvoke({"messages": []})
"""

from brain_engine.graph.compiled import CompiledGraph
from brain_engine.graph.constants import END, START
from brain_engine.graph.graph import StateGraph
from brain_engine.graph.state import MessagesState, add_messages

__all__ = [
    "CompiledGraph",
    "END",
    "MessagesState",
    "START",
    "StateGraph",
    "add_messages",
]
