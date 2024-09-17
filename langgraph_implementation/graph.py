from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph_elements import call_model, get_input_image, router, tool_node
from state import AgentState

ENABLE_LANGGRAPH_DEBUG_MODE = False


# Define Graph

workflow = StateGraph(AgentState)

# Define Nodes
workflow.add_node("get_input_image", get_input_image)
workflow.add_node("call_model", call_model)
workflow.add_node("tools", tool_node)

# Entrypoint
workflow.add_edge(START, "get_input_image")
workflow.add_edge("get_input_image", "call_model")
workflow.add_conditional_edges(
    "call_model", router, {"tools": "tools", "finish": END})


checkpointer = MemorySaver()

# Create runnable
graph = workflow.compile(
    checkpointer=checkpointer, interrupt_before=None, debug=ENABLE_LANGGRAPH_DEBUG_MODE
)
