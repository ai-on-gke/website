---
linkTitle: "LLMs"
title: "NVIDIA NIM for Large Language Models (LLMs) on GKE"
description: "This guide explains how to deploy NVIDIA NIM inference microservices on a Google Kubernetes Engine (GKE) cluster, requiring an NVIDIA AI Enterprise License for access to the models. It details the process of setting up a GKE cluster with GPU-enabled nodes, configuring access to the NVIDIA NGC registry, and deploying a NIM using a Helm chart with persistent storage. Finally, it demonstrates how to test the deployed NIM service by sending a sample prompt and verifying the response, ensuring the inference microservice is functioning correctly."
weight: 30
type: docs
owner:
  - name: "Francisco Cabrera"
    link: "https://github.com/fcabrera23"
tags:
 - Blueprints
 - NVIDIA
 - NIM on GKE
 - LLM
cloudShell:
    enabled: true
    folder: site/content/docs/blueprints/nims-on-gke/nim-llm
    editorFile: _index.md
---
## Before you begin

> [!CAUTION]
> Before you proceed further, ensure you have the NVIDIA AI Enterprise License (NVAIE) to access the NIMs.  To get started, go to [build.nvidia.com](https://build.nvidia.com/explore/discover?signin=true) and provide your company email address

1. Get access to NVIDIA NIMs

2. In the [Google Cloud console](https://console.cloud.google.com), on the project selector page, select or create a new project with [billing enabled](https://cloud.google.com/billing/docs/how-to/verify-billing-enabled#console)

3. Ensure you have the following tools installed on your workstation
   * [gcloud CLI](https://cloud.google.com/sdk/docs/install)
   * [kubectl](https://kubernetes.io/docs/tasks/tools/#kubectl)
   * [git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)
   * [jq](https://jqlang.github.io/jq/)
   * [ngc](https://ngc.nvidia.com/setup)

4. Enable the required APIs
   ```bash
   gcloud services enable \
     container.googleapis.com \
     file.googleapis.com
   ```

## Set up your GKE Cluster

1. Choose your region and set your project and machine variables:
	```bash
	export PROJECT_ID=$(gcloud config get project)
	export REGION=us-central1
	export ZONE=${REGION?}-a
	```	


1. Create a GKE cluster:
	```bash
	gcloud container clusters create nim-demo --location ${REGION?} \
	  --workload-pool ${PROJECT_ID?}.svc.id.goog \
	  --enable-image-streaming \
	  --enable-ip-alias \
	  --node-locations ${ZONE?} \
	  --workload-pool=${PROJECT_ID?}.svc.id.goog \
	  --addons=GcpFilestoreCsiDriver  \
	  --machine-type n2d-standard-4 \
	  --num-nodes 1 --min-nodes 1 --max-nodes 5 \
	  --ephemeral-storage-local-ssd=count=2 \
  	--labels=created-by=ai-on-gke,guide=nim-on-gke
	```

1. Get cluster credentials
   ```bash
   kubectl config set-cluster nim-demo
   ```

1. Create a nodepool
	```bash
	gcloud container node-pools create g2-standard-24 --cluster nim-demo \
  	--accelerator type=nvidia-l4,count=2,gpu-driver-version=latest \
 	--machine-type g2-standard-24 \
 	--ephemeral-storage-local-ssd=count=2 \
 	--enable-image-streaming \
	--num-nodes=1 --min-nodes=1 --max-nodes=2 \
 	--node-locations $REGION-a,$REGION-b --region $REGION
	```

## Set Up Access to NVIDIA NIMs and prepare environment

> [!NOTE]
> If you have not set up NGC, see [NGC Setup](https://ngc.nvidia.com/setup) to get your access key and begin using NGC.

1. Get your NGC_API_KEY from NGC
   ```bash
   export NGC_CLI_API_KEY="<YOUR_API_KEY>"
   ```	

2. As a part of the NGC setup, set your configs
	```bash
	ngc config set
	```

3. Ensure you have access to the repository by listing the models
	```bash
	ngc registry model list
	```

4. Create a Kuberntes namespace
	```bash
	kubectl create namespace nim
	```

## Deploy a PVC to persist the model
> [!NOTE]
> This PVC will [dynamically provision a PV](https://cloud.google.com/kubernetes-engine/docs/concepts/persistent-volumes#dynamic_provisioning) with the necessary storage to persist model weights across replicas of your pods.

1. Create a PVC to persist the model weights - recommended for deployments with more than one (1) replica.  Save the following yaml as `pvc.yaml`.
	```yaml
	apiVersion: v1
	kind: PersistentVolumeClaim
	metadata:
	  name: model-store-pvc
	  namespace: nim
	spec:
	  accessModes:
	    - ReadWriteMany
	  resources:
	    requests:
	      storage: 30Gi
	  storageClassName: standard-rwx
	```

2. Apply PVC
	```bash
	kubectl apply -f pvc.yaml
	```
	
## Deploy the NIM with the generated engine using a Helm chart

1. Clone the nim-deploy repository
	```bash
	git clone https://github.com/NVIDIA/nim-deploy.git
	cd nim-deploy/helm
	```

2. Deploy chart with minimal configurations
	```bash
	helm --namespace nim install demo-nim nim-llm/ --set model.ngcAPIKey=$NGC_CLI_API_KEY --set persistence.enabled=true --set persistence.existingClaim=model-store-pvc
	```

## Test the NIM

>[!NOTE]
> Expect the **demo-nim** deployment to take a few minutes as the Llama3 model downloads.

1. Expose the service
	```bash
	kubectl port-forward --namespace nim services/demo-nim-nim-llm 8000
	```

2. Send a test prompt
	```bash
	curl -X 'POST' \
	  'http://localhost:8000/v1/chat/completions' \
	  -H 'accept: application/json' \
	  -H 'Content-Type: application/json' \
	  -d '{
	  "messages": [
	    {
	      "content": "You are a polite and respectful poet.",
	      "role": "system"
	    },
	    {
	      "content": "Write a limerick about the wonders of GPUs and Kubernetes?",
	      "role": "user"
	    }
	  ],
	  "model": "meta/llama3-8b-instruct",
	  "max_tokens": 256,
	  "top_p": 1,
	  "n": 1,
	  "stream": false,
	  "frequency_penalty": 0.0
	}' | jq '.choices[0].message.content' -
	```

3. Browse the API by navigating to http://localhost:8000/docs

## Clean up

Remove the cluster and deployment by runnign the following command:
```bash
gcloud container clusters delete l4-demo --location ${REGION} 
```
