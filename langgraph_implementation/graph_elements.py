import logging
import os

import boto3
from botocore.config import Config
from callback_handler import RealTimeFileCallbackHandler
from langchain_aws import ChatBedrockConverse
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field
from state import AgentState
from tools import tools

logger = logging.getLogger()
logger.setLevel("INFO")


region = os.environ["region"]
host = os.environ["aoss_host"]
if host.startswith("https:"):
    host = host.removeprefix("https://")

READ_TIMEOUT_BEDROCK = 1000
MODEL_ID_BEDROCK = "anthropic.claude-3-sonnet-20240229-v1:0"


handler = RealTimeFileCallbackHandler("output.log")
config = Config(read_timeout=READ_TIMEOUT_BEDROCK)
bedrock_runtime = boto3.client(
    "bedrock-runtime", region_name=region, config=config)

####

tool_node = ToolNode(tools)

llm = ChatBedrockConverse(
    client=bedrock_runtime,
    model=MODEL_ID_BEDROCK,
    temperature=0,
    max_tokens=4096,
)


llm = llm.bind_tools(tools)


def get_input_image(state: AgentState):
    user_ques = state["messages"][-1].content

    class InputImage(BaseModel):
        "Only give out the S3 url for the image input path"
        input_image: str = Field(description="S3 url for the image input path")

    structured_llm = llm.with_structured_output(InputImage)
    model_response = structured_llm.invoke(user_ques)
    if model_response and "input_image" in vars(model_response):
        return {"input_image": model_response.input_image, "database": host}
    return {"input_image": "", "database": host}


def call_model(state: AgentState):
    messages = state["messages"]
    response = llm.invoke(messages)
    # We return a list, because this will get added to the existing list
    return {"messages": [response]}


############## EDGES ################


def router(state: AgentState):
    result = state["messages"][-1]
    # tools = [tool["name"] for tool in result.tool_calls]
    # print(f"LLM is calling the following tools for use: {tools}")
    if len(result.tool_calls) > 0:
        return "tools"
    return "finish"
