# This is the entry point for the CDK app
# It loads the configuration from config.yml and creates the FashionAgentStack

import os
from pathlib import Path

import aws_cdk as cdk
import yaml
from cdk_nag import AwsSolutionsChecks, NagSuppressions

from components.stacks.fashion_agent_stack import FashionAgentStack

# Load the configuration from config.yml
with open(os.path.join(Path(__file__).parent, "config.yml"), "r") as ymlfile:
    stack_config = yaml.load(ymlfile, Loader=yaml.loader.SafeLoader)


current_file_path = Path(__file__).resolve()

# Create the CDK app and environment
app = cdk.App()
env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=os.getenv("CDK_DEFAULT_REGION")
)

# Create the FashionAgentStack with the loaded configuration
stack = FashionAgentStack(
    scope=app, stack_name=stack_config["stack_name"], config=stack_config, env=env
)

NagSuppressions.add_stack_suppressions(
    stack,
    [
        {"id": "AwsSolutions-IAM5",
            "reason": "Need the wildcard for CloudWatch logs so the stack can create several streams"},
    ],
    True,
)


cdk.Aspects.of(app).add(AwsSolutionsChecks())
# Synthesize the AWS CloudFormation template for the stack
app.synth()
