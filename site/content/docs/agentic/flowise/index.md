---
linkTitle: "Flowise"
title: "Deploy Flowise AI to GKE"
description: "This tutorial will provide instructions on how to deploy and use [FlowiseAI](https://flowiseai.com/) on GKE (Google Kubernetes Engine) to build and operate AI applications using a low-code/no-code approach."
weight: 40
type: docs
owner: 
  - name: "Vlado Djerek"
    link: https://github.com/volatilemolotov
tags:
 - Evaluation
 - Fine-tuning
 - Tutorials
cloudShell:
    enabled: true
    folder: site/content/docs/agentic/flowise
    editorFile: index.md
---

This tutorial will provide instructions on how to deploy and use [FlowiseAI](https://flowiseai.com/) on GKE (Google Kubernetes Engine) to build and operate AI applications using a low-code/no-code approach.

## Overview

This tutorial is designed for developers and platform engineers interested in leveraging FlowiseAI on GKE for building customized LLM flows and AI agents, offloading resource-intensive tasks to a managed cluster. As an example for this tutorial, we will create a multi-agent application that acts as a software development team with a software developer and a code reviewer which are coordinated by a supervisor. For more info, you may want to read the [docs](https://docs.flowiseai.com/using-flowise/agentflowv1/multi-agents).

### What will you learn

1. Provision required infrastructure automatically (using Terraform). The GKE Autopilot cluster is used by default.  
2. Install FlowiseAI on the GKE cluster.  
3. Configure FlowiseAI to build and manage LLM flows.  
4. Build a sample AI application (e.g., a chatbot or agent) using FlowiseAI components.  
5. Serve the resulting AI application from the GKE cluster.

### Filesystem structure

```
├── agentflow.json  # Example Flowise Agentflow.
├── terraform/      # Terraform config that creates required infrastructure.
├───── cloudsql.tf
├───── default_env.tfvars
├───── main.tf
├───── network.tf
├───── outputs.tf
├───── variables.tf
├───── bucket.tf
└───── workload_idenity.tf
```

## Before you begin

1. Ensure you have a GCP project with billing enabled and [enabled the GKE API](https://cloud.google.com/kubernetes-engine/docs/how-to/enable-gkee).  
2. Ensure you have the following tools installed on your workstation  
   * [gcloud CLI](https://cloud.google.com/sdk/docs/install)  
   * [kubectl](https://kubernetes.io/docs/tasks/tools/#kubectl)  
   * [terraform](https://developer.hashicorp.com/terraform/tutorials/aws-get-started/install-cli)  
   * [helm](https://helm.sh/docs/intro/install/)  
     

If you previously installed the gcloud CLI, get the latest version by running:

```bash
gcloud components update
```

Ensure that you are signed in using the gcloud CLI tool. Run the following command:

```bash
gcloud auth application-default login
```

## Infrastructure Setup

### Clone the repository

Clone the repository with our guides and cd to the `flowise/` directory by running these commands:

```bash
git clone https://github.com/ai-on-gke/tutorials-and-examples.git
cd tutorials-and-examples/flowise
```

### Enable Necessary APIs

Enable the APIs required for GKE, Artifact Registry, Cloud Build and Cloud Storage

```bash
gcloud services enable \
    container.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    storage.googleapis.com
```

### Create cluster and other resources

In this section we will use Terraform to automate the creation of infrastructure resources. For more details how it is done please refer to the terraform config in the `terraform/` folder. By default, the configuration provisions an Autopilot GKE cluster, but it can be changed to standard by setting `autopilot_cluster = false`.

It creates the following resources. For more information such as resource names and other details, please refer to the [Terraform config](https://github.com/ai-on-gke/tutorials-and-examples/tree/main/flowise/terraform):

* Service Accounts:  
  1. Cluster IAM Service Account (derives name from a cluster name, e.g. `tf-gke-<cluster name>`) – manages permissions for the GKE cluster.  
  2. Application’s IAM Service Account (default name `flowise-tf` and can be changed in the terraform config) – manages permissions for the deployed application to access:  
     * LLM models data that is stored in a Cloud Storage bucket.

* Cloud Storage Bucket to store data such as LLM model.
* [Artifact registry](https://cloud.google.com/artifact-registry/docs/overview) – stores container images for the application.  
* [CloudSQL](https://cloud.google.com/sql/docs/introduction) instance to store Flowise data. To verify that the data is persisted, you can verify it, for example, in the [CloudSQL Studio](https://cloud.google.com/sql/docs/mysql/manage-data-using-studio) after the tutorial is completed.

1. Go the the terraform directory:

    ```bash
    cd terraform
    ```
   

2. Specify the following values inside the `default_env.tfvars` file (or make a separate copy):  
    `<PROJECT_ID>` – replace with your project id (you can find it in the project settings).  
        
3. Init terraform modules:

    ```bash
    terraform init
    ```

4. Optionally run the `plan` command to view an execution plan:

    ```bash
    terraform plan -var-file=default_env.tfvars
    ```

5. Execute the plan:

    ```bash
    terraform apply -var-file=default_env.tfvars
    ```

    And you should see your resources created:

    ```bash

    Apply complete! Resources: 25 added, 0 changed, 0 destroyed.

    Outputs:

    bucket_name = "flowise-tf"
    cloudsql_database_name = "flowise"
    cloudsql_database_secret_name = "db-secret"
    cloudsql_database_user = "flowise"
    cloudsql_instance_ip = "<ip>"
    gke_cluster_location = "us-central1"
    gke_cluster_name = "flowise-tf"
    k8s_service_account_name = "flowise-tf"
    project_id = "<your project_id>"
    ```

6. Configure your kubectl context:

    ```bash
    gcloud container clusters get-credentials $(terraform output -raw gke_cluster_name) --region $(terraform output -raw gke_cluster_location) --project $(terraform output -raw project_id)
    ```


## Deploy the Ollama to serve LLMs

1. Run this command in order to create a deployment manifest. The command will subsitute required values from the terraform:

```bash
cat <<EOF > ../ollama-deployment.yml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ollama
spec:
  selector:
    matchLabels:
      app: ollama
  template:
    metadata:
      labels:
        app: ollama
      annotations:
        gke-gcsfuse/volumes: 'true'
    spec:
      serviceAccount: $(terraform output -raw k8s_service_account_name)
      nodeSelector:
        cloud.google.com/gke-accelerator: nvidia-l4
      containers:
        - name: ollama
          image: ollama/ollama:latest
          ports:
            - containerPort: 11434
          volumeMounts:
            - name: ollama-data
              mountPath: /root/.ollama/
          resources:
            limits:
              nvidia.com/gpu: 1
      volumes:
        - name: ollama-data
          csi:
            driver: gcsfuse.csi.storage.gke.io
            volumeAttributes:
              bucketName: $(terraform output -raw bucket_name)
              mountOptions: implicit-dirs,only-dir=ollama
---
apiVersion: v1
kind: Service
metadata:
  name: ollama
spec:
  selector:
    app: ollama
  ports:
    - protocol: TCP
      port: 11434
      targetPort: 11434
EOF
```

2. Deploy the created manifest:

```bash
kubectl apply -f ../ollama-deployment.yml
```


3. Wait for Ollama is successfully deployed:


```bash
kubectl rollout status deployment/ollama
```

4. Pull the  `llama3.2` model within Ollama server pod:

```bash
kubectl exec $(kubectl get pod -l app=ollama -o name) -c ollama -- ollama pull llama3.2
```


## Flowise Deployment and Configuration

1. Add Flowise helm repository:

    ```bash
    helm repo add cowboysysop https://cowboysysop.github.io/charts/
    ```

2. Create yml file with values to customize the Flowise helm chart:

    ```bash
    cat <<EOF > ../values.yml
    externalPostgresql:
      enabled: true
      host: $(terraform output -raw cloudsql_instance_ip)
      port: 5432
      username: $(terraform output -raw cloudsql_database_user)
      existingSecret: $(terraform output -raw cloudsql_database_secret_name)
      existingSecretKeyPassword: password
      database: $(terraform output -raw cloudsql_database_name)
      readinessProbe:
        enabled: true
        initialDelaySeconds: 120
    serviceAccount:
      create: false
      name: $(terraform output -raw k8s_service_account_name)
    EOF
    ```

	 

3. Install Flowise helm chart with the values from the file that was created previously

    ```bash
    helm install flowise cowboysysop/flowise -f ../values.yml
    ```

4. Wait the completion of the deployment:

    ```bash
    kubectl rollout status deployment/flowise
    ```

5. Forward port of the Flowise service in order to access its web UI:

    ```bash
    kubectl port-forward svc/flowise 3000:3000
    ```

    In case of errors, try looking at logs:

    ```bash
    kubectl logs -l app.kubernetes.io/name=flowise
    ```

## Trying multi-agent example

### Load the example agentflow

1. Open web UI at [http://localhost:3000](http://localhost:3000)   
2. Create new Agentflow by clicking on the “Add New” button in the “Agentflows” section:

![alt text](1_create_agentflow.png)

3. Click on the `Load Agents` button and select our example Agentflow from the file `tutorials-and-examples/flowise/agentflow.json` in the repository. This is basically a builtin `Software Team` template from the Flowise marketplace but it uses the Ollama Chat Model to access locally hosted LLMs.   
   

![alt text](2_load.png)

4. Our example Agentflow must be loaded now. Save and give it a name by clicking the save button in order to start using it:
![alt text](3_save_agentflow.png)

Note: 
* Use the `Max iterations` parameter in the `Additional Parameters` of the `Supervisor` node in order to prevent infinite loops.

### Use the Agentflow

The `Supervisor` node has to process the initial prompt and make a task for the `Senior Software Engineer` node. When the task is ready, the supervisor has to pass its result to the `Code Reviewer` node and repeat it until the code is approved.

1. Open the chat window ...
   
![alt text](4_open_chat.png)

 ... and expand it for convenience:
 
![alt text](5_expand_chat.png)

2. Enter the propmt. In our example we ask to write a Snake game.

In the beginning the `Supervisor` requests to write the code from the `Senior Software Engineer` worker:
![alt text](6_supervisor_sde.png)

Then the code is passed by the `Supervisor` to the `Code Reviever` worker:
![alt text](7_supervisor_reviewer.png)

Then, after some iterations, the `Code Reviewer` should approve the code:
![alt text](8_approve.png)

### Optional. Use as Embedding or API endpoint

The agentflow can be accessed through API for further automation or embedded into webpage. 

1. Click on API Endpoint:
   
![alt text](9_embed.png)

2. Choose one of the options:

![alt text](10_embed_window.png)


## Cleaning up

1. Destroy the provisioned infrastructure.

    ```bash
    terraform destroy -var-file=default_env.tfvars
    ```

## Troubleshooting

### Some models are unabable to work with agents.

If you change the model, be aware that it may not work properly with the agents and a prompt may just stop without any descriptive messages from the Flowise. In this case you can look at logs of the Flowise:

```bash
 kubectl logs -l app.kubernetes.io/name=flowise
```

or Ollama

```bash
 kubectl logs -l app=ollama
```

