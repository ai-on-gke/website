---
linkTitle: "Device sharing of GPUs with DRA using MIG"
title: "Device sharing of GPUs with DRA using MIG"
description: "This tutorial guides you through how to do device sharing of NVIDIA GPUs with Dynamic Resource Allocation on Google Kubernetes Engine (GKE) using Multi-Instance GPU (MIG) mode."
weight: 35
owner:
  - name: "Morten Torkildsen"
    link: "https://github.com/mortent"
type: docs
tags:
 - GPU
 - Device Sharing
 - DRA
 - MIG
draft: false
cloudShell: 
    enabled: true
    folder: site/content/docs/tutorials/dynamic-resource-allocation/gpu-dynamic-mig
    editorFile: index.md
---

## **Background**

This tutorial guides you through how to do device sharing of NVIDIA GPUs with Dynamic Resource Allocation on Google Kubernetes Engine (GKE). This guide covers Multi-Instance GPU (MIG), with separate guides covering the other device sharing modes, time slicing and Multi-Process Service (MPS).

{{% include "/site/content/docs/tutorials/dynamic-resource-allocation/gpu-sharing-common.md" %}}

Let’s get started and explore how to share GPUs with MIG.

## **Prepare the Environment**

To set up your environment with Cloud Shell, follow these steps:

1. In the Google Cloud console, click the **Activate Cloud Shell** icon to launch a session in the bottom pane.
2. Set the default environment variables:

```bash
export PROJECT_ID=$(gcloud config get project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
export CLUSTER_NAME=gpu-vllm-dynamicmig
export LOCATION=us-east5 # Choose a region that has NVIDIA H100 GPUs available
export ZONE=us-east5-a # Choose a zone within the region that has H100 GPUs available.
export HF_TOKEN=HUGGING_FACE_TOKEN # Replace with your actual Hugging Face token
export CLUSTER_VERSION="1.36" # Must be 1.36 or later, or 1.34+ with alpha features enabled.
export NAMESPACE=default
```

## **Create and configure Google Cloud Resources**

### Create a GKE Cluster

```bash
gcloud container clusters create $CLUSTER_NAME \
    --location=$LOCATION \
    --cluster-version=$CLUSTER_VERSION \
    --project=$PROJECT_ID \
    --num-nodes=1 \
    --labels=created-by=ai-on-gke,guide=gpu-mig-sharing
```

### Create a node pool with H100 GPUs

This creates a node pool with a single machine with a single H100 GPU. We disable installation of the default GPU Device Plugin since we will be installing the NVIDIA GPU DRA driver instead.

```bash
gcloud container node-pools create h100-pool \
    --cluster=${CLUSTER_NAME} \
    --location=${LOCATION} \
    --node-locations=${ZONE} \
    --machine-type="a3-highgpu-1g" \
    --accelerator="type=nvidia-h100-80gb,count=1,gpu-driver-version=disabled" \
    --num-nodes=1 \
    --node-labels=gke-no-default-nvidia-gpu-device-plugin=true,nvidia.com/gpu.present=true,cloud.google.com/gke-nvidia-gpu-dra-driver=true \
    --spot
```

> [!NOTE]
> We use `a3-highgpu-1g` which provides 1 NVIDIA H100 GPU. This machine type is in high demand, which is why we use the `--spot` flag in the node pool creation command.

## **Configure Kubectl to communicate with your cluster**

```bash
gcloud container clusters get-credentials ${CLUSTER_NAME} --location=${LOCATION}
```

## **Create Kubernetes Secret for Hugging Face credentials**

> [!NOTE]
> Make sure you have accepted the model license terms on Hugging Face for the `google/gemma-3-1b-it` model before proceeding. Your Hugging Face token must have access to this model.

To create a Kubernetes Secret that contains the Hugging Face token, run the following command:

```bash
kubectl create secret generic hf-secret --from-literal=hf_api_token=${HF_TOKEN} --namespace=${NAMESPACE}
```

