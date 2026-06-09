---
linkTitle: "Workload fungibility between GPUs and CPUs with DRA"
title: "Workload fungibility between GPUs and CPUs with DRA"
description: "This tutorial guides you through how to achieve workload fungibility across NVIDIA GPUs and CPUs on Google Kubernetes Engine (GKE) using Dynamic Resource Allocation (DRA) and Device Binding Conditions."
weight: 40
owner:
  - name: "Morten Torkildsen"
    link: "https://github.com/mortent"
type: docs
tags:
 - GPU
 - CPU
 - DRA
 - Fungibility
 - vLLM
draft: false
cloudShell: 
    enabled: true
    folder: site/content/docs/tutorials/dynamic-resource-allocation/gpu-cpu-fungibility
    editorFile: index.md
---

## **Background**

Consider an inference service running vLLM that scales horizontally. Each Pod prefers a GPU for maximum throughput and low latency, but when GPU supply is exhausted — whether due to cluster capacity limits or cloud provider quota — the service should continue scaling by falling back to CPU-based inference rather than failing or remaining indefinitely in a `Pending` state.

Dynamic Resource Allocation (DRA) in Kubernetes introduces two key capabilities that make this possible:
1. **Prioritized Allocation (`firstAvailable`)**: In your `ResourceClaimTemplate`, you can define a prioritized list of resource requests. You can declare an NVIDIA GPU (`gpu.nvidia.com`) as the first choice and exclusive CPUs (`dra.cpu`) as the fallback.
2. **Device Binding Conditions (KEP-5007)**: A GPU inference workload (`vllm/vllm-openai`) and a CPU inference workload (`vllm/vllm-cpu`) require different container images. However, the scheduler's choice between GPU and CPU is only known at scheduling time. By adding a gating device with a binding condition (`image-verified`), Kubelet blocks Pod startup until the `dra-driver-image-configurator` controller observes the allocation result, mutates the Pod's runtime container image to the correct vLLM image, and satisfies the condition.

Let’s get started and explore how to achieve workload fungibility between GPUs and CPUs using DRA.

## **Prepare the Environment**

To set up your environment with Cloud Shell, follow these steps:

1. In the Google Cloud console, click the **Activate Cloud Shell** icon to launch a session in the bottom pane.
2. Set the default environment variables:

```bash
export PROJECT_ID=$(gcloud config get project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
export CLUSTER_NAME=gpu-cpu-fungibility
export LOCATION=us-central1 # Choose a region with NVIDIA L4 GPUs available
export ZONE=us-central1-c # Choose a zone within the region with L4 capacity
export HF_TOKEN=HUGGING_FACE_TOKEN # Replace with your actual Hugging Face token
export CLUSTER_VERSION="1.36.0-gke.100" # Must be 1.36 or later
export NAMESPACE=default
```

## **Create and configure Google Cloud Resources**

### Create a GKE Cluster

Device Binding Conditions (KEP-5007) is a beta feature enabled by default starting in Kubernetes 1.36. Create a GKE cluster running version 1.36 or later:

```bash
gcloud container clusters create $CLUSTER_NAME \
    --location=$LOCATION \
    --cluster-version=$CLUSTER_VERSION \
    --project=$PROJECT_ID \
    --num-nodes=1 \
    --labels=created-by=ai-on-gke,guide=gpu-cpu-fungibility
```

### Create a node pool with a single L4 GPU

We create a node pool with exactly one machine and one NVIDIA L4 GPU. We disable the installation of the standard GPU Device Plugin since we will install the NVIDIA GPU DRA driver.

```bash
gcloud container node-pools create gpu-pool \
    --cluster=${CLUSTER_NAME} \
    --location=${LOCATION} \
    --node-locations=${ZONE} \
    --machine-type="g2-standard-8" \
    --accelerator="type=nvidia-l4,count=1,gpu-driver-version=disabled" \
    --num-nodes=1 \
    --node-labels=gke-no-default-nvidia-gpu-device-plugin=true,nvidia.com/gpu.present=true,cloud.google.com/gke-nvidia-gpu-dra-driver=true \
    --spot
```

### Create a CPU node pool for fallback inference

