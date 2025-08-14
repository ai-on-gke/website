---
linkTitle: "Llama Guard with Google ADK"
title: "Securing Google ADK application agent with Llama Guard"
description: "This tutorial provides instructions on how to secure a [ADK](https://google.github.io/adk-docs/) agent deployed on [Google Kubernetes Engine (GKE)](https://cloud.google.com/kubernetes-engine/docs/concepts/kubernetes-engine-overview) with [Llama Guard](https://www.llama.com/docs/model-cards-and-prompt-formats/llama-guard-3/)"
weight: 10
type: docs
owner:   
  - name: "Vlado Djerek"
    link: "https://github.com/volatilemolotov"
tags:
 - Evaluation
 - ADK
 - Tutorials
---
## Overview

This tutorial provides instructions on how to secure an [ADK](https://google.github.io/adk-docs/) agent deployed on [Google Kubernetes Engine (GKE)](https://cloud.google.com/kubernetes-engine/docs/concepts/kubernetes-engine-overview) with [Llama Guard](https://www.llama.com/docs/model-cards-and-prompt-formats/llama-guard-3/)

This example uses Google ADK to interact with LLM, but it is more focused on LLama Guard. For more detailed guide in Google ADK, please refer to our another guide - [Agent Development Kit (ADK) on GKE](/docs/agentic/adk-llama-vllm/)

The tutorial will cover:

* Setting up your Google Cloud environment.  
* Building a container image for your agent.  
* Deploying the LLMs via vLLM including the Llama Guard model.  
* Deploying the agent to a GKE cluster.  
* Testing your deployed agent.

## Before you begin

Ensure you have the following tools installed on your workstation  
   * [gcloud CLI](https://cloud.google.com/sdk/docs/install)  
   * [kubectl](https://kubernetes.io/docs/tasks/tools/#kubectl)  
   * [terraform](https://developer.hashicorp.com/terraform/tutorials/aws-get-started/install-cli)

If you previously installed the gcloud CLI, get the latest version by running:

```
gcloud components update
```

Ensure that you are signed in using the gcloud CLI tool. Run the following command:

```
gcloud auth application-default login
```

## Infrastructure Setup

### Clone the repository

Clone the repository with our guides and cd to the Llama Guard tutorial directory by running these commands:

```
git clone https://github.com/ai-on-gke/tutorials-and-examples.git
cd tutorials-and-examples/security/llama-guard/
```

### Filesystem structure

```
```

### Enable Necessary APIs

Enable the APIs required for GKE, Artifact Registry, Cloud Build, and Vertex AI

```
gcloud services enable \
    container.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
```

## Create cluster and other resources

In this section we will use Terraform to automate the creation of infrastructure resources. For more details how it is done please refer to the terraform config in the `terraform/` folder. By default, the configuration provisions an Autopilot GKE cluster, but it can be changed to standard by setting `autopilot_cluster = false`.

It creates the following resources. For more information such as resource names and other details, please refer to the [Terraform config](https://github.com/ai-on-gke/tutorials-and-examples/tree/main/security/model-armor/terraform):

* Service Accounts:
    - Cluster IAM Service Account (derives name from a cluster name, e.g. `tf-gke-<cluster name>`) – manages permissions for the GKE cluster.
* [Artifact registry](https://cloud.google.com/artifact-registry/docs/overview) – stores container images for the application.  
    

1. Go the the terraform directory:

    ```bash
    cd terraform
    ``` 

2. Specify the following values inside the `example.tfvars` file (or make a separate copy):  
   * `<PROJECT_ID>` – replace with your project id (you can find it in the project settings).

    Other values can be changed, if needed, but can be left with default values.

3. Init terraform modules:

    ```bash
    terraform init
    ``` 

4. Optionally run the plan command to view an execution plan:
    
    ```bash
    terraform plan -var-file=example.tfvars
    ```

5. Execute the plan:

    ```bash
    terraform apply -var-file=example.tfvars
    ```

    And you should see your resources created:

    ```
    Apply complete! Resources: 16 added, 0 changed, 0 destroyed.
    
    Outputs:
    
    ```

6. Configure your kubectl context:

    ```bash
    gcloud container clusters get-credentials $(terraform output -raw gke_cluster_name) --region $(terraform output -raw gke_cluster_location)
    ```

## Deploy vLLM Models

We need to deploy vLLM servers that will serve the Llama Guard model and another normal model that the LLama Guard will secure.

1. Apply vLLM deployment manifest with base Llama model:

   ```sh
   kubectl apply ../vllm/vllm-llama.yaml
   ```

2. Apply vLLM deployment manifest with Llama Guard model:

   ```sh
   kubectl apply ../vllm/vllm-llama-guard.yaml
   ```

3. Wait until these deployments are ready:

   ```sh
   kubectl rollout status deployment/vllm-llama3 deployment/vllm-llama-guard-3
   ```

## Deploy and Configure the Agent Application

This application consists of a simple ADK agent that uses [Callbacks](https://google.github.io/adk-docs/callbacks/). It uses [Before Model Callback](https://google.github.io/adk-docs/callbacks/types-of-callbacks/#before-model-callback) and [After Model Callback](https://google.github.io/adk-docs/callbacks/types-of-callbacks/#after-model-callback) to invoke Llama Guard on the user prompt and the model's response respectively. 

For more info you can also look at the [adk-app/secured_agent/agent.py](https://github.com/ai-on-gke/tutorials-and-examples/blob/main/security/llama-guard/adk-app/secured_agent/agent.py) file which has the variable `secured_agent`, which is an instance of the [LlmAgent](https://google.github.io/adk-docs/agents/llm-agents/) class and its two constructor arguments: `before_model_callback` and `after_model_callback`. These callbacks are used to invoke LLama Guard.

You can also look at the file [adk-app/secured_agent/llama_guard.py](https://github.com/ai-on-gke/tutorials-and-examples/blob/main/security/llama-guard/adk-app/secured_agent/llama_guard.py) where the prompt for the LlamaGuard is constructed. More information on prompt and LLama Guard in general can be found in the [official docs](https://www.llama.com/docs/model-cards-and-prompt-formats/llama-guard-3/)

1. Build image with our ADK application:
   ```sh 
   gcloud builds submit \
       --tag $(terraform output -raw image_repository_full_name)/secured-agent:latest \
       --project=$(terraform output -raw project_id) \
       ../adk-app
   ```

2. Deploy the manifest for the ADK application:

   ```sh
   kubectl apply -f ../gen/secured-agent.yaml
   ```

3. Wait until the deployment is ready:

   ```sh
   kubectl rollout status deployment/adk-agent
   ```

## Testing

1. Open our ADK application's URL in the web browser. If you enabled [Identity Aware Proxy from our other guide](/docs/tutorials/security/identity-aware-proxy/), then use its URL. Otherwise, you can just use the port-forward command and open the link [http://127.0.0.1:8000](http://127.0.0.1:8000):

   ```sh
   kubectl port-forward svc/adk-agent 8000:80
   ```

2. The ADK application web UI has to be opened with our `secured_agent` agent being already selected. Try to do some prompting and make a normal prompt:

   ![alt_text](normal-prompt.png) 

3. Then try some malicious prompt:

   ![alt_text](malicious-prompt.png)

   As you can see, the model's response is the same as we specified in the agent's code. 

## Cleaning up

1. Destroy the provisioned infrastructure.

    ```bash
    terraform destroy -var-file=example.tfvars
