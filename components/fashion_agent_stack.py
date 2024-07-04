import json
import subprocess
import shlex
from pathlib import Path
from typing import Any, Dict

import aws_cdk as cdk
from aws_cdk import CfnOutput, Duration, RemovalPolicy
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from cdk_nag import NagSuppressions, NagPackSuppression
from .opensearchserverless import OpenSearchServerlessConstruct
from .prompt import agent_instructions
from constructs import Construct
import os


class FashionAgentStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        stack_name: str,
        config: Dict[str, Any],
        **kwargs,
    ) -> None:
        super().__init__(scope, stack_name, **kwargs)
        self.nag_suppressed_resources = []
        current_file_path = Path(__file__).resolve()

        # Create S3 buckets
        access_log_bucket = s3.Bucket(
            self,
            "AccessLogBucket",
            bucket_name=f"fashion-agent-access-logs-{self.account}-{self.region}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
        )
        if not config["bucket_name"]:
            bucket_name = f"fashion-agent-{self.account}-{self.region}"

        bucket = s3.Bucket(
            self,
            "FashionAgentBucket",
            bucket_name=bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            enforce_ssl=True,
            auto_delete_objects=True,
            server_access_logs_bucket=access_log_bucket,
            server_access_logs_prefix="fashion-agent-logs/",
        )

        CfnOutput(
            self,
            "BucketName",
            value=bucket.bucket_name,
        )

        # Load the Schema
        schema_path = current_file_path.parent / f"{config['schema_name']}"
        with open(schema_path, "r") as f:
            schema_content = json.load(f)

        lambda_policy_document = iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel"],
                    resources=[
                        f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-embed-image-v1",
                        f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-image-generator-v1",
                    ],
                ),
                iam.PolicyStatement(
                    actions=["s3:GetObject", "s3:PutObject"],
                    resources=[bucket.bucket_arn, f"{bucket.bucket_arn}/*"],
                ),
                # iam.ManagedPolicy.from_aws_managed_policy_name(
                #    "service-role/AWSLambdaBasicExecutionRole"
                # )
            ]
        )

        policy_lambda = iam.Policy(
            self, f"{self.stack_name}-policy", document=lambda_policy_document
        )
        # Define the lambda IAM Role
        self.lambda_role = iam.Role(
            self,
            "FashionAgentLambdaRole",
            role_name="FashionAgentLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )

        self.lambda_role.attach_inline_policy(policy_lambda)
        self.nag_suppressed_resources.append(policy_lambda)
        self.nag_suppressed_resources.append(self.lambda_role)

        # Add User IAM Roles and lambda IAM Roles to a list of roles that can access opensearch
        assert config["opensearch"][
            "opensearch_arns"
        ], "Opensearch arns cannot be empty"
        role_names = [x.split("/")[-1] for x in config["opensearch"]["opensearch_arns"]]
        opensearch_access_roles = [
            iam.Role.from_role_arn(self, f"IamUser{name}", role_arn=x)
            for x, name in zip(config["opensearch"]["opensearch_arns"], role_names)
        ]
        opensearch_access_roles.append(self.lambda_role)

        # Deploy opensearch serverless
        if config["opensearch"]["deploy"]:
            self.opensearch = OpenSearchServerlessConstruct(
                self,
                "OpenSearchServerlessConstructs",
                opensearch_access_roles,
                stack_name=stack_name,
                config=config,
            )

            opensearch_endpoint_url = self.opensearch.endpoint_url
            opensearch_arn = self.opensearch.opensearch_arn

            aoss_document = iam.PolicyDocument(
                statements=[
                    iam.PolicyStatement(
                        actions=["aoss:APIAccessAll"],
                        resources=[opensearch_arn, f"{opensearch_arn}/*"],
                    )
                ]
            )

            aoss_access_policy = iam.Policy(
                self,
                "AOSSAccessPolicy",
                policy_name="AOSSAccessPolicy",
                document=aoss_document,
            )

            # Attach the AOSSAccessPolicy to allow lambda so it can read from opensearch
            self.lambda_role.attach_inline_policy(aoss_access_policy)
            self.nag_suppressed_resources.append(aoss_access_policy)
            self.nag_suppressed_resources.append(self.lambda_role)

        else:
            opensearch_endpoint_url = ""
            opensearch_arn = ""

        # Create lambda function
        lambda_function = lambda_.Function(
            self,
            "AgentLambda",
            function_name="FashionAgentLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=Duration.seconds(180),
            role=self.lambda_role,
            code=lambda_.Code.from_asset("components/lambda_function.py.zip"),
            handler="lambda_function.lambda_handler",
            environment={
                "region_info": self.region,
                "s3_bucket": bucket.bucket_name,
                "aoss_host": opensearch_endpoint_url,
                "index_name": config["opensearch"]["opensearch_index_name"],
                "embeddingSize": config["embeddingSize"],
            },
            layers=[
                lambda_.LayerVersion.from_layer_version_arn(
                    self,
                    "PillowLayer",
                    layer_version_arn=f"arn:aws:lambda:{self.region}:770693421928:layer:Klayers-p312-Pillow:2",
                ),
                lambda_.LayerVersion.from_layer_version_arn(
                    self,
                    "RequestsLayer",
                    layer_version_arn=f"arn:aws:lambda:{self.region}:770693421928:layer:Klayers-p312-requests:6",
                ),
                self.create_dependencies_layer(config["agent_name"]),
            ],
        )

        bedrock_principal = iam.ServicePrincipal(
            "bedrock.amazonaws.com",
            conditions={
                "StringEquals": {"aws:SourceAccount": self.account},
                "ArnLike": {
                    "aws:SourceArn": f"arn:aws:bedrock:{self.region}:{self.account}:agent/*"
                },
            },
        )

        lambda_function.grant_invoke(bedrock_principal)

        # Create IAM Role for the agent
        agent_role = iam.Role(
            self,
            "AgentRole",
            role_name="AmazonBedrockExecutionRoleForAgents_FashionAgent",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            inline_policies={
                "BedrockAgentBedrockAllowPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="AmazonBedrockAgentBedrockFoundationModelPolicy",
                            effect=iam.Effect.ALLOW,
                            actions=["bedrock:InvokeModel"],
                            resources=[
                                f"arn:aws:bedrock:{self.region}::foundation-model/{config['foundation_model']}"
                            ],
                        )
                    ]
                ),
                "BedrockAgentS3AllowPolicy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="AllowAgentAccessOpenAPISchema",
                            effect=iam.Effect.ALLOW,
                            actions=["s3:GetObject"],
                            resources=[
                                f"arn:aws:s3:::{bucket_name}/{config['schema_name']}"
                            ],
                        )
                    ]
                ),
            },
        )

        # Create Agent
        cfn_agent = bedrock.CfnAgent(
            self,
            "FashionAgent",
            agent_name="fashion-agent",
            auto_prepare=True,
            agent_resource_role_arn=agent_role.role_arn,
            description="Agent for fashion related topics",
            foundation_model=config["foundation_model"],
            idle_session_ttl_in_seconds=3600,
            instruction=agent_instructions,
            action_groups=[
                bedrock.CfnAgent.AgentActionGroupProperty(
                    action_group_name="imagevar",
                    action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                        lambda_=lambda_function.function_arn
                    ),
                    api_schema=bedrock.CfnAgent.APISchemaProperty(
                        payload=json.dumps(schema_content)
                    ),
                    description="Actions related to creating images and getting information to process images",
                )
            ],
        )

        CfnOutput(
            self,
            "AgentId",
            value=cfn_agent.ref,
            description="AgentId",
        )

        cfn_agent_alias = bedrock.CfnAgentAlias(
            self,
            "FashionAgentAlias",
            agent_alias_name="FashionAgentAlias",
            agent_id=cfn_agent.ref,
        )

        CfnOutput(
            self,
            "AgentAliasId",
            value=cfn_agent_alias.attr_agent_alias_id,
            description="Agent Alias ID",
        )
        self.add_nag_suppressions()

    def create_dependencies_layer(self, agent_name) -> lambda_.LayerVersion:
        requirements_file = "lambda_requirements.txt"
        output_dir = ".build/app"  # a temporary directory to store the dependencies
        os.makedirs(output_dir, exist_ok=True)
        command = f"pip install -r {shlex.quote(requirements_file)} -t {shlex.quote(output_dir)}/python".split()
        # download the dependencies and store them in the output_dir
        subprocess.check_call(command)

        layer_id = f"{agent_name}-lambda-libs"  # a unique id for the layer
        layer_code = lambda_.Code.from_asset(
            output_dir
        )  # import the dependencies / code

        my_layer = lambda_.LayerVersion(
            self,
            layer_id,
            code=layer_code,
        )

        return my_layer

    def add_nag_suppressions(self):
        NagSuppressions.add_resource_suppressions(
            self.nag_suppressed_resources,
            [
                NagPackSuppression(
                    id="AwsSolutions-IAM5",
                    reason="All IAM policies defined in this "
                    "solution"
                    "grant only least-privilege "
                    "permissions. Wild"
                    "card for resources is used only for "
                    "services"
                    "which do not have a resource arn",
                )
            ],
            apply_to_children=True,
        )
