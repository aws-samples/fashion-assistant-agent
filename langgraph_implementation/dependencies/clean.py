import os
import boto3
from random import randint

s3_client = boto3.client('s3')
ssm_client = boto3.client('ssm')
aoss_client = boto3.client('opensearchserverless')

response = ssm_client.get_parameters(
    Names=[
        'AOSSHost', 'S3BucketName'
    ]
)
param_dict = {}
for parameter in response['Parameters']:
    param_dict[parameter['Name']] = parameter['Value']



# Empty S3 Bucket
try:
    objects = s3_client.list_objects(Bucket=param_dict['S3BucketName'])
    if 'Contents' in objects:
        for obj in objects['Contents']:
            s3_client.delete_object(Bucket=param_dict['S3BucketName'], Key=obj['Key'])
    print(f"Bucket '{param_dict['S3BucketName']}' empty successfully.")
except:
    pass

aoss_collection_id = param_dict['AOSSHost'].split('.')[0]

# Delete Collection for OpenSearchServerless
aoss_client.delete_collection(
    id=aoss_collection_id
)
print(f"Collection '{param_dict['AOSSCollectionName']}' deleted successfully.")

