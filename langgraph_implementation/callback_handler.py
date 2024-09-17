import re

from langchain.callbacks.base import BaseCallbackHandler
from langchain.schema import AIMessage


class RealTimeFileCallbackHandler(BaseCallbackHandler):
    def __init__(self, output_file):
        self.output_file = output_file

    def on_chat_model_start(
        self, serialized, message, *, run_id, parent_run_id, tags, metadata, **kwargs
    ):
        for msgs in message:
            for msg in msgs:
                if isinstance(msg, AIMessage):
                    with open(self.output_file, "w") as f:
                        dump = re.sub(
                            r"###########.*?###########", "", msg.content
                        ).rstrip()
                        # f.write(msg.content)
                        f.write(dump)
