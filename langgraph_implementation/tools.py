import base64
import json
import logging
import os
from io import BytesIO
from random import randint

import boto3
import requests
from helpers import (
    find_similar_image_in_opensearch_index,
    get_location_coordinates,
    load_image_from_s3,
    titan_image,
)
from langchain_core.tools import tool

logger = logging.getLogger()
logger.setLevel("INFO")

REQUEST_TIMEOUT = 10

bedrock_client = boto3.client("bedrock-runtime")
s3_client = boto3.client("s3")
bucket_name = os.environ["s3_bucket"]
host = os.environ["aoss_host"]
if host.startswith("https:"):
    host = host.removeprefix("https://")


################## Tools #############################


@tool
def get_weather(location_name: str):
    """
    Finds weather at a given location.It retrieves current weather data for a particular geographical location.

    Args:
        event (dict): The event object containing parameters including latitude and longitude.

    Returns:
        body: A string containing the current weather data.
    """
    logger.info(f"Location name: {location_name}")

    latitude, longitude = get_location_coordinates(location_name)
    if not latitude or not longitude:
        logger.warning(
            f"Could not find location coordinates for {location_name}")
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
        weather_data = response.json()
    else:
        response_code = 400
        results = {
            "body": "There is no weather information for this location. Use default value.",
            "response_code": response_code,
        }
        logger.warning(f"No weather data found for {location_name}")
        return results

    if "weather_data" in locals():
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
        results = f"Temperature is {temperature} in Fahrenheit. The weather description is {weather_code_dict[weathercode]}"
        logger.info(f"Weather results: {results}")
        return results
    else:
        results = "There is no weather information for this location. Use default value."
        logger.warning(f"No weather data found for {location_name}")
        return results


@tool
def get_image_gen(input_query: str, weather: str):
    """
    Given the weather information, generate an image based on user's query and image provided
    Description: This action generates images based on user's query and image provided by the user, the weather parameter is only needed if a location was mentioned in the user query. If weather is needed used the /weather api to get the weather input needed. In other cases where a location is not mentioned by the user query the weather parameter is optional.
    Args:
        input_query (str): This is the part of the user query that describes the details to be included in the image
        weather (str): This parameter is input with the weather output if the /weather api is called. If a location is not mentioned in the user query use the default value of None
    Returns:
        body (str): generated image location or error message
    """
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
            raise Exception(
                f"Image generation error. Error is {finish_reason}")

        base64_image = response_body.get("images")[0]
        base64_bytes = base64_image.encode("ascii")
        image_bytes = base64.b64decode(base64_bytes)

        image_data = BytesIO(image_bytes)

        rand_suffix = randint(0, 1000000)
        file_name = f"gen_image_{rand_suffix}.jpg"
        output_key = "OutputImages/" + file_name
        s3_client.upload_fileobj(image_data, bucket_name, output_key)
    except Exception as e:
        results = f"Image cannot be generated, please try again: see error {e}"
        return results

    results = f"s3://{bucket_name}/{output_key}"
    return results


@tool
def outpaint(prompt_text: str, prompt_mask: str, image_location: str):
    """
    Outpaint the outfit to the desired scene or environment. Perform image outpainting based on the provided event parameters.
    Description: Modify an image by seamlessly extending the region defined by the mask. Outpaint the outfit in the image to the requested scene or environment or background using a text prompt.
    Args:
        prompt_text (str): The prompt describing the desired scene or environment the user wants for the outfit
        prompt_mask (str): The description for mask of the outfit to be outpainted. Mask here means the parts that user doesn't want to change. Just describing the items to be masked, without adding instructions like keeping or unchanging.
        image_location (str): The S3 location URI for user uploaded image
    Returns:
        body: output image location or error message.
    """

    try:
        encoded_image = load_image_from_s3(image_location)
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
            output_key = "OutputImages/" + image_location.split("/")[-1]
            output_s3_location = "s3://" + bucket_name + "/" + output_key
            s3_client.upload_fileobj(image_data, bucket_name, output_key)

    except Exception as e:
        results = f"Image cannot be outpainted, please try again: see error {e}"
        return results

    results = output_s3_location
    return results


@tool
def inpaint(prompt_text: str, prompt_mask: str, image_location: str):
    """
    Inpaint the outfit to the desired style in the same scene.

    Description: Inpainting is defined as an image editing operation where a section of the image called a mask is modified to match the surrounding background in the input image. Inpaint the outfits or clothes in the image to the desired style. The inpaint parameter mask should only contains the outfits/clothes need to be changed, parameter text should contains the desired outfits/clothes design in the generated image.

    Args:
        prompt_text (str): Text prompt to guide inpainting, showing how user wants the new image to be.
        prompt_mask (str): Prompt used for describing where in the current image that the user wants to change. Normally it should be the current outfits or clothes in the image.
        image_location (str): The S3 location URI for user uploaded image

    Returns:
        body: output image location or error message.
    """
    try:
        encoded_image = load_image_from_s3(image_location)
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
            output_key = "OutputImages/" + image_location.split("/")[-1]
            output_s3_location = "s3://" + bucket_name + "/" + output_key
            s3_client.upload_fileobj(image_data, bucket_name, output_key)
    except Exception as e:
        results = f"Image cannot be inpainted, please try again: see error {e}"
        return results

    results = output_s3_location
    return results


@tool
def image_lookup(input_image: str, input_query: str):
    """
    Search for the image in the database/catalog by performing image lookup based on input image or query.
    Description: Search for the input image in the database/catalog & retrieve similar images. If the image location is not provided use the default value of None
    Args:
        input_image (str): S3 location URI of the user uploaded image. This is not a required input. If an input location for the image is not provided, use a default value of None.
        input_query (str): This is the part of the user query that describes the details to be included in the image. This is not a required input. If this not provided used a default value of None
    Returns:
        body : image location or error message
    """
    logger.info(f"Input image: {input_image}, Input query: {input_query}")

    if not host:
        logger.warning("No database available for image lookup")
        return "No database available for image look_up, try other actions."
    if (input_query != "None") or (input_image != "None"):
        similar_img_b64 = find_similar_image_in_opensearch_index(
            image_path=input_image, text=input_query, k=1
        )
    else:
        # If none of the two possible inputs is provided. Return 404
        logger.warning("No valid inputs provided for image lookup")
        return "No valid inputs provided. Ask user to provide and image or image description"
    try:
        if similar_img_b64:
            image_data = BytesIO(similar_img_b64[0])

            rand_suffix = randint(0, 1000000)
            file_name = f"lookup_image_{rand_suffix}.jpg"
            output_key = "OutputImages/" + file_name
            output_s3_location = "s3://" + bucket_name + "/" + output_key
            s3_client.upload_fileobj(image_data, bucket_name, output_key)
            return output_s3_location
        else:
            response = ""
    except Exception as e:
        logger.error(f"Error in image lookup: {str(e)}")
        return "Error in image_lookup. Please take another action.",
    if input_image and (input_image != "None"):
        response = input_image

    logger.info(f"Image lookup response: {response}")
    return response


@tool
def human_input_tool(ai_message) -> str:
    """
    Use this tool when you require human input of feedback from your actions or thoughts.
    Args:
        ai_message (str): the action or thought to which you require feedback.
    Returns:
        str: human input.
    """
    print("############## Human Feedback ###############")

    print("\nInsert your feedback. Press Enter + q to end.\n")
    contents = []
    while True:
        try:
            print(input("Give your input:"))
        except EOFError:
            break

    print("\n############################################")
    return


tools = [get_weather, inpaint, outpaint,
         get_image_gen, image_lookup, human_input_tool]
