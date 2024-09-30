from config import *

# Empty and delete S3 Bucket
try:
    objects = s3_client.list_objects(Bucket=bucket_name)
    if 'Contents' in objects:
        for obj in objects['Contents']:
            s3_client.delete_object(Bucket=bucket_name, Key=obj['Key'])
    s3_client.delete_bucket(Bucket=bucket_name)
except:
    pass


# Initialize the IAM client

# The name of the policy you want to delete
# bedrock_agent_bedrock_allow_policy_name = 'YourPolicyNameHere'

def delete_policy_by_name(policy_name):
    # List all policies
    paginator = iam_client.get_paginator('list_policies')
    for response in paginator.paginate(Scope='Local'):
        for policy in response['Policies']:
            if policy['PolicyName'] == policy_name:
                policy_arn = policy['Arn']

                # Detach the policy from all roles
                response = iam_client.list_entities_for_policy(PolicyArn=policy_arn)
                for role in response['PolicyRoles']:
                    role_name = role['RoleName']
                    iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

                # Delete the policy by ARN
                try:
                    iam_client.delete_policy(PolicyArn=policy_arn)
                    print(f"Policy '{policy_name}' deleted successfully.")
                    return
                except Exception as e:
                    print(f"Error deleting policy '{policy_name}':", e)
                    return
    print(f"Policy '{policy_name}' not found.")


try:
    # Example usage
    delete_policy_by_name(bedrock_agent_bedrock_allow_policy_name)
    delete_policy_by_name(bedrock_agent_s3_allow_policy_name)
except:
    pass


# Delete Policies for OpenSearchServerless
try:
    # Delete encryption policy
    response = aoss_client.delete_security_policy(
        name='{}-policy'.format(collection_name),
        type='encryption'
    )
    print(f"Encryption policy '{collection_name}-policy' deleted successfully.")

    # Delete network policy
    response = aoss_client.delete_security_policy(
        name='{}-policy'.format(collection_name),
        type='network'
    )
    print(f"Network policy '{collection_name}-policy' deleted successfully.")

    # Delete data access policy
    response = aoss_client.delete_access_policy(
        name='{}-policy'.format(collection_name),
        type='data'
    )
    print(f"Data access policy '{collection_name}-policy' deleted successfully.")

except Exception as e:
    print(f"Error cleaning up resources: {e}")

# Delete Collection for OpenSearchServerless
try:
    # Delete collection
    response = aoss_client.delete_collection(
        id=aoss_collection_id
    )
    print(f"Collection '{aoss_collection_id}' deleted successfully.")

except Exception as e:
    print(f"Error cleaning up resources: {e}")

# Delete updated lines in Config.py
try:
    if aoss_collection_id:
        with open('dependencies/config.py', "r") as f:
            lines = f.readlines()
        with open('dependencies/config.py', "w") as f:
            f.writelines(lines[:-5])  # Remove aoss_collection_id and aoss_host
        print("aoss_collection_id and aoss_host removed from config.py")
except Exception as e:
    print(f"Config.py not change")