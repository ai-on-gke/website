---
linkTitle: "Checkpoints"
title: "Creating Inference Checkpoints"
description: "Overviews how to convert your inference checkpoint for various model servers"
weight: 30
type: docs
owner:
  - name: "Francisco Cabrera"
    link: "https://github.com/fcabrera23"
tags:
    - Experimentation
    - Tutorials
    - Inference Servers
draft: false
cloudShell:
    enabled: true
    folder: site/content/docs/tutorials/inference-servers/checkpoints
    editorFile: index.md
---

## Overview
This document outlines the process for converting inference checkpoints for use with various model servers, such as Jetstream with MaxText or Pytorch/XLA backends. The core of this process utilizes the `checkpoint_entrypoint.sh` script, packaged within a Docker container, to handle the specific conversion steps required by different server configurations. The goal is to prepare your trained model checkpoints for efficient deployment and inference serving.

## Checkpoint creation

>[!NOTE]
> The [checkpoint_converter.sh](https://github.com/ai-on-gke/tutorials-and-examples/blob/main/inference-servers/checkpoints/checkpoint_converter.sh) script overviews how to convert your inference checkpoint for various model servers.

1. Clone the [AI-on-GKE/tutorial-and-examples](https://github.com/ai-on-gke/tutorials-and-examples) repository
   ```bash
   git clone https://github.com/ai-on-gke/tutorials-and-examples
   cd tutorials-and-examples/inference-servers/checkpoints
   ```

1. Build the Docker image that contains the conversion script and its dependencies. Tag the image and push it to a container registry (like Google Container Registry - GCR) accessible by your execution environment (e.g., Kubernetes).

   ```bash
   docker build -t inference-checkpoint .
   docker tag inference-checkpoint ${LOCATION}-docker.pkg.dev/${PROJECT_ID}/jetstream/inference-checkpoint:latest
   docker push ${LOCATION}-docker.pkg.dev/${PROJECT_ID}/jetstream/inference-checkpoint:latest
   ```

1. The conversion is typically run as a containerized job, for example, using a [Kubernetes job](https://github.com/ai-on-gke/tutorials-and-examples/blob/main/inference-servers/jetstream/maxtext/single-host-inference/checkpoint-job.yaml). You will need to configure the job to use the `${LOCATION}-docker.pkg.dev/${PROJECT_ID}/jetstream/inference-checkpoint:latest` image and pass the required arguments based on your target inference server and checkpoint details.

    **Jetstream + MaxText**
    ```yaml
    --bucket_name: [string] The GSBucket name to store checkpoints, without gs://.
    --inference_server: [string] The name of the inference server that serves your model. (Optional) (default=jetstream-maxtext)
    --model_path: [string] The model path.
    --model_name: [string] The model name. ex. llama-2, llama-3, gemma.
    --huggingface: [bool] The model is from Hugging Face. (Optional) (default=False)
    --quantize_type: [string] The type of quantization. (Optional)
    --quantize_weights: [bool] The checkpoint is to be quantized. (Optional) (default=False)
    --input_directory: [string] The input directory, likely a GSBucket path.
    --output_directory: [string] The output directory, likely a GSBucket path.
    --meta_url: [string] The url from Meta. (Optional)
    --version: [string] The version of repository. (Optional) (default=main)
    ```

    **Jetstream + Pytorch/XLA**
    ```yaml
    --inference_server: [string] The name of the inference server that serves your model.
    --model_path: [string] The model path.
    --model_name: [string] The model name. ex. llama-2, llama-3, gemma.
    --quantize_weights: [bool] The checkpoint is to be quantized. (Optional) (default=False)
    --quantize_type: [string] The type of quantization. Availabe quantize type: {"int8", "int4"} x {"per_channel", "blockwise"}. (Optional) (default=int8_per_channel)
    --version: [string] The version of repository to override, ex. jetstream-v0.2.2, jetstream-v0.2.3. (Optional) (default=main)
    --input_directory: [string] The input directory, likely a GSBucket path. (Optional)
    --output_directory: [string] The output directory, likely a GSBucket path.
    --huggingface: [bool] The model is from Hugging Face. (Optional) (default=False)
    ```
