import base64
import json
import logging
import os
from random import randint
from typing import List, Optional

import boto3
import requests
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

logger = logging.getLogger()
logger.setLevel("INFO")

REQUEST_TIMEOUT = 10
region = os.environ["region_info"]
bedrock_client = boto3.client("bedrock-runtime")
host = os.environ["aoss_host"]
s3_client = boto3.client("s3")
bucket_name = os.environ["s3_bucket"]
index_name = os.environ["index_name"]
if host.startswith("https:"):
    host = host.removeprefix("https://")

embeddingSize = int(os.environ["embeddingSize"])

# similarity threshold - to retrieve the matching images from OpenSearch index
RETRIEVE_THRESHOLD = 0.2


def get_titan_multimodal_embedding(
    image_path: str = "None",
    text: str = "None",
):
    """
    This function reads the image path, and gets the embeddings by calling Titan Multimodal Embeddings model Amazon Bedrock.

    Args:
        image_path (str): Path to the input image. Defaults to "None".
        text (str): Text input for embedding. Defaults to "None".

    Returns:
        tuple: A tuple containing payload_body and vector (embeddings).
    """

    embedding_config = {
        # OutputEmbeddingLength has to be one of: [256, 384, 1024],
        "embeddingConfig": {"outputEmbeddingLength": embeddingSize}
    }

    payload_body = {}

    if image_path and image_path != "None":
        if image_path.startswith("s3"):
            payload_body["inputImage"] = load_image_from_s3(image_path)
        else:
            with open(image_path, "rb") as image_file:
                input_image = base64.b64encode(
                    image_file.read()).decode("utf8")
            payload_body["inputImage"] = input_image
    if text and (text != "None"):
        payload_body["inputText"] = text

    if (image_path == "None") and (text == "None"):
        print("please provide either an image and/or a text description")

    response = bedrock_client.invoke_model(
        body=json.dumps({**payload_body, **embedding_config}),
        modelId="amazon.titan-embed-image-v1",
        accept="application/json",
        contentType="application/json",
    )
    vector = json.loads(response.get("body").read())
    return (payload_body, vector)


def find_similar_image_in_opensearch_index(
    image_path: str = "None", text: str = "None", k: int = 1
) -> List:
    """
    Find similar images in the OpenSearch index based on image path or text query.

    Args:
        image_path (str): Path to the input image in S3. Defaults to "None".
        text (str): Text query for image search. Defaults to "None".
        k (int): Number of similar images to retrieve. Defaults to 1.

    Returns:
        List: List of retrieved images as byte strings.
    """
    logger.info(
        f"Finding similar image with params: image_path={image_path}, text={text}, k={k}"
    )
    # Create the client with SSL/TLS enabled, but hostname verification disabled.
    if host is None:
        logger.warning("Host is None, returning None")
        return None
    credentials = boto3.session.Session().get_credentials()
    aws_auth = AWSV4SignerAuth(credentials, region, "aoss")
    opensearch_client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=aws_auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        pool_maxsize=20,
        timeout=3000,
    )
    if (image_path != "None") or (text != "None"):
        _, embedding = get_titan_multimodal_embedding(
            image_path=image_path, text=text)
    query = {
        "size": 5,
        "query": {"knn": {"vector_field": {"vector": embedding["embedding"], "k": k}}},
    }
    # search for documents in the index with the given query
    response = opensearch_client.search(index=index_name, body=query)
    retrieved_images = []
    for hit in response["hits"]["hits"]:
        # only retrieve the image if the matching-score is more than a certain pre-defined threshold.
        if hit["_score"] > RETRIEVE_THRESHOLD:
            image = hit["_source"]["image_b64"]
            img = base64.b64decode(image)
            retrieved_images.append(img)

    logger.info(f"Retrieved {len(retrieved_images)} similar images")
    return retrieved_images


def titan_image(
    payload: dict,
    num_image: int = 1,
    cfg: float = 10.0,
    seed: Optional[int] = None,
    model_id: str = "amazon.titan-image-generator-v2",
) -> list:
    """
    Generate images using the Titan Image Generator model.

    Args:
        payload (dict): The input payload for image generation.
        num_image (int): Number of images to generate. Defaults to 1.
        cfg (float): Scale for classifier-free guidance. Defaults to 10.0.
        seed (int): Seed for random number generation. Defaults to None.
        model_id (str): ID of the Titan model to use. Defaults to "amazon.titan-image-generator-v2".

    Returns:
        list: List of generated images as byte strings.
    """

    #   ImageGenerationConfig Options:
    #   - numberOfImages: Number of images to be generated
    #   - quality: Quality of generated images, can be standard or premium
    #   - height: Height of output image(s)
    #   - width: Width of output image(s)
    #   - cfgScale: Scale for classifier-free guidance
    #   - seed: The seed to use for reproducibility
    seed = seed if seed is not None else randint(0, 214783647)  # noqa: F821

    params = {
        "imageGenerationConfig": {
            "numberOfImages": num_image,  # Range: 1 to 5
            "quality": "premium",  # Options: standard/premium
            "height": 1024,  # Supported height list above
            "width": 1024,  # Supported width list above
            "cfgScale": cfg,  # Range: 1.0 (exclusive) to 10.0
            "seed": seed,  # Range: 0 to 214783647
        }
    }
    body = json.dumps({**payload, **params})

    response = bedrock_client.invoke_model(
        body=body,
        modelId=model_id,
        accept="application/json",
        contentType="application/json",
    )

    response_body = json.loads(response.get("body").read())
    base64_image = response_body.get("images")[0]
    base64_bytes = base64_image.encode("ascii")
    image_bytes = base64.b64decode(base64_bytes)

    images = [
        image_bytes
    ]
    return images


def get_location_coordinates(location_name: str):
    """
    Calls the Open-Meteo Geocoding API to get the latitude and longitude
    of the given location name.

    Args:
        location_name (str): The name of the location to search for.

    Returns:
        tuple: A tuple containing the latitude and longitude of the location,
               or None if the location is not found.
    """
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={location_name}"
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    if response.status_code == 200:
        data = response.json()
        if data.get("results"):
            result = data["results"][0]
            latitude = result["latitude"]
            longitude = result["longitude"]
            return latitude, longitude
        else:
            return None, None

    return None, None


def load_image_from_s3(image_path: str):
    """
    Load an image from S3 and encode it as a base64 string.

    Args:
        image_path (str): The S3 path of the image to load.

    Returns:
        str: Base64 encoded image content or None if there's an error.
    """
    try:
        s3 = boto3.client("s3")
        _bucket_name, object_key = image_path.replace(
            "s3://", "").split("/", 1)
        response = s3.get_object(Bucket=_bucket_name, Key=object_key)
        # Read the object's body
        image_content = response["Body"].read()
        # Encode the body in bytes & decode it into a string.
        image_encoded = base64.b64encode(image_content).decode("utf8")

    except Exception as e:
        print(f"Error downloading file from S3: {e}")
        return None

    return image_encoded
