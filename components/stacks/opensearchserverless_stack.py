import json

from aws_cdk import CfnOutput
from aws_cdk import aws_iam as iam
from aws_cdk import aws_opensearchserverless as opss
from cdk_nag import NagSuppressions, NagPackSuppression
from constructs import Construct


class OpenSearchServerlessConstruct(Construct):
    def __init__(
        self,
        scope,
        id: str,
        principal_roles,
        stack_name: str,
        config: dict,
        **kwargs,
    ):
        super().__init__(scope, id, **kwargs)
        self.nag_suppressed_resources = []

        self.principal_roles = principal_roles
        self.config = config
        self.stack_name = stack_name
        self.collection_name = self.config["opensearch"]["opensearch_collection_name"]

        network_security_policy = json.dumps(
            [
                {
                    "Rules": [
                        {
                            "Resource": [f"collection/{self.collection_name}"],
                            "ResourceType": "dashboard",
                        },
                        {
                            "Resource": [f"collection/{self.collection_name}"],
                            "ResourceType": "collection",
                        },
                    ],
                    "AllowFromPublic": True,
                }
            ],
            indent=2,
        )

        cfn_network_security_policy = opss.CfnSecurityPolicy(
            self,
            "NetworkSecurityPolicy",
            policy=network_security_policy,
            name=f"{self.collection_name}-ntw-pol",
            type="network",
        )

        encryption_security_policy = json.dumps(
            {
                "Rules": [
                    {
                        "Resource": [f"collection/{self.collection_name}"],
                        "ResourceType": "collection",
                    }
                ],
                "AWSOwnedKey": True,
            },
            indent=2,
        )

        cfn_encryption_security_policy = opss.CfnSecurityPolicy(
            self,
            "EncryptionSecurityPolicy",
            policy=encryption_security_policy,
            name=f"{self.collection_name}-enc-pol",
            type="encryption",
        )

        cfn_collection = opss.CfnCollection(
            self,
            "OpsSearchCollection",
            name=self.collection_name,
            description="Collection to be used for search using OpenSearch Serverless",
            type="VECTORSEARCH",
        )
        cfn_collection.add_dependency(cfn_network_security_policy)
        cfn_collection.add_dependency(cfn_encryption_security_policy)

        data_access_policy = json.dumps(
            [
                {
                    "Rules": [
                        {
                            "Resource": [f"collection/{self.collection_name}"],
                            "Permission": [
                                "aoss:*",
                            ],
                            "ResourceType": "collection",
                        },
                        {
                            "Resource": [f"index/{self.collection_name}/*"],
                            "Permission": [
                                "aoss:*",
                            ],
                            "ResourceType": "index",
                        },
                    ],
                    "Principal": [f"{role.role_arn}" for role in self.principal_roles],
                    "Description": "data-access-rule",
                }
            ],
            indent=2,
        )

        ## ********* Principal role AOSS policy *********
        aoss_access_docpolicy = iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "aoss:*",
                    ],
                    resources=["*"],
                )
            ]
        )

        aoss_access_policy = iam.Policy(
            self,
            f"{stack_name}-aoss-access-policy",
            policy_name=f"{stack_name}-aoss-access-pol",
            document=aoss_access_docpolicy,
        )

        for role in principal_roles:
            role.attach_inline_policy(aoss_access_policy)

        self.nag_suppressed_resources.append(aoss_access_policy)

        # Max length of policy name is 32
        data_access_policy_name = f"{self.collection_name[:21]}-data-pol"
        try:
            if len(data_access_policy_name) > 32:
                raise ValueError(f"data_access_policy_name '{data_access_policy_name}' exceeds 32 characters")
        except ValueError as e:
            raise ValueError(f"Error creating data access policy name: {str(e)}") from e

        opss.CfnAccessPolicy(
            self,
            "OpssDataAccessPolicy",
            name=data_access_policy_name,
            description="Policy for data access",
            policy=data_access_policy,
            type="data",
        )

        self.collection_endpoint = CfnOutput(
            self,
            f"{stack_name}-OSS-Endpoint",
            value=cfn_collection.attr_collection_endpoint,
        )
        self.dashboard_endpoint = CfnOutput(
            self,
            f"{stack_name}-OSS-DashboardsURL",
            value=cfn_collection.attr_dashboard_endpoint,
        )

        self.opensearch_arn_output = CfnOutput(
            self, f"{stack_name}-OSS-arn", value=cfn_collection.attr_arn
        )

        self.endpoint_url = cfn_collection.attr_collection_endpoint
        self.opensearch_arn = cfn_collection.attr_arn
        self.add_nag_suppressions()

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
