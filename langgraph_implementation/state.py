from typing import Annotated, List, TypedDict

from langgraph.graph.message import AnyMessage, add_messages


class AgentState(TypedDict):
    messages: Annotated[List[AnyMessage], add_messages]
    input_image: bool = False
    output_s3_location: str = None
    database: str = None
