# Fashion Assistant agent
This project is a fashion assistant agent built with Amazon Titan models and LangGraph, aimed at providing users with personalized fashion advice and experience.

## Features

- **Image-to-Image or Text-to-Image Search**: Allows users to search for products from the catalog that are similar to styles they like.
- **Text-to-Image Generation**: If the desired style is not available in the database, it can generate customized images based on the user's query.
- **Weather API Integration**: By fetching weather information from the location mentioned in the user's prompt, the agent can suggest appropriate outfits for the occasion.
- **Outpainting**: Users can upload an image and request to change the background, allowing them to visualize their preferred styles in different settings.
- **Inpainting**: Enables users to modify specific clothing items in an uploaded image, such as changing the design or color.

## Flow Chart
![Flow Chart](images/flowchart_agent.png)


## Prerequisites

- An active AWS account and IAM role (with permissions for Bedrock and S3)
- Access to Anthropic Claude-3 Sonnet, Amazon Titan Image Generator, and Titan Multi-modal Embedding models enabled
- Prepare required datasets 
- Install required Python libraries 

## Implementation

When workshop environment is deployed, a Amazon Opensearch Serverless collection is created to store the vector embeddings of your fashion image dataset.

#### Step 1. Data Ingestion

This notebook aims to download the prepared dataset and embed images with Titan Multimodal Embeddings, then ingest the vector representations into AOSS collection

#### Step 2. LangGraph Agent Interaction

This notebook creates the LangGraph agent. You will interact with this agent with your fashino requests.


#### STEP 3. Cleanup

To avoid unnecessary costs, make sure to delete the resources used in this solution using this notebook.