We create a second node pool with standard CPU capacity for workloads to fall back to when GPU capacity is fully consumed. Ensure the Kubelet CPU Manager policy is set to `none` (default on GKE).

```bash
gcloud container node-pools create cpu-pool \
    --cluster=${CLUSTER_NAME} \
    --location=${LOCATION} \
    --node-locations=${ZONE} \
    --machine-type="n2-standard-16" \
    --num-nodes=1 \
    --node-labels=cloud.google.com/gke-cpu-dra-driver=true \
    --spot
```

### Create Google Artifact Registry repository

Create a Docker artifact repository to store our customized DRA drivers (`dra-driver-noop` and `dra-driver-image-configurator`):

```bash
gcloud artifacts repositories create dra-drivers \
    --repository-format=docker \
    --location=$LOCATION \
    --project=$PROJECT_ID \
    --description="DRA Drivers Repository"

gcloud auth configure-docker ${LOCATION}-docker.pkg.dev

export REPO_URI="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/dra-drivers"
```

## **Configure Kubectl to communicate with your cluster**

To configure kubectl to communicate with your cluster, run the following command:

```bash
gcloud container clusters get-credentials ${CLUSTER_NAME} --location=${LOCATION}
```

## **Create Kubernetes Secret for Hugging Face credentials**

> [!NOTE]
> Make sure you have accepted the model license terms on Hugging Face for the `google/gemma-4-2b-it` model before proceeding. Your Hugging Face token must have access to this model.

To create a Kubernetes Secret that contains the Hugging Face token, run the following command:

```bash
kubectl create secret generic hf-secret --from-literal=hf_api_token=${HF_TOKEN} --namespace=${NAMESPACE}
```

## **Install the NVIDIA GPU driver**

Since we disabled the installation of the GPU Device Plugin at node pool creation time, we need to install the NVIDIA GPU driver manually on the GPU node.

```bash
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml
```

## **Build, Push, and Install the DRA Drivers**

In this tutorial, we deploy three distinct DRA components:
1. **NVIDIA GPU DRA Driver**: Implements allocation for GPUs (`gpu.nvidia.com`).
2. **CPU DRA Driver**: Implements allocation for exclusive CPUs (`dra.cpu`).
3. **Image Configurator Controller & No-Op Driver**: Observes allocation results, mutates container runtime images, and resolves binding conditions (`image-configurator.x-k8s.io`).

### Install the NVIDIA GPU DRA driver

Clone the `dra-driver-nvidia-gpu` repository from the `kubernetes-sigs` GitHub organization and install using its Helm chart:

```bash
git clone https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu.git

helm install dra-driver-nvidia-gpu ./dra-driver-nvidia-gpu/deployment/helm/dra-driver-nvidia-gpu \
    --namespace=kube-system \
    --set 'kubeletPlugin.tolerations[0].operator=Exists' # Needed to run on tainted GPU nodes
```

> [!NOTE]
> The NVIDIA DRA driver chart defaults to pulling driver container images directly from the official Kubernetes container image registry (`registry.k8s.io`).

### Install the CPU DRA driver

Clone the `dra-driver-cpu` repository and install the driver using its official Helm chart:

```bash
git clone https://github.com/kubernetes-sigs/dra-driver-cpu.git

helm install dra-driver-cpu ./dra-driver-cpu/deployment/helm/dra-driver-cpu \
    --namespace=kube-system
```

### Build, Push, and Install the Image Configurator and No-Op Driver

Clone the `dra-drivers` repository containing the `dra-driver-image-configurator` controller and `dra-driver-noop` plugin. Build their container images and push them to your newly created Google Artifact Registry repository:

```bash
git clone https://github.com/gke-labs/dra-drivers.git

# Build and push dra-driver-noop
docker build -t ${REPO_URI}/dra-driver-noop:latest ./dra-drivers/dra-driver-noop
docker push ${REPO_URI}/dra-driver-noop:latest

# Build and push dra-driver-image-configurator
docker build -t ${REPO_URI}/dra-driver-image-configurator:latest ./dra-drivers/dra-driver-image-configurator
docker push ${REPO_URI}/dra-driver-image-configurator:latest
```

