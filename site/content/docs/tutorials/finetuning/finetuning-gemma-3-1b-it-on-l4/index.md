---
linkTitle: "Fine-tuning Gemma 3-1B-it on L4"
title: "Fine-tuning Gemma 3-1B-it on L4"
description: "This tutorial guides you through fine-tuning the Gemma 3-1B-it language model on Google Kubernetes Engine (GKE) using L4 GPU, leveraging Parameter Efficient Fine Tuning (PEFT) and LoRA. It covers setting up a GKE cluster, containerizing the fine-tuning code, running the fine-tuning job, and uploading the resulting model to Hugging Face. Finally, it demonstrates how to deploy and interact with the fine-tuned model using vLLM on GKE."
weight: 30
owner:
  - name: "Francisco Cabrera"
    link: "https://github.com/fcabrera23"
type: docs
tags:
 - Experimentation
 - Tutorials
 - Gemma
 - Fine-tuning
draft: false
cloudShell: 
    enabled: true
    folder: site/content/docs/tutorials/finetuning/finetuning-gemma-3-1b-it-on-l4
    editorFile: index.md
---
We’ll walk through fine-tuning a Gemma 3-1B-it model using GKE using L4 GPU. L4 GPU are suitable for many use cases beyond serving models. We will demonstrate how the L4 GPU is a great option for fine tuning LLMs such as Gemma 3, at a fraction of the cost of using a higher end GPU.

