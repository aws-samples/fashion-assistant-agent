import base64
import json
import os
from random import randint
from typing import List
from io import BytesIO

import boto3
import requests
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection
import logging

logger = logging.getLogger()
logger.setLevel("INFO")

REQUEST_TIMEOUT = 10

region = os.environ["region_info"]
bedrock_client = boto3.client("bedrock-runtime")
s3_client = boto3.client("s3")
bucket_name = os.environ["s3_bucket"]
host = os.environ["aoss_host"]
if host.startswith("https:"):
    host = host.removeprefix("https://")
index_name = os.environ["index_name"]
embeddingSize = int(os.environ["embeddingSize"])

# similarity threshold - to retrieve the matching images from OpenSearch index
RETRIEVE_THRESHOLD = 0.2


def get_named_parameter(event, name):
    """
    Retrieve the value of a named parameter from the event object.

    Args:
        event (dict): The event object containing parameters.
        name (str): The name of the parameter to retrieve.

    Returns:
        The value of the named parameter or None if not found.
    """
    try:
        return next(item for item in event["parameters"] if item["name"] == name)[
            "value"
        ]
    except:
        return None


def get_weather(event):
    """
    Retrieves current weather data from Open-Meteo API for the given location.

    Args:
        event (dict): The event object containing parameters including latitude and longitude.

    Returns:
        dict: A dictionary with 'body' containing the current weather data and 'response_code'.
    """
    logger.info(f"Getting weather for event: {event}")
    location_name = get_named_parameter(event, "location_name")
    logger.info(f"Location name: {location_name}")

    latitude, longitude = get_location_coordinates(location_name)
    if not latitude or not longitude:
        logger.warning(f"Could not find location coordinates for {location_name}")
        raise Exception(f"Error: Could not find location {location_name}")

    base_url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current_weather": True,
        "hourly": "temperature_2m,relativehumidity_2m,windspeed_10m",
    }

    response = requests.get(base_url, params=params, timeout=REQUEST_TIMEOUT)

    if response.status_code == 200:
        data = response.json()
    else:
        response_code = 400
        results = {
            "body": "There is no weather information for this location. Use default value.",
            "response_code": response_code,
        }
        logger.warning(f"No weather data found for {location_name}")
        return results

    payload = {
        "taskType": "WEATHER",
        "weatherParams": {
            "location_name": location_name,  # Required
        },
    }
    weather_data = data

    if weather_data:
        current_weather = weather_data["current_weather"]
        temperature = current_weather["temperature"]
        weathercode = current_weather["weathercode"]
        weather_code_dict = {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Depositing rime fog",
            51: "Drizzle: Light intensity",
            53: "Drizzle: Moderate intensity",
            55: "Drizzle: Dense intensity",
            56: "Freezing Drizzle: Light intensity",
            57: "Freezing Drizzle: Dense intensity",
            61: "Rain: Slight intensity",
            63: "Rain: Moderate intensity",
            65: "Rain: Heavy intensity",
            66: "Freezing Rain: Light intensity",
            67: "Freezing Rain: Heavy intensity",
            71: "Snow fall: Slight intensity",
            73: "Snow fall: Moderate intensity",
            75: "Snow fall: Heavy intensity",
            77: "Snow grains",
            80: "Rain showers: Slight intensity",
            81: "Rain showers: Moderate intensity",
            82: "Rain showers: Violent intensity",
            85: "Snow showers: Slight intensity",
            86: "Snow showers: Heavy intensity",
            95: "Thunderstorm: Slight or moderate",
            96: "Thunderstorm with slight hail",
            99: "Thunderstorm with heavy hail",
        }
        response_code = 200
        results = {
            "body": f"Temperature is {temperature} in Fahrenheit. The weather description is {weather_code_dict[weathercode]}",
            "response_code": response_code,
        }
        logger.info(f"Weather results: {results}")
        return results
    else:
        response_code = 400
        results = {
            "body": "There is no weather information for this location. Use default value.",
            "response_code": response_code,
        }
        logger.warning(f"No weather data found for {location_name}")
        return results


def get_location_coordinates(location_name):
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
        _, embedding = get_titan_multimodal_embedding(image_path=image_path, text=text)
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