Install `dra-driver-noop` as a Kubelet plugin configured to pull from your Artifact Registry:

```bash
helm install dra-driver-noop ./dra-drivers/dra-driver-noop/deployment/helm \
    --namespace=kube-system \
    --set driverNames="image-configurator.x-k8s.io" \
    --set image.repository="${REPO_URI}/dra-driver-noop" \
    --set image.tag="latest"
```

Update the `dra-driver-image-configurator` deployment manifest to point to your Artifact Registry image, then apply:

```bash
sed -i "s|image: .*|image: ${REPO_URI}/dra-driver-image-configurator:latest|g" ./dra-drivers/dra-driver-image-configurator/deploy/daemonset.yaml

kubectl apply -f ./dra-drivers/dra-driver-image-configurator/deploy/daemonset.yaml
kubectl apply -f ./dra-drivers/dra-driver-image-configurator/deploy/deviceclass.yaml
```

### Verify that the drivers are working

Verify that all drivers have initialized successfully and published their respective `ResourceSlice` objects:

```bash
kubectl get pods -A | grep -E 'nvidia-dra-driver|dra-driver-cpu|image-configurator|dra-driver-noop'
```

All driver pods should be in a Running state. Verify published slices for `gpu.nvidia.com`, `dra.cpu`, and `image-configurator.x-k8s.io`:

```bash
kubectl get resourceslices -o yaml
```

## **Create the DRA ResourceClaimTemplate**

To enable dynamic fallback between GPU and CPU, we define a `ResourceClaimTemplate` using the `firstAvailable` prioritized allocation field. 

The scheduler evaluates the requested subrequests in order:
1. `gpu`: Prioritizes allocating an NVIDIA GPU (`gpu.nvidia.com`).
2. `cpu`: Falls back to allocating 8 exclusive CPUs (`dra.cpu`) if a GPU is unavailable.

We also request the `image-config` gating device from `image-configurator.x-k8s.io`. This device injects the `bindingConditions: ["image-verified"]` condition, blocking Pod startup until the container image has been updated.

The `config` section provides opaque `ImageConfig` parameters for each subrequest. When `device/gpu` is selected, the controller mutates the container image to `vllm/vllm-openai:v0.7.2`. When `device/cpu` is selected, the controller mutates the container image to `vllm/vllm-cpu:v0.7.2`.

Inspect the following `claim-template.yaml`:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: gpu-or-cpu
spec:
  spec:
    devices:
      requests:
      - name: device
        firstAvailable:
        - name: gpu
          deviceClassName: gpu.nvidia.com
        - name: cpu
          deviceClassName: dra.cpu
          capacity:
            requests:
              dra.cpu/cpu: "8"
      - name: image-config
        exactly:
          deviceClassName: image-configurator.x-k8s.io
      config:
      - requests: ["device/gpu"]
        opaque:
          driver: image-configurator.x-k8s.io
          parameters:
            apiVersion: image-configurator.x-k8s.io/v1alpha1
            kind: ImageConfig
            containerName: vllm
            image: vllm/vllm-openai:v0.7.2
      - requests: ["device/cpu"]
        opaque:
          driver: image-configurator.x-k8s.io
          parameters:
            apiVersion: image-configurator.x-k8s.io/v1alpha1
            kind: ImageConfig
            containerName: vllm
            image: vllm/vllm-cpu:v0.7.2
```

Apply the manifest:

```bash
kubectl apply -f claim-template.yaml --namespace=${NAMESPACE}
```

## **Deploy the vLLM Workloads**

We deploy a small Gemma 4 model (`google/gemma-4-2b-it`). We will create two Pod instances referencing our `ResourceClaimTemplate`. 

Because our cluster has exactly **one** physical L4 GPU, the first Pod will claim the GPU. When the second Pod is created, the scheduler will detect that the GPU is fully occupied and automatically allocate CPU capacity via the prioritized list.

Notice that both Pods specify a placeholder image (`registry.k8s.io/pause:3.10`). The `dra-driver-image-configurator` controller observes the allocation decision, mutates the Pod image to either `vllm-openai` or `vllm-cpu`, and sets the binding condition status to `True` before execution starts.

Inspect the following `pod-1.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: vllm-1
  labels:
    app: vllm