Let’s get started and fine-tune Gemma 3-1B-it on the [b-mc2/sql-create-context](https://huggingface.co/datasets/b-mc2/sql-create-context) dataset using GKE.
Parameter Efficient Fine Tuning (PEFT) and LoRA is used so fine-tuning is posible
on GPUs with less GPU memory.

As part of this tutorial, you will get to do the following:

1. Prepare your environment with a GKE cluster in
    Autopilot mode.
2. Create a fine-tuning container.
3. Use GPU to fine-tune the Gemma 3-1B-it model and upload the model to huggingface.

## Prerequisites

* A terminal with `kubectl` and `gcloud` installed. Cloud Shell works great!
* Create a [Hugging Face](https://huggingface.co/) account, if you don't already have one.
* Ensure your project has sufficient quota for GPUs. To learn more, see [About GPUs](https://cloud.google.com/kubernetes-engine/docs/concepts/gpus#gpu_quota) and [Allocation quotas](https://cloud.google.com/compute/resource-usage#gpu_quota).
* To get access to the Gemma models for deployment to GKE, you must first sign the license consent agreement then generate a Hugging Face access token. Make sure the token has `Write` permission.

## Creating the GKE cluster with L4 nodepool

Let’s start by setting a few environment variables that will be used throughout this post. You should modify these variables to meet your environment and needs.

Download the code and files used throughout the tutorial:

```bash
git clone https://github.com/ai-on-gke/tutorials-and-examples.git
cd tutorials-and-examples/finetuning-gemma-3-1b-it-on-l4
```

Run the following commands to set the env variables and make sure to replace `<my-project-id>`:

```bash
gcloud config set project <my-project-id>
export PROJECT_ID=$(gcloud config get project)
export REGION=us-central1
export HF_TOKEN=<YOUR_HF_TOKEN>
export CLUSTER_NAME=finetune-gemma
```

> Note: You might have to rerun the export commands if for some reason you reset your shell and the variables are no longer set. This can happen for example when your Cloud Shell disconnects.

Create the GKE cluster by running:

```bash
gcloud container clusters create-auto ${CLUSTER_NAME} \
  --project=${PROJECT_ID} \
  --region=${REGION} \
  --release-channel=rapid \
  --labels=created-by=ai-on-gke,guide=finetuning-gemma-3-1b-it-on-l4
```

### Create a Kubernetes secret for Hugging Face credentials

In your shell session, do the following:

  1. Configure `kubectl` to communicate with your cluster:

      ```sh
      gcloud container clusters get-credentials ${CLUSTER_NAME} --location=${REGION}
      ```

  2. Create a Kubernetes Secret that contains the Hugging Face token:

      ```sh
      kubectl create secret generic hf-secret \
        --from-literal=hf_api_token=${HF_TOKEN} \
        --dry-run=client -o yaml | kubectl apply -f -
      ```

### Containerize the Code with Docker and Cloud Build

1. Create an Artifact Registry Docker Repository

    ```sh
    gcloud artifacts repositories create gemma \
        --project=${PROJECT_ID} \
        --repository-format=docker \
        --location=us \
        --description="Gemma Repo"
    ```

2. Execute the build and create inference container image.

    ```sh
    gcloud builds submit .
    ```

## Run Gemma 3 Fine-tuning Job on GKE

1. Open the `finetune.yaml` manifest.
2. Edit the `<IMAGE_URL>` name with the container image built with Cloud Build and `NEW_MODEL` environment variable value. This `NEW_MODEL` will be the name of the model you would save as a public model in your Hugging Face account.
3. Run the following command to create the fine-tuning job:

    ```sh
    kubectl apply -f finetune.yaml
    ```

4. Monitor the job by running:

    ```sh
    watch kubectl get pods
    ```

5. You can check the logs of the job by running:

    ```sh
    kubectl logs -f -l app=gemma-finetune
    ```

6. Once the job is completed, you can check the model in Hugging Face.

## Serve the Fine-tuned Gemma 3 Model on Google Kubernetes Engine (GKE)

To deploy the fine-tuned Gemma 3 model on GKE you can follow the instructions from Deploy a pre-trained Gemma-3 model on  [vLLM](https://cloud.google.com/kubernetes-engine/docs/tutorials/serve-gemma-gpu-vllm#deploy-vllm). Select the `Gemma 3 1B-it` instruction and change the `MODEL_ID` to `<YOUR_HUGGING_FACE_PROFILE>/gemma-3-1b-it-sql-finetuned`.

### Set up port forwarding

Once the model is deploye, run the following command to set up port forwarding to the model:

```sh
kubectl port-forward service/llm-service 8000:8000
```

The output is similar to the following:

```sh
Forwarding from 127.0.0.1:8000 -> 8000
```

### Interact with the Gemma 3 model using curl

Once the model is deployed In a new terminal session, use curl to chat with your model:

> The following example command is for vLLM.

```sh
curl http://127.0.0.1:8000/v1/chat/completions \
-X POST \
-H "Content-Type: application/json" \
-d '{
    "model": "google/gemma-3-1b-it",
    "messages": [
        {
          "role": "user",
          "content": "Question: What is the total number of attendees with age over 30 at kubecon eu? Context: CREATE TABLE attendees (name VARCHAR, age INTEGER, kubecon VARCHAR)"
        }
    ]
}'
```

The following output shows an example of the model response:

```sh
{
  "id": "chatcmpl-5cc07394271a4183820c62199e84c7db",
  "object": "chat.completion",
  "created": 1744811735,
  "model": "google/gemma-3-1b-it",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "reasoning_content": null,
        "content": "\`\`\`sql\nSELECT COUNT(age) FROM attendees WHERE age > 30 AND kubecon = 'kubecon eu'\n\`\`\`",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": 106
    }
  ],
  "usage": {
    "prompt_tokens": 45,
    "total_tokens": 73,
    "completion_tokens": 28,
    "prompt_tokens_details": null
  },
  "prompt_logprobs": null
}
```

## Clean Up

To avoid incurring charges to your Google Cloud account for the resources used in this tutorial, either delete the project that contains the resources, or keep the project and delete the individual resources.

### Delete the deployed resources

To avoid incurring charges to your Google Cloud account for the resources that you created in this guide, run the following command:

```sh
gcloud container clusters delete ${CLUSTER_NAME} \
  --region=${REGION}
```
