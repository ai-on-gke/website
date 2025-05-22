---
linkTitle: "Hugging Face TGI"
title: "Hugging Face TGI"
description: "This guide demonstrates how to deploy a Hugging Face Text Generation Inference (TGI) server on Google Kubernetes Engine (GKE) using NVIDIA L4 GPUs, enabling you to serve large language models like Mistral-7b-instruct. It walks you through creating a GKE cluster, deploying the TGI application, sending prompts to the model, and monitoring the service's performance using metrics, while also providing instructions for cleaning up the cluster."
weight: 30
owner:
  - name: "Francisco Cabrera"
    link: "https://github.com/fcabrera23"
type: docs
tags: 
    - Orchestration
    - Tutorials
cloudShell:
    enabled: true
    folder: site/content/docs/tutorials/inference-servers/hf-tgi
    editorFile: index.md
---

## Overview
This guide demonstrates how to deploy a Hugging Face Text Generation Inference (TGI) server on Google Kubernetes Engine (GKE) using NVIDIA L4 GPUs, enabling you to serve large language models like Mistral-7b-instruct. It walks you through creating a GKE cluster, deploying the TGI application, sending prompts to the model, and monitoring the service's performance using metrics, while also providing instructions for cleaning up the cluster.

## Prerequisites

Make sure you have:

* A Google Cloud project with billing enabled.
* Google Cloud SDK (gcloud CLI) installed and configured.
* kubectl installed.
* Terraform installed.
* Sufficient Google Cloud quotas for NVIDIA L4 GPUs and G2 machines.

## Installation

1. Clone the [AI-on-GKE/tutorial-and-examples](https://github.com/ai-on-gke/tutorials-and-examples) repository
   ```bash
   git clone https://github.com/ai-on-gke/tutorials-and-examples
   cd tutorials-and-examples/hugging-face-tgi/
   ```

1. Set environment variables
	```bash
	export REGION=us-central1
	export PROJECT_ID=$(gcloud config get project)
	```

1. Create GKE cluster
	```bash
	gcloud container clusters create l4-demo --location ${REGION}   \
	--workload-pool ${PROJECT_ID}.svc.id.goog   --enable-image-streaming \
	--node-locations=$REGION-a --addons GcsFuseCsiDriver  \
	--machine-type n2d-standard-4  \
	--num-nodes 1 --min-nodes 1 --max-nodes 5   \
	--ephemeral-storage-local-ssd=count=2 --enable-ip-alias \
	--labels=created-by=ai-on-gke,guide=hf-tgi
	```

 1. Get cluster credentials
	```bash
	kubectl config set-cluster l4-demo
	```

1. Create GPU node pool
	```bash
	gcloud container node-pools create g2-standard-24 --cluster l4-demo \
	  --accelerator type=nvidia-l4,count=2,gpu-driver-version=latest \
	  --machine-type g2-standard-24 \
	  --ephemeral-storage-local-ssd=count=2 \
	 --enable-image-streaming \
	 --num-nodes=1 --min-nodes=1 --max-nodes=2 \
	 --node-locations $REGION-a,$REGION-b --region $REGION
	 ```

1. Deploy the application using Terraform:
    * Set the `project_id` in `workloads.tfvars`.
    * Apply the Terraform configuration:
        ```bash
        terraform init
        terraform apply -var-file=workloads.tfvars
        ```

1. Check application status
   ```bash
    kubectl logs -l app=mistral-7b-instruct -n l4-demo
    ```
    Look for logs indicating the model has loaded successfully.

1. Set up port forward for testing
   ```bash
   kubectl port-forward deployment/mistral-7b-instruct 8080:8080 -n l4-demo &
   ```

## API Interaction and Service Monitoring
1. Try a few prompts by sending a request to the TGI server using the forwarded port
	```bash
	export USER_PROMPT="How to deploy a container on K8s?"
	```
	```bash
	curl 127.0.0.1:8080/generate -X POST \
	    -H 'Content-Type: application/json' \
	    --data-binary @- <<EOF
	{
	    "inputs": "[INST] <<SYS>>\nYou are a helpful, respectful and honest assistant. Always answer as helpfully as possible, while being safe.  Your answers should not include any harmful, unethical, racist, sexist, toxic, dangerous, or illegal content. Please ensure that your responses are socially unbiased and positive in nature. If a question does not make any sense, or is not factually coherent, explain why instead of answering something not correct. If you don't know the answer to a question, please don't share false information.\n<</SYS>>\n$USER_PROMPT[/INST]",
	    "parameters": {"max_new_tokens": 400}
	}
	EOF
	```
1. Explore the `/metrics` endpoint of the service for performance information
    * Fetch and filter for `tgi_request_count`. This command will retrieve all metrics and then filter the output to show only lines containing tgi_request_count.
      ```bash
      curl -s 127.0.0.1:8080/metrics | grep tgi_request_count
      ```
      Expected Output (similar to):

      ```bash
      # TYPE tgi_request_count counter
      tgi_request_count 1.0
      ```
   * To find `tgi_batch_inference_count` without the progress meter:
     ```bash
     curl -s 127.0.0.1:8080/metrics | grep tgi_batch_inference_count
     ```
     And the output will be similar to:
     ```bash
     # TYPE tgi_batch_inference_count counter
     tgi_batch_inference_count{method="decode"} 292
     tgi_batch_inference_count{method="prefill"} 1
     ```
  
1. Go to cloud monitoring and search for one of those metrics. For example, `tgi_request_count` or `tgi_batch_inference_count`. Those metrics should show up if you search for them in PromQL. 

## Clean up

Remove the cluster and deployment by running the following command:
```bash
gcloud container clusters delete l4-demo --location ${REGION} 
```
