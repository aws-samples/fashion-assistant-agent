# This is the entry point for the CDK app
# It loads the configuration from config.yml and creates the FashionAgentStack

import os
from pathlib import Path
from zipfile import ZipFile

import aws_cdk as cdk
import yaml
from components.fashion_agent_stack import FashionAgentStack
from yaml.loader import SafeLoader


def zip_file(source_file, dest_file):
    # Zip the file and save it to the destination directory
    with ZipFile(dest_file, "w") as zip_file:
        zip_file.write(source_file, source_file.name)


# Load the configuration from config.yml
with open(os.path.join(Path(__file__).parent, "config.yml"), "r") as ymlfile:
    stack_config = yaml.load(ymlfile, Loader=SafeLoader)


current_file_path = Path(__file__).resolve()

zip_file(
    source_file=current_file_path.parent / "components/lambda_function.py",
    dest_file=current_file_path.parent / "components/lambda_function.py.zip",
)

# Create the CDK app and environment
app = cdk.App()
env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"), region="us-east-1"
)

# Create the FashionAgentStack with the loaded configuration
stack = FashionAgentStack(
    scope=app, stack_name=stack_config["stack_name"], config=stack_config, env=env
)

# Synthesize the AWS CloudFormation template for the stack
app.synth()