def image_lookup(event, host):
    """
    Perform image lookup based on input image or query.

    Args:
        event (dict): The event object containing input parameters.
        host (str): The host address for the opensearch instance.

    Returns:
        dict: A dictionary with 'body' (image location or error message) and 'response_code'.
    """
    logger.info(f"Image lookup for event: {event}")
    input_image = get_named_parameter(event, "input_image")
    input_query = get_named_parameter(event, "input_query")
    logger.info(f"Input image: {input_image}, Input query: {input_query}")

    if not host:
        logger.warning("No database available for image lookup")
        return {
            "body": "No database available for image look_up, try other actions.",
            "response_code": 404,
        }

    if (input_query != "None") or (input_image != "None"):
        similar_img_b64 = find_similar_image_in_opensearch_index(
            image_path=input_image, text=input_query, k=1
        )
    else:
        # If none of the two possible inputs is provided. Return 404
        logger.warning("No valid inputs provided for image lookup")
        return {
            "body": "No valid inputs provided. Ask user to provide and image or image description",
            "response_code": 404,
        }
    try:
        if similar_img_b64:
            image_data = BytesIO(similar_img_b64[0])

            rand_suffix = randint(0, 1000000)
            file_name = f"lookup_image_{rand_suffix}.jpg"
            output_key = "OutputImages/" + file_name
            output_s3_location = "s3://" + bucket_name + "/" + output_key
            s3_client.upload_fileobj(image_data, bucket_name, output_key)
            response = {"body": output_s3_location, "response_code": 200}
        else:
            response = {"body": "", "response_code": 400}
    except Exception as e:
        logger.error(f"Error in image lookup: {str(e)}")
        response = {
            "body": "Something went wrong",
            "response_code": 400,
        }
    # If the response_code is 400 - return the original input image
    if input_image and (input_image != "None"):
        response["body"] = input_image

    logger.info(f"Image lookup response: {response}")
    return response


def inpaint(event):
    """
    Perform image inpainting based on the provided event parameters.

    Args:
        event (dict): The event object containing inpainting parameters.

    Returns:
        dict: A dictionary with 'body' (output image location or error message) and 'response_code'.
    """
    prompt_text = get_named_parameter(event, "text")
    prompt_mask = get_named_parameter(event, "mask")
    input_image = get_named_parameter(event, "image_location")

    try:
        encoded_image = load_image_from_s3(input_image)
        payload = {
            "taskType": "INPAINTING",
            "inPaintingParams": {
                "text": prompt_text,
                "negativeText": "bad quality, low resolution",  # Optional
                "image": encoded_image,  # Required
                "maskPrompt": prompt_mask,  # One of "maskImage" or "maskPrompt" is required
            },
        }
        result = titan_image(payload)[0]
        if result:
            image_data = BytesIO(result)
            output_key = "OutputImages/" + input_image.split("/")[-1]
            output_s3_location = "s3://" + bucket_name + "/" + output_key
            s3_client.upload_fileobj(image_data, bucket_name, output_key)
    except Exception as e:
        response_code = 400
        results = {
            "body": f"Image cannot be inpainted, please try again: see error {e}",
            "response_code": response_code,
        }
        return results

    response_code = 200
    results = {"body": output_s3_location, "response_code": response_code}
    return results


def outpaint(event):
    """
    Perform image outpainting based on the provided event parameters.

    Args:
        event (dict): The event object containing outpainting parameters.

    Returns:
        dict: A dictionary with 'body' (output image location or error message) and 'response_code'.
    """
    prompt_text = get_named_parameter(event, "text")
    prompt_mask = get_named_parameter(event, "mask")
    input_image = get_named_parameter(event, "image_location")

    try:
        encoded_image = load_image_from_s3(input_image)
        payload = {
            "taskType": "OUTPAINTING",
            "outPaintingParams": {
                "text": prompt_text,  # Required
                "image": encoded_image,  # Required
                "maskPrompt": prompt_mask,  # One of "maskImage" or "maskPrompt" is required
                "outPaintingMode": "PRECISE",  # One of "PRECISE" or "DEFAULT"
            },
        }
        result = titan_image(payload)[0]

        if result:
            image_data = BytesIO(result)
            output_key = "OutputImages/" + input_image.split("/")[-1]
            output_s3_location = "s3://" + bucket_name + "/" + output_key
            s3_client.upload_fileobj(image_data, bucket_name, output_key)

    except Exception as e:
        response_code = 400
        results = {
            "body": f"Image cannot be outpainted, please try again: see error{e}",
            "response_code": response_code,
        }
        return results

    response_code = 200
    results = {"body": output_s3_location, "response_code": response_code}
    return results