spec:
  tolerations:
  - key: "nvidia.com/gpu"
    operator: "Exists"
    effect: "NoSchedule"
  resourceClaims:
  - name: device
    resourceClaimTemplateName: gpu-or-cpu
  containers:
  - name: vllm
    image: registry.k8s.io/pause:3.10 # Will be mutated by the controller
    command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
    args:
    - --host=0.0.0.0
    - --port=8000
    - --model=google/gemma-4-2b-it
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
      - name: device
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
```

Create a second identical pod manifest named `pod-2.yaml` (with `metadata.name: vllm-2`).

### 1. Deploy the first Pod (Allocated to GPU)

Apply the first Pod manifest:

```bash
kubectl apply -f pod-1.yaml --namespace=${NAMESPACE}
```

Verify that `vllm-1` is successfully allocated the GPU (`gpu.nvidia.com`):

```bash
kubectl get resourceclaim vllm-1-device -o jsonpath='{.status.allocation.results}'
```

Confirm that the container image has been mutated from `pause` to `vllm/vllm-openai:v0.7.2`:

```bash
kubectl get pod vllm-1 -o jsonpath='{.spec.containers[0].image}'
```

### 2. Deploy the second Pod (Fallback to CPU)

Apply the second Pod manifest:

```bash
kubectl apply -f pod-2.yaml --namespace=${NAMESPACE}
```

Because the single physical GPU is occupied by `vllm-1`, the scheduler automatically falls back to allocating CPUs from `dra.cpu`.

Verify that `vllm-2` was allocated CPU capacity:

```bash
kubectl get resourceclaim vllm-2-device -o jsonpath='{.status.allocation.results}'
```

Confirm that `vllm-2`'s container image has been successfully mutated to `vllm/vllm-cpu:v0.7.2`:

```bash
kubectl get pod vllm-2 -o jsonpath='{.spec.containers[0].image}'
```

View the logs from both running model servers:

```bash
kubectl logs pod/vllm-1 -f
kubectl logs pod/vllm-2 -f
```

You should see startup logs indicating successful initialization on GPU and CPU respectively.

## **Generate traffic to the model**

We will verify both model servers respond correctly by sending test completions requests.

First, test the GPU-allocated pod (`vllm-1`):

```bash
kubectl port-forward pod/vllm-1 8000:8000 &
curl http://localhost:8000/v1/completions \
-H "Content-Type: application/json" \
-d '{
    "model": "google/gemma-4-2b-it",
    "prompt": "Write a story about San Francisco",
    "max_tokens": 100,
    "temperature": 0
}'
```

Next, test the CPU-fallback pod (`vllm-2`):

```bash
kubectl port-forward pod/vllm-2 8001:8000 &
curl http://localhost:8001/v1/completions \
-H "Content-Type: application/json" \
-d '{
    "model": "google/gemma-4-2b-it",
    "prompt": "Write a story about San Francisco",
    "max_tokens": 100,
    "temperature": 0
}'
```

The output from both instances will return a HTTP 200 OK response with the generated text.

## **Understanding the DRA Benefit**

This tutorial demonstrated the powerful combination of Dynamic Resource Allocation (DRA) prioritized allocation (`firstAvailable`) and Device Binding Conditions (KEP-5007).

In traditional Kubernetes environments, an AI service requiring different container images for GPU execution (`vllm-openai`) and CPU execution (`vllm-cpu`) could not seamlessly fall back across hardware architectures. If GPU supply ran out, scaling would stall or fail entirely.

With DRA and the `dra-driver-image-configurator`, your application can prioritize high-performance GPU execution while maintaining 100% horizontal elasticity through CPU fallbacks. The scheduler determines which compute device is available, and the binding condition ensures the correct specialized container image is configured on the fly before container execution begins.

## **Clean up** 

To avoid incurring charges to your Google Cloud account for the resources that you created in this guide, run the following command to delete the cluster:

```bash
gcloud container clusters delete ${CLUSTER_NAME} \
  --location=${LOCATION}
```