## **Install the NVIDIA GPU driver**

```bash
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml
```

## **Install the NVIDIA GPU DRA driver**

We install the NVIDIA GPU DRA driver using a Helm chart from the official Kubernetes OCI registry. We enable Dynamic MIG support by setting `featureGates.DynamicMIG=true`.

```bash
helm install dra-driver-nvidia-gpu oci://registry.k8s.io/dra-driver-nvidia/charts/dra-driver-nvidia-gpu \
    --version="0.4.0" --create-namespace --namespace=dra-driver-nvidia-gpu \
    --set nvidiaDriverRoot="/home/kubernetes/bin/nvidia/" \
    --set gpuResourcesEnabledOverride=true \
    --set resources.computeDomains.enabled=false \
    --set featureGates.DynamicMIG=true \
    --set kubeletPlugin.priorityClassName="" \
    --set 'kubeletPlugin.tolerations[0].operator=Exists' # Needed to ensure the driver can run on tainted GPU nodes
```

### Verify that the NVIDIA GPU DRA driver is working

Check that the NVIDIA GPU DRA driver is installed and working by inspecting the driver pod:

```bash
kubectl -n dra-driver-nvidia-gpu get pods
```

The pod should be in a Running state. If not, you can inspect the logs with:

```bash
kubectl -n dra-driver-nvidia-gpu logs -l app.kubernetes.io/name=dra-driver-nvidia-gpu -c gpus
```

Verify that the driver has published a ResourceSlice object that lists the GPU on the node:

> [!NOTE]
> It might take a minute or two for the driver to fully initialize and publish the `ResourceSlice` after installation.

```bash
kubectl get resourceslices
```

You should see two resourceslices.


## **Create the DRA ResourceClaimTemplate**

For MIG, each pod needs its own isolated partition. To demonstrate a mixed workload scenario, we will use two distinct `ResourceClaimTemplate`s: one for production requesting a larger MIG profile (`3g.40gb`), and one for development requesting a smaller profile (`1g.10gb`).

Inspect the following `claim-template.yaml`.

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: gpu-claim-template-prod
spec:
  spec:
    devices:
      requests:
      - name: gpu
        exactly:
          deviceClassName: mig.nvidia.com
          selectors:
          - cel:
              expression: |
                device.attributes["gpu.nvidia.com"].profile == "3g.40gb"
---
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: gpu-claim-template-dev
spec:
  spec:
    devices:
      requests:
      - name: gpu
        exactly:
          deviceClassName: mig.nvidia.com
          selectors:
          - cel:
              expression: |
                device.attributes["gpu.nvidia.com"].profile == "1g.10gb"
