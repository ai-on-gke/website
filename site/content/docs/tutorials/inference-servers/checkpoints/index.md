---
linkTitle: "Checkpoints"
title: "Creating Inference Checkpoints"
description: "Overviews how to convert your inference checkpoint for various model servers"
weight: 30
type: docs
tags:
    - Experimentation
    - Tutorials
    - Inference Servers
draft: false
---

## Overview
This document outlines the process for converting inference checkpoints for use with various model servers, such as Jetstream with MaxText or Pytorch/XLA backends. The core of this process utilizes the `checkpoint_entrypoint.sh` script, packaged within a Docker container, to handle the specific conversion steps required by different server configurations. The goal is to prepare your trained model checkpoints for efficient deployment and inference serving.

## Checkpoint creation

>[!NOTE]
> The [checkpoint_entrypoint.sh](https://github.com/ai-on-gke/tutorials-and-examples/blob/main/inference-servers/checkpoints/checkpoint_converter.sh) script overviews how to convert your inference checkpoint for various model servers.

1. Clone the [AI-on-GKE/tutorial-and-examples](https://github.com/ai-on-gke/tutorials-and-examples) repository
   ```bash
   git clone https://github.com/ai-on-gke/tutorials-and-examples
   cd tutorials-and-examples/hugging-face-tgi/
   ```

1. Build the Docker image that contains the conversion script and its dependencies. Tag the image and push it to a container registry (like Google Container Registry - GCR) accessible by your execution environment (e.g., Kubernetes).

   ```bash
   docker build -t inference-checkpoint .
   docker tag inference-checkpoint gcr.io/${PROJECT_ID}/inference-checkpoint:latest
   docker push gcr.io/${PROJECT_ID}/inference-checkpoint:latest
   ```

1. The conversion is typically run as a containerized job, for example, using a [Kubernetes job](https://github.com/ai-on-gke/tutorials-and-examples/blob/main/inference-servers/jetstream/maxtext/single-host-inference/checkpoint-job.yaml). You will need to configure the job to use the `gcr.io/${PROJECT_ID}/inference-checkpoint:latest` image and pass the required arguments based on your target inference server and checkpoint details.

    **Jetstream + MaxText**
    ```yaml
    - -s=INFERENCE_SERVER
    - -b=BUCKET_NAME
    - -m=MODEL_PATH
    - -v=VERSION (Optional)
    ```

    **Jetstream + Pytorch/XLA**
    ```yaml
    - -s=INFERENCE_SERVER
    - -m=MODEL_PATH
    - -n=MODEL_NAME
    - -q=QUANTIZE_WEIGHTS (Optional) (default=False)
    - -t=QUANTIZE_TYPE (Optional) (default=int8_per_channel)
    - -v=VERSION (Optional) (default=jetstream-v0.2.3)
    - -i=INPUT_DIRECTORY (Optional)
    - -o=OUTPUT_DIRECTORY
    - -h=HUGGINGFACE (Optional) (default=False)
    ```

## Argument descriptions:

The following table details the arguments accepted by the [checkpoint_entrypoint.sh](https://github.com/ai-on-gke/tutorials-and-examples/blob/main/inference-servers/checkpoints/checkpoint_converter.sh) script:

| Argument          | Flag | Type   | Description                                                                                                                                      |
| :---------------- | :--- | :----- | :----------------------------------------------------------------------------------------------------------------------------------------------- |
| BUCKET_NAME       | `-b` | `str`  | Google Cloud Storage (GCS) Bucket name, *without* the `gs://` prefix. Required for certain server types like Jetstream+MaxText.                  |
| INFERENCE_SERVER  | `-s` | `str`  | Specifies the target inference server. Examples: `jetstream-maxtext`, `jetstream-pytorch`.                                                       |
| MODEL_PATH        | `-m` | `str`  | Path to the base model checkpoint. The interpretation varies depending on the inference server and whether it's a local path or GCS path.         |
| MODEL_NAME        | `-n` | `str`  | Name of the model architecture (e.g., `llama-2`, `llama-3`, `gemma`). Required for servers like Jetstream+Pytorch/XLA.                            |
| HUGGINGFACE       | `-h` | `bool` | (Optional) Set to `true` if the input checkpoint is from HuggingFace Hub format. Default: `false`.                                              |
| QUANTIZE_WEIGHTS  | `-q` | `str`  | (Optional) Set to `true` to enable weight quantization. Default: `false`.                                                                        |
| QUANTIZE_TYPE     | `-t` | `str`  | (Optional) Specifies the quantization type if `QUANTIZE_WEIGHTS` is true. Available types include combinations like `int8_per_channel`, `int4_blockwise`. Default: `int8_per_channel`. |
| VERSION           | `-v` | `str`  | (Optional) Overrides the default version of the inference server components being used (e.g., `jetstream-v0.2.2`, `jetstream-v0.2.3`).         |
| INPUT_DIRECTORY   | `-i` | `str`  | (Optional) Explicitly specifies the input directory for the checkpoint, often a GCS path (`gs://...`). May override parts of `MODEL_PATH`.         |
| OUTPUT_DIRECTORY  | `-o` | `str`  | Specifies the directory where the converted checkpoint should be saved, often a GCS path (`gs://...`). Required for servers like Jetstream+Pytorch/XLA. |