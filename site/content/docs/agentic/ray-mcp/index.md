---
linkTitle: "Deploying MCP Servers on GKE"
title: "Deploying MCP Servers on GKE: Building AI Agents with ADK and Ray-Served Models"
description: "This guide provides instructions for deploying a **Ray cluster with the AI Device Kit (ADK)** and a **custom Model Context Protocol (MCP) server** on **Google Kubernetes Engine (GKE)**. It covers setting up the infrastructure with Terraform, containerizing and deploying the Ray Serve application, deploying a custom MCP server for real-time weather data, and finally deploying an ADK agent that utilizes these components. The guide also includes steps for verifying deployments and cleaning up resources."
weight: 30
type: docs
owner:
  - name: "Vlado Djerek"
    link: https://github.com/volatilemolotov

tags:
 - ADK
 - Tutorials
 - MCP
---
## Introduction
This guide shows how to host a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction) server with Server Sent Events (SSE) transport on Google Kubernetes Engine (GKE). MCP is an open protocol that standardizes how AI agents interact with their environment and external data sources. MCP clients can communicate with the MCP servers using two distinct transport mechanisms:
   * [Standard Input/Output (stdio)](https://modelcontextprotocol.io/docs/concepts/transports#standard-input%2Foutput-stdio) - direct process communication
   * [Server-Sent Events (SSE)](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http) or Streamable HTTP - web-based streaming communication

### You have several options for deploying MCP:
1. Local Development: Host both MCP clients and servers on the same local machine

2. Hybrid Setup: Run an MCP client locally and have it communicate with remote MCP servers hosted on a cloud platform like GKE

3. Full Cloud Deployment: Host both MCP clients and servers on a cloud platform.

>[!NOTE]
> While GKE supports hosting MCP servers with stdio transport (through multi-container pods or sidecar patterns), streamable HTTP transport is the recommended approach for Kubernetes deployments. HTTP-based transport aligns better with Kubernetes networking principles, enables independent scaling of components, and provides better observability and debugging capabilities, and offers better security isolation between components.

## Before you begin

Ensure you have the following tools installed on your workstation
   * [gcloud CLI](https://cloud.google.com/sdk/docs/install)
   * [kubectl](https://kubernetes.io/docs/tasks/tools/#kubectl)
   * [terraform](https://developer.hashicorp.com/terraform/tutorials/aws-get-started/install-cli)
   * [Helm](https://helm.sh/docs/intro/install/)

If you previously installed the gcloud CLI, get the latest version by running:

```
gcloud components update
```

Ensure that you are signed in using the gcloud CLI tool. Run the following command:

```
gcloud auth application-default login
```

### MCP server Development
You have two main approaches for implementing an [MCP server](https://modelcontextprotocol.io/introduction):
- **Developing your own MCP server**: We recommend that you use an MCP server SDK to develop your MCP server, such as the [official Python, TypeScript, Java, or Kotlin SDKs](https://modelcontextprotocol.io/) or [FastMCP](https://gofastmcp.com/).
- **Existing MCP servers**: You can find a list of official and community MCP servers on the [MCP servers GitHub repository](https://github.com/modelcontextprotocol/servers#readme). [Docker Hub](https://hub.docker.com) also provides a [curated list of MCP servers](https://hub.docker.com/u/mcp).

## Overview:
In the [Building Agents with Agent Development Kit (ADK) on GKE Autopilot cluster using Self-Hosted LLM](https://gke-ai-labs.dev/docs/agentic/adk-llama-vllm/) tutorial, we successfully built a weather agent. However, the weather agent cannot answer questions such as "What's tomorrow's weather in Seattle" because it lacks access to a live weather data source. In this tutorial, we'll address this limitation by building and deploying a custom MCP server using FastMCP. This server will provide our agent with real-time weather capabilities and will be deployed on GKE. We will continue to use the same LLM backend powered by Ray Serve/vLLM ([per Ray Serve for Self-Hosted LLMs tutorial](https://gke-ai-labs.dev/docs/agentic/ray-serve/)).

Folder structure:
```
tutorials-and-examples/adk/ray-mcp/
├── adk_agent/
│  └── weather_agent/
│  │   ├── __init__.py
│  │   ├── weather_agent.py
│  │   └── deployment_agent.yaml
│  ├── main.py
│  └── requirements.txt
│  └── Dockerfile
│
└── mcp_server/
│  ├── weather_mcp.py
│  └── deployment_weather_mcp.yaml
│  └── Dockerfile
│  └── requirements.txt
│
└── terraform/
    ├── artifact_registry.tf
    └── main.tf
    └── outputs.tf
    └── variables.tf
    └── default_env.tfvars
    └── network.tf
    └── providers.tf
    └── workload_identity.tf
```

## Step 1: Set Up the Infrastructure with Terraform

Start by setting up the GKE cluster, service account, IAM roles, and Artifact Registry using Terraform.

Download the code and navigate to the tutorial directory:

```bash
git clone https://github.com/ai-on-gke/tutorials-and-examples.git
cd tutorials-and-examples/adk/ray-mcp/terraform
```

Set the environment variables, replacing `<PROJECT_ID>` and `<MY_HF_TOKEN>`:

```bash
gcloud config set project <PROJECT_ID>
export PROJECT_ID=$(gcloud config get project)
export REGION=$(terraform output -raw gke_cluster_location)
export HF_TOKEN=<MY_HF_TOKEN>
export CLUSTER_NAME=$(terraform output -raw gke_cluster_name)
```

Update the `<PROJECT_ID>` placeholder in `default_env.tfvars` with your own Google Cloud Project ID Name.

Initialize Terraform, inspect plan and apply the configuration:

```bash
terraform init
terraform plan --var-file=./default_env.tfvars
terraform apply --var-file=./default_env.tfvars
```

Review the plan and type yes to confirm. This will create:

- A GKE Autopilot cluster named `llama-ray-cluster`.
- A service account `adk-ray-agent-sa`.
- An IAM role binding granting the service account `roles/artifactregistry.reader`.
- An Artifact Registry repository `llama-ray`.

Configure `kubectl` to communicate with the cluster:

```bash
gcloud container clusters get-credentials $CLUSTER_NAME --region=$REGION --project $PROJECT_ID
```

## Step 2: Containerize and Deploy the Ray Serve Application

Before deploying our MCP server, we need to set up the Ray Serve application that will power our LLM backend. Follow the [Ray Serve for Self-Hosted LLMs](https://gke-ai-labs.dev/docs/agentic/ray-serve/) tutorial to deploy the LLM backend. Specifically, complete `Step 2: Containerize and Deploy the Ray Serve Application` from that guide. You can access the content of this step by running this command:

```bash
cd tutorials-and-examples/ray-serve/ray-serve-vllm
```

After completing the `Step 2` in Ray Serve tutorial, verify the deployment:

```bash
kubectl get pods | grep ray
kubectl get services | grep ray
```

You should see Ray head and worker pods running, plus Ray services.

Next: Once Ray Serve is running, proceed to Step 3 to deploy our MCP server that will connect to this LLM backend.

## Step 3: Deploy the MCP server

Navigate to MCP Server Directory

```bash
cd tutorials-and-examples/adk/ray-mcp/mcp_server
```

Let's create a new namespace where we deploy our ADK application and MCP Server:

```bash
kubectl create namespace adk-weather-tutorial
```

Build and push the MCP Server container image:

```bash
gcloud builds submit \
    --tag us-docker.pkg.dev/$PROJECT_ID/llama-ray/mcp-server:latest \
    --project=$PROJECT_ID .
```

Update the `<PROJECT_ID>` placeholders in the `./deployment_weather_mcp.yaml` file where applicable. Apply the manifest:
```bash
kubectl apply -f deployment_weather_mcp.yaml
```

### Test with MCP Inspector

Let's validate our MCP server using the official MCP Inspector tool.

Run this command to port-forward the MCP Server:

```bash
kubectl -n adk-weather-tutorial port-forward svc/weather-mcp-server 8000:8080
```

In another terminal session, run this command:
```bash
npx @modelcontextprotocol/inspector@0.14.2
```

Expected output:
```log
Starting MCP inspector...
⚙️ Proxy server listening on 127.0.0.1:6277
🔑 Session token: <SESSION_TOKEN>
Use this token to authenticate requests or set DANGEROUSLY_OMIT_AUTH=true to disable auth

🔗 Open inspector with token pre-filled:
   http://localhost:6274/?MCP_PROXY_AUTH_TOKEN=<SESSION_TOKEN>
   (Auto-open is disabled when authentication is enabled)

🔍 MCP Inspector is up and running at http://127.0.0.1:6274
```

To connect to your MCP Server, you need to do the following:
   * `Transport Type` - choose `SSE`.
   * `URL` - paste `http://127.0.0.1:8000/sse`.
   * `Configuration` -> `Proxy Session Token` - paste `<SESSION_TOKEN>` from the terminal (see example logs above).

Press the `Connect` button, and navigate to the `tools` tab. Here you can click the `List Tools` button and check how these tools work.
![](./image1.png)

Now you can cancel the port-forwarding and close the inspector.

## Step 4: Deploy the ADK Agent

Navigate to the ADK agent directory:

```bash
cd tutorials-and-examples/adk/ray-mcp/adk_agent
```

Build and push the ADK agent container image:

```bash
gcloud builds submit \
    --tag us-docker.pkg.dev/$PROJECT_ID/llama-ray/adk-agent:latest \
    --project=$PROJECT_ID .
```

Update the `./deployment_agent.yaml` file `<PROJECT-ID>` placeholders where applicable. Apply the manifest:

```bash
kubectl apply -f deployment_agent.yaml
```

Verify the deployment:

- Check the pods:

    ```bash
    kubectl -n adk-weather-tutorial get pods
    ```

    You should see five pods: the two Ray pods and the ADK agent pod.
    ```bash
    NAME                                                  READY   STATUS    RESTARTS       AGE
    adk-agent-6c8488db64-hjt86                            1/1     Running   0              61m
    kuberay-operator-bb8d4d9c4-kwjml                      1/1     Running   2 (177m ago)   3h1m
    llama-31-8b-raycluster-v8vj4-gpu-group-worker-ttfp7   1/1     Running   0              162m
    llama-31-8b-raycluster-v8vj4-head-ppt6t               1/1     Running   0              162m
    weather-mcp-server-79748fd6b5-8h4m7                   1/1     Running   0              43m
    ```
- Check the services:

    ```bash
    kubectl -n adk-weather-tutorial get services
    ```

    You should see seven services, including the ADK service.

    ```bash
    NAME                                    TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)                                         AGE
    adk-agent                               ClusterIP   34.118.235.225   <none>        80/TCP                                          64m
    kuberay-operator                        ClusterIP   34.118.236.198   <none>        8080/TCP                                        3h5m
    kubernetes                              ClusterIP   34.118.224.1     <none>        443/TCP                                         3h40m
    llama-31-8b-head-svc                    ClusterIP   None             <none>        10001/TCP,8265/TCP,6379/TCP,8080/TCP,8000/TCP   153m
    llama-31-8b-raycluster-v8vj4-head-svc   ClusterIP   None             <none>        10001/TCP,8265/TCP,6379/TCP,8080/TCP,8000/TCP   165m
    llama-31-8b-serve-svc                   ClusterIP   34.118.233.111   <none>        8000/TCP                                        153m
    weather-mcp-server                      ClusterIP   34.118.239.33    <none>        8080/TCP                                        46m
    ```

- Access your ADK Agent using port-forwarding:

    ```bash
    kubectl -n adk-weather-tutorial port-forward svc/adk-agent 8000:80
    ```

    You should see the following output:
    ```log
    Forwarding from 127.0.0.1:8000 -> 8080
    Forwarding from [::1]:8000 -> 8080
    ```

    Navigate to http://127.0.0.1:8000 and test your agent.
    ![](./image2.png)

## Step 5: Clean Up

Destroy the provisioned infrastructure.

```bash
cd tutorials-and-examples/adk/ray-mcp/terraform
terraform destroy -var-file=default_env.tfvars
```