```

Apply the manifest:

```bash
kubectl apply -f claim-template.yaml --namespace=${NAMESPACE}
```

## **Deploy the vllm workload**

We are using the Gemma 3 models as they are smaller and therefore let us run the tutorial using hardware (i.e., GPUs) that are more easily available than what would be needed to run larger foundation models.

We create two separate Deployments to represent our mixed workloads. The `vllm-prod` deployment uses the larger production claim template. The `vllm-dev` deployment uses the smaller development claim template and adds `--max-model-len=2048` to fit within the smaller memory profile. We also create a LoadBalancer Service for each.

Notice the new DRA syntax in the Pod spec: `spec.resourceClaims` defines the claims or claim templates to be used, and `resources.claims` in the container spec links the container to those defined claims.

Inspect the following `vllm.yaml`.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-prod
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-prod
  template:
    metadata:
      labels:
        app: vllm-prod
    spec:
      tolerations:
      - key: "nvidia.com/gpu"
        operator: "Exists"
        effect: "NoSchedule"
      resourceClaims:
      - name: gpu
        resourceClaimTemplateName: gpu-claim-template-prod
      containers:
      - name: vllm-gpu
        image: vllm/vllm-openai:v0.21.0
        command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
        args:
        - --host=0.0.0.0
        - --port=8000
        - --model=google/gemma-3-1b-it
        env: 
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef:
              name: hf-secret
              key: hf_api_token
        ports:
        - containerPort: 8000
        resources:
          claims:
          - name: gpu
        readinessProbe:
          tcpSocket:
            port: 8000
          initialDelaySeconds: 15
          periodSeconds: 10
        volumeMounts:
        - name: dshm
          mountPath: /dev/shm
      volumes:
      - name: dshm
        emptyDir:
          medium: Memory
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-prod-service
spec:
  selector:
    app: vllm-prod
  type: LoadBalancer
  ports:
    - name: http
      protocol: TCP
      port: 8000
      targetPort: 8000
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-dev
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-dev
  template:
    metadata:
      labels:
        app: vllm-dev
    spec:
      tolerations:
      - key: "nvidia.com/gpu"
        operator: "Exists"
        effect: "NoSchedule"
      resourceClaims:
      - name: gpu
        resourceClaimTemplateName: gpu-claim-template-dev
      containers:
      - name: vllm-gpu
        image: vllm/vllm-openai:v0.21.0
        command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
        args:
        - --host=0.0.0.0
        - --port=8000
        - --model=google/gemma-3-1b-it
        - --max-model-len=2048
        env: 
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef:
              name: hf-secret
              key: hf_api_token
        ports:
        - containerPort: 8000
        resources:
          claims:
          - name: gpu
        readinessProbe:
          tcpSocket:
            port: 8000
          initialDelaySeconds: 15
          periodSeconds: 10
        volumeMounts:
        - name: dshm
          mountPath: /dev/shm
      volumes:
      - name: dshm
        emptyDir:
          medium: Memory
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-dev-service
spec:
  selector:
    app: vllm-dev
  type: LoadBalancer
  ports:
    - name: http
      protocol: TCP
      port: 8000
      targetPort: 8000
```

Apply the manifest:

```bash
kubectl apply -f vllm.yaml --namespace=${NAMESPACE}
```

## **Generate traffic to the model**

Send requests to the model servers and verify that we are getting responses from both workloads.

> [!NOTE]
> Provisioning the external IP for the LoadBalancer services may take a few minutes. If the `export` commands fail or return empty, wait a moment and try again.

```bash
export vllm_prod_service=$(kubectl get service vllm-prod-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}' -n ${NAMESPACE})
export vllm_dev_service=$(kubectl get service vllm-dev-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}' -n ${NAMESPACE})

echo "Sending request to Production service..."
curl http://$vllm_prod_service:8000/v1/completions \
-H "Content-Type: application/json" \
-d '{
    "model": "google/gemma-3-1b-it",
    "prompt": "Write a story about san francisco",
    "max_tokens": 100,
    "temperature": 0
}'

echo "Sending request to Development service..."
curl http://$vllm_dev_service:8000/v1/completions \
-H "Content-Type: application/json" \
-d '{
    "model": "google/gemma-3-1b-it",
    "prompt": "Write a story about san francisco",
    "max_tokens": 100,
    "temperature": 0
}'
```

## **Understanding the DRA Benefit**

This tutorial demonstrated one of the key benefits of using Dynamic Resource Allocation (DRA) for GPU sharing. With the traditional Device Plugin, you would have had to provision a node pool statically partitioned into specific MIG profiles. 

With DRA, we were able to run a mixed workload—a production deployment needing a `3g.40gb` profile and a development deployment needing a `1g.10gb` profile—on the **same** physical H100 GPU without any node-level reconfiguration. Kubernetes and the NVIDIA DRA driver dynamically partitioned the GPU on the fly to fulfill the `ResourceClaimTemplate` requests defined in the workload manifests. This provides significant flexibility, maximizes hardware utilization, and allows developers to request exactly what they need at the application level.

## **Clean up** 

To avoid incurring charges to your Google Cloud account for the resources that you created in this guide, run the following command to delete the cluster:

```bash
gcloud container clusters delete ${CLUSTER_NAME} \
  --location=${LOCATION}
```