def get_image_gen(event):
    """
    Generate an image based on the provided event parameters.

    Args:
        event (dict): The event object containing image generation parameters.

    Returns:
        dict: A dictionary with 'body' (generated image location or error message) and 'response_code'.
    """
    input_query = get_named_parameter(event, "input_query")
    weather = get_named_parameter(event, "weather")

    # Read image from file and encode it as base64 string.

    try:
        if weather == "None":
            prompt = f"{input_query}"
        else:
            prompt = f"{input_query}.Make the clothing suitable for wearing in {weather} weather conditions."

        body = json.dumps(
            {
                "taskType": "TEXT_IMAGE",
                "textToImageParams": {"text": prompt},
                "imageGenerationConfig": {
                    "numberOfImages": 1,
                    "height": 1024,
                    "width": 1024,
                    "cfgScale": 10.0,
                    "seed": 0,
                },
            }
        )

        accept = "application/json"
        content_type = "application/json"
        model_id = "amazon.titan-image-generator-v2:0"

        response = bedrock_client.invoke_model(
            body=body, modelId=model_id, accept=accept, contentType=content_type
        )
        response_body = json.loads(response.get("body").read())

        finish_reason = response_body.get("error")
        if finish_reason is not None:
            raise Exception(f"Image generation error. Error is {finish_reason}")

        base64_image = response_body.get("images")[0]
        base64_bytes = base64_image.encode("ascii")
        image_bytes = base64.b64decode(base64_bytes)

        image_data = BytesIO(image_bytes)

        rand_suffix = randint(0, 1000000)
        file_name = f"gen_image_{rand_suffix}.jpg"
        output_key = "OutputImages/" + file_name
        s3_client.upload_fileobj(image_data, bucket_name, output_key)
    except Exception as e:
        response_code = 400
        results = {
            "body": f"Image cannot be generated, please try again: see error {e}",
            "response_code": response_code,
        }
        return results

    response_code = 200
    output_s3_location = f"s3://{bucket_name}/{output_key}"
    results = {"body": output_s3_location, "response_code": response_code}

    return results


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
        _bucket_name, object_key = image_path.replace("s3://", "").split("/", 1)
        response = s3.get_object(Bucket=_bucket_name, Key=object_key)
        # Read the object's body
        image_content = response["Body"].read()
        # Encode the body in bytes & decode it into a string.
        image_encoded = base64.b64encode(image_content).decode("utf8")

    except Exception as e:
        print(f"Error downloading file from S3: {e}")
        return None

    return image_encoded


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
                input_image = base64.b64encode(image_file.read()).decode("utf8")
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


def titan_image(
    payload: dict,
    num_image: int = 1,
    cfg: float = 10.0,
    seed: int = None,
    modelId: str = "amazon.titan-image-generator-v2",
) -> list:
    """
    Generate images using the Titan Image Generator model.

    Args:
        payload (dict): The input payload for image generation.
        num_image (int): Number of images to generate. Defaults to 1.
        cfg (float): Scale for classifier-free guidance. Defaults to 10.0.
        seed (int): Seed for random number generation. Defaults to None.
        modelId (str): ID of the Titan model to use. Defaults to "amazon.titan-image-generator-v2".

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
    seed = seed if seed is not None else randint(0, 214783647)

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
        modelId=modelId,
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


def lambda_handler(event, context):
    """
    AWS Lambda function handler for bedrock agents.

    Args:
        event (dict): The event dict containing details about the request.
        context (object): Runtime information provided by AWS Lambda.

    Returns:
        dict: A dictionary containing the action response, including 'statusCode', 'headers', and 'body'.
    """
    logger.info(f"Received event: {event}")
    response_code = 200
    action_group = event["actionGroup"]
    api_path = event["apiPath"]

    logger.info(f"Processing action: {action_group}, API path: {api_path}")

    if api_path == "/imageGeneration":
        result = get_image_gen(event)
    elif api_path == "/weather":
        result = get_weather(event)
    elif api_path == "/image_lookup":
        result = image_lookup(event, host)
    elif api_path == "/inpaint":
        result = inpaint(event)
    elif api_path == "/outpaint":
        result = outpaint(event)
    else:
        logger.warning(f"Unknown API path: {api_path}")
        result = {"body": "Unknown API path", "response_code": 400}

    body = result["body"]
    response_code = result["response_code"]

    response_body = {"application/json": {"body": str(body)}}
    action_response = {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action_group,
            "apiPath": api_path,
            "httpMethod": event["httpMethod"],
            "httpStatusCode": response_code,
            "responseBody": response_body,
        },
    }

    logger.info(f"Returning response: {action_response}")
    return action_response
