---
linkTitle: "Workload fungibility between GPUs and CPUs with DRA"
title: "Workload fungibility between GPUs and CPUs with DRA"
description: "This tutorial guides you through how to achieve workload fungibility across NVIDIA GPUs and CPUs on Google Kubernetes Engine (GKE) using Dynamic Resource Allocation (DRA), Custom Compute Classes (CCC), and Device Binding Conditions."
weight: 40
owner:
  - name: "John Belamaric"
    link: "https://github.com/johnbelamaric"
  - name: "Tsubasa Watanabe"
    link: "https://github.com/ttsuuubasa"
type: docs
tags:
 - GPU
 - CPU
 - DRA
 - Fungibility
 - vLLM
 - Custom Compute Class
draft: false
cloudShell: 
    enabled: true
    folder: site/content/docs/tutorials/dynamic-resource-allocation/gpu-cpu-fungibility
    editorFile: index.md
---

## **Background**

Consider an inference service running vLLM that scales horizontally. Each Pod prefers a GPU for maximum throughput and low latency, but when GPU supply is exhausted — whether due to cluster capacity limits, node pool size limits, or cloud provider quota — the service should continue scaling by falling back to CPU-based inference rather than failing or remaining indefinitely in a `Pending` state.

Achieving this level of workload fungibility requires coordinating three distinct layers:
1. **Infrastructure Autoscaling with Custom Compute Classes (CCC)**: In GKE, Custom Compute Classes allow you to declare a ranked list of node pool priorities for **node autoscaling**. When pending Pods require nodes, Cluster Autoscaler scales the first node pool (`gpu-pool`) until it reaches its limits (such as `max-nodes` or quota constraints), and then falls back to scaling the next node pool (`cpu-pool`).
2. **Prioritized Device Allocation and Pod Scoring with DRA (`firstAvailable`)**: In your `ResourceClaimTemplate`, you define a prioritized list of resource requests: NVIDIA GPU (`gpu.nvidia.com`) first, and exclusive CPUs (`dra.cpu`) second. When scheduling Pods across existing nodes, kube-scheduler scores nodes that can satisfy earlier `firstAvailable` entries higher, ensuring Pods land on GPU nodes whenever GPU capacity is available.
3. **Runtime Image Mutation with Device Binding Conditions (KEP-5007)**: A GPU inference workload (`vllm/vllm-openai`) and a CPU inference workload (`vllm/vllm-cpu`) require different container images. However, the scheduler's choice between GPU and CPU is only known at scheduling time. By adding a gating device with a binding condition (`image-configurator.x-k8s.io/image-updated`), Kubelet blocks Pod startup until the `dra-driver-image-configurator` controller observes the allocation result, mutates the Pod's runtime container image to the matching vLLM image, emits an `ImagePatched` event, and satisfies the condition.

Together, these capabilities allow a **single Kubernetes Deployment** to scale seamlessly across both GPU and CPU nodes.

Let’s get started and explore how to achieve workload fungibility between GPUs and CPUs using DRA and Custom Compute Classes.

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
export COMPUTE_CLASS=fungible-gpu-cpu
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

### Create an autoscaling GPU node pool

We create a node pool with an NVIDIA L4 GPU per machine. We configure this node pool to start with 1 node and autoscale up to 2 nodes. We associate this node pool with our Custom Compute Class using the `cloud.google.com/compute-class` label and taint. We also disable the installation of the standard GPU Device Plugin since we will install the NVIDIA GPU DRA driver.

```bash
gcloud container node-pools create gpu-pool \
    --cluster=${CLUSTER_NAME} \
    --location=${LOCATION} \
    --node-locations=${ZONE} \
    --machine-type="g2-standard-8" \
    --accelerator="type=nvidia-l4,count=1,gpu-driver-version=disabled" \
    --num-nodes=1 \
    --enable-autoscaling \
    --min-nodes=0 \
    --max-nodes=2 \
    --node-labels=cloud.google.com/compute-class=${COMPUTE_CLASS},gke-no-default-nvidia-gpu-device-plugin=true,nvidia.com/gpu.present=true,cloud.google.com/gke-nvidia-gpu-dra-driver=true \
    --node-taints=cloud.google.com/compute-class=${COMPUTE_CLASS}:NoSchedule \
    --spot
```

### Create an autoscaling CPU node pool for fallback inference

We create a second node pool with standard CPU capacity for workloads to fall back to when GPU capacity is fully consumed. We configure this pool to start with 0 nodes and autoscale up to 2 nodes. We associate this pool with the same Custom Compute Class:

```bash
gcloud container node-pools create cpu-pool \
    --cluster=${CLUSTER_NAME} \
    --location=${LOCATION} \
    --node-locations=${ZONE} \
    --machine-type="n2-standard-16" \
    --num-nodes=0 \
    --enable-autoscaling \
    --min-nodes=0 \
    --max-nodes=2 \
    --node-labels=cloud.google.com/compute-class=${COMPUTE_CLASS},cloud.google.com/gke-cpu-dra-driver=true \
    --node-taints=cloud.google.com/compute-class=${COMPUTE_CLASS}:NoSchedule \
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
> Unlike earlier Gemma models (such as Gemma 2 and Gemma 3) which were gated and required accepting the Gemma Terms of Use on Hugging Face, Gemma 4 is released under the permissive Apache 2.0 license and is not gated. Accepting license terms is not required. However, providing a Hugging Face token is still recommended to prevent unauthenticated download rate limits from Hugging Face.

To create a Kubernetes Secret that contains the Hugging Face token, run the following command:

```bash
kubectl create secret generic hf-secret --from-literal=hf_api_token=${HF_TOKEN} --namespace=${NAMESPACE}
```

## **Install the NVIDIA GPU driver**

Since we disabled the installation of the GPU Device Plugin at node pool creation time, we need to install the NVIDIA GPU driver manually on the GPU node:

```bash
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml
```

## **Build, Push, and Install the DRA Drivers**

In this tutorial, we deploy three distinct DRA components:
1. **NVIDIA GPU DRA Driver**: Implements allocation for GPUs (`gpu.nvidia.com`).
2. **CPU DRA Driver**: Implements allocation for exclusive CPUs (`dra.cpu`).
3. **Image Configurator Controller & No-Op Driver**: Observes allocation results, mutates container runtime images, emits events, and resolves binding conditions (`image-configurator.x-k8s.io`).

> [!NOTE]
> The no-op driver is necessary for now when using the image configurator, but features coming in later versions of Kubernetes will eliminate the need to run this driver.

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

Clone the `dra-driver-cpu` repository and install the driver using its official Helm chart, targeted to nodes labeled for the CPU DRA driver:

```bash
git clone https://github.com/kubernetes-sigs/dra-driver-cpu.git

helm install dra-driver-cpu ./dra-driver-cpu/deployment/helm/dra-driver-cpu \
    --namespace=kube-system \
    --set 'nodeSelector.cloud\.google\.com/gke-cpu-dra-driver=true' \
    --set 'tolerations[0].operator=Exists'
```

### Build, Push, and Install the Image Configurator and No-Op Driver

Clone the `dra-drivers` repository containing the `dra-driver-image-configurator` controller and `dra-driver-noop` plugin. Build their container images and push them to your newly created Google Artifact Registry repository:

```bash
git clone https://github.com/gke-labs/dra-drivers.git

# Build and push dra-driver-noop
docker build -t ${REPO_URI}/dra-driver-noop:latest -f ./dra-drivers/dra-driver-noop/deployments/container/Dockerfile ./dra-drivers/dra-driver-noop
docker push ${REPO_URI}/dra-driver-noop:latest

# Build and push dra-driver-image-configurator
docker build -t ${REPO_URI}/dra-driver-image-configurator:latest ./dra-drivers/dra-driver-image-configurator
docker push ${REPO_URI}/dra-driver-image-configurator:latest
```

Install `dra-driver-noop` as a Kubelet plugin configured to pull from your Artifact Registry:

```bash
helm install dra-driver-noop ./dra-drivers/dra-driver-noop/deployments/helm \
    --namespace=kube-system \
    --set driverNames="image-configurator.x-k8s.io" \
    --set image.repository="${REPO_URI}/dra-driver-noop" \
    --set image.tag="latest"
```

The `dra-driver-image-configurator` runs as a Deployment in the `kube-system` namespace. Update its deployment manifest to point to your Artifact Registry image, then apply the deployment and the `DeviceClass`:

```bash
sed -i "s|image: .*|image: ${REPO_URI}/dra-driver-image-configurator:latest|g" ./dra-drivers/dra-driver-image-configurator/deploy/deployment.yaml

kubectl apply -f ./dra-drivers/dra-driver-image-configurator/deploy/deployment.yaml
kubectl apply -f ./dra-drivers/dra-driver-image-configurator/deploy/deviceclass.yaml
```

> [!NOTE]
> The `dra-driver-image-configurator` repository also includes an optional Validating Admission Webhook (`deploy/webhook.yaml`) that can be used in clusters with `cert-manager` to validate `ImageConfig` parameters in `ResourceClaim` and `ResourceClaimTemplate` objects at creation time.

### Verify that the drivers are working

Verify that all drivers have initialized successfully and published their respective `ResourceSlice` objects:

```bash
kubectl get pods -A | grep -E 'nvidia-dra-driver|dra-driver-cpu|image-configurator|dra-driver-noop'
```

All driver pods should be in a `Running` state. Verify published slices for `gpu.nvidia.com`, `dra.cpu`, and `image-configurator.x-k8s.io`:

```bash
kubectl get resourceslices -o yaml
```

Notice that the ResourceSlice for `image-configurator.x-k8s.io` publishes a device that is advertised as available from all nodes and specifies:
```yaml
bindingConditions:
- image-configurator.x-k8s.io/image-updated
bindingFailureConditions:
- image-configurator.x-k8s.io/image-update-failed
```

## **Create the Custom Compute Class (CCC)**

To instruct GKE how to autoscale nodes when scaling our workload, we define a `ComputeClass`. 

The `priorities` list in a `ComputeClass` controls **node autoscaling** (Cluster Autoscaler), not pod scheduling. When Pods are pending and require additional cluster capacity, Cluster Autoscaler attempts to scale up node pools in the order specified. It will scale `gpu-pool` until it reaches its limits (such as `max-nodes: 2` or quota constraints), and only then fall back to scaling `cpu-pool`.

Inspect the following `compute-class.yaml`:

```yaml
apiVersion: cloud.google.com/v1
kind: ComputeClass
metadata:
  name: fungible-gpu-cpu
spec:
  priorities:
    - nodepools: [gpu-pool]
    - nodepools: [cpu-pool]
  whenUnsatisfiable: DoNotScaleUp
```

Apply the Custom Compute Class:

```bash
kubectl apply -f compute-class.yaml
```

## **Create the DRA ResourceClaimTemplate**

To enable dynamic fallback between GPU and CPU at the device level, we define a `ResourceClaimTemplate` using the `firstAvailable` prioritized allocation field. 

The scheduler evaluates the requested subrequests in order:
1. `gpu`: Prioritizes allocating an NVIDIA GPU (`gpu.nvidia.com`). Nodes that can satisfy earlier entries in `firstAvailable` are scored higher by kube-scheduler.
2. `cpu`: Falls back to allocating 8 exclusive CPUs (`dra.cpu`) if a GPU is unavailable.

We also request the `image-config` gating device from `image-configurator.x-k8s.io`. This device injects the `bindingConditions: ["image-configurator.x-k8s.io/image-updated"]` condition, blocking Pod startup until the container image has been updated.

The `config` section provides opaque `ImageConfig` parameters for each subrequest:
- When `device/gpu` is selected, the controller mutates the container image to `vllm/vllm-openai:v0.7.2`.
- When `device/cpu` is selected, the controller mutates the container image to `vllm/vllm-cpu:v0.7.2`.

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

## **Deploy the vLLM Workload as a Single Deployment**

Rather than creating separate Deployments for GPU and CPU, we create a single unified `Deployment`. 

Key configuration elements:
- `nodeSelector`: Specifies `cloud.google.com/compute-class: fungible-gpu-cpu`, directing GKE's Cluster Autoscaler to follow the priorities declared in our Custom Compute Class (`gpu-pool` first, then `cpu-pool`) whenever new nodes are required.
- `tolerations`: Includes tolerations for the Custom Compute Class taint and NVIDIA GPU taints.
- `resourceClaims`: References our `gpu-or-cpu` `ResourceClaimTemplate`. For every Pod replica created by the Deployment, Kubernetes creates an associated `ResourceClaim`.
- `containers[0].image`: Uses a placeholder image (`registry.k8s.io/pause:3.10`). The `dra-driver-image-configurator` controller inspects the scheduler's device allocation, mutates the Pod image to the corresponding vLLM image, emits an `ImagePatched` event, and satisfies the binding condition before Kubelet starts the container.

Inspect the following `deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-fungible
  labels:
    app: vllm-fungible
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-fungible
  template:
    metadata:
      labels:
        app: vllm-fungible
    spec:
      nodeSelector:
        cloud.google.com/compute-class: fungible-gpu-cpu
      tolerations:
      - key: "cloud.google.com/compute-class"
        operator: "Exists"
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
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-service
spec:
  selector:
    app: vllm-fungible
  ports:
  - port: 8000
    targetPort: 8000
```

Apply the deployment and service:

```bash
kubectl apply -f deployment.yaml --namespace=${NAMESPACE}
```

## **Demonstrate Fungibility by Scaling the Deployment**

We will observe the full scaling lifecycle:
1. Deploy 1 replica on the initial GPU node.
2. Scale to 2 replicas, triggering Cluster Autoscaler to scale `gpu-pool` to 2 nodes.
3. Scale to 4 replicas, hitting the `max-nodes` limit of `gpu-pool` and triggering Cluster Autoscaler to scale `cpu-pool` to 2 nodes.

### 1. Inspect the first Pod (Allocated to GPU)

Wait for the first Pod to be scheduled:

```bash
kubectl get pods -l app=vllm-fungible -o wide
```

Notice that the Pod is placed on the node in `gpu-pool`. Kube-scheduler does not understand CCC or node pool priorities; instead, it evaluates the DRA `firstAvailable` list and scores nodes higher if they can satisfy earlier entries (`gpu.nvidia.com`). Because the existing node in `gpu-pool` has an available L4 GPU, it receives the highest score and Pod 1 is placed on it.

Verify that the `ResourceClaim` allocated the NVIDIA GPU (`gpu.nvidia.com`):

```bash
CLAIM_NAME=$(kubectl get pod -l app=vllm-fungible -o jsonpath='{.items[0].status.resourceClaimStatuses[0].resourceClaimName}')
kubectl get resourceclaim ${CLAIM_NAME} -o jsonpath='{.status.allocation.devices.results}' | jq .
```

Verify that the `dra-driver-image-configurator` controller satisfied the binding condition:

```bash
kubectl get resourceclaim ${CLAIM_NAME} -o jsonpath='{.status.devices}' | jq .
```

The output shows the `image-configurator.x-k8s.io/image-updated` condition with status `True`:
```json
[
  {
    "conditions": [
      {
        "lastTransitionTime": "2026-06-09T21:55:02Z",
        "message": "Container image has been updated",
        "reason": "ImagePatched",
        "status": "True",
        "type": "image-configurator.x-k8s.io/image-updated"
      }
    ],
    "device": "image-configurator",
    "driver": "image-configurator.x-k8s.io",
    "pool": "all-nodes"
  }
]
```

Confirm that the Pod's container image was mutated from `pause` to `vllm/vllm-openai:v0.7.2`:

```bash
kubectl get pods -l app=vllm-fungible -o jsonpath='{.items[0].spec.containers[0].image}'
```

Inspect the Pod events to see the `ImagePatched` event emitted by the controller:

```bash
POD_1=$(kubectl get pods -l app=vllm-fungible -o jsonpath='{.items[0].metadata.name}')
kubectl describe pod ${POD_1} | grep -E 'ImagePatched|Scheduled|Pulled|Created|Started'
```

### 2. Scale the Deployment to 2 Replicas (GPU Pool Autoscales)

The single physical L4 GPU on our initial node is now occupied by the first Pod. Scale the Deployment to 2 replicas:

```bash
kubectl scale deployment vllm-fungible --replicas=2 --namespace=${NAMESPACE}
```

Observe how GKE and DRA handle the new Pod:
1. **Pending Pod & Autoscaler Trigger**: Because the first GPU node's L4 GPU is claimed, Pod 2 is temporarily `Pending`.
2. **Custom Compute Class Evaluation**: Cluster Autoscaler evaluates the `priorities` list in `fungible-gpu-cpu`. The top priority is `gpu-pool`. Since `gpu-pool` currently has 1 node and its limit is `max-nodes: 2`, Cluster Autoscaler scales up `gpu-pool` to 2 nodes.
3. **Scheduling & Image Mutation**: When the second GPU node becomes ready, kube-scheduler places Pod 2 onto it. DRA allocates the node's L4 GPU, and `dra-driver-image-configurator` mutates the container image to `vllm/vllm-openai:v0.7.2`, emits an `ImagePatched` event, and satisfies the binding condition.

Verify that both Pods are running on GPU nodes:

```bash
kubectl get pods -l app=vllm-fungible -o wide
```

### 3. Scale the Deployment to 4 Replicas (CPU Pool Autoscales)

Now scale the Deployment to 4 replicas:

```bash
kubectl scale deployment vllm-fungible --replicas=4 --namespace=${NAMESPACE}
```

Observe the fallback to CPU capacity:
1. **GPU Limit Reached**: Both L4 GPUs across the 2 nodes in `gpu-pool` are occupied. Because `gpu-pool` has reached its `max-nodes: 2` limit, Cluster Autoscaler cannot scale `gpu-pool` any further.
2. **Fallback to CPU Pool**: Cluster Autoscaler moves to the second priority in the `ComputeClass`: `cpu-pool`. It scales up `cpu-pool` from 0 nodes to 2 nodes.
3. **DRA Prioritized Allocation**: Once the CPU nodes join the cluster, kube-scheduler places Pods 3 and 4 onto them. Because no GPUs are present on these nodes, DRA falls back to allocating 8 CPUs per Pod from `dra.cpu`.
4. **Image Mutation to CPU**: The `dra-driver-image-configurator` controller observes that `device/cpu` was satisfied for Pods 3 and 4, mutates their container images from `registry.k8s.io/pause:3.10` to `vllm/vllm-cpu:v0.7.2`, emits `ImagePatched` events, and unblocks Kubelet.

### 4. Verify the Heterogeneous Deployment

Verify all 4 Pods across the Deployment:

```bash
kubectl get pods -l app=vllm-fungible -o custom-columns=\
NAME:.metadata.name,\
NODE:.spec.nodeName,\
IMAGE:.spec.containers[0].image,\
STATUS:.status.phase
```

You should see output showing all 4 Pods running under the same Deployment, with 2 running on GPU nodes and 2 running on CPU nodes:

```
NAME                             NODE                                    IMAGE                     STATUS
vllm-fungible-7988d44bb5-x8k2p   gke-gpu-cpu-fungibility-gpu-pool-...    vllm/vllm-openai:v0.7.2   Running
vllm-fungible-7988d44bb5-m4z9n   gke-gpu-cpu-fungibility-gpu-pool-...    vllm/vllm-openai:v0.7.2   Running
vllm-fungible-7988d44bb5-c7q2d   gke-gpu-cpu-fungibility-cpu-pool-...    vllm/vllm-cpu:v0.7.2      Running
vllm-fungible-7988d44bb5-f1p8w   gke-gpu-cpu-fungibility-cpu-pool-...    vllm/vllm-cpu:v0.7.2      Running
```

Inspect the logs of the replicas to verify that both model servers have initialized successfully:

```bash
kubectl logs -l app=vllm-fungible --prefix=true --tail=50
```

The logs will show the first two replicas initializing vLLM with CUDA and L4 GPUs, while the third and fourth replicas initialize vLLM using the CPU backend.

## **Generate traffic to the model**

Because all GPU and CPU pods are managed by the same Deployment and fronted by the same `vllm-service`, incoming traffic is automatically load-balanced across both hardware architectures.

Start port-forwarding to the Service:

```bash
kubectl port-forward svc/vllm-service 8000:8000 &
```

Send completion requests to test inference:

```bash
curl http://localhost:8000/v1/completions \
-H "Content-Type: application/json" \
-d '{
    "model": "google/gemma-4-2b-it",
    "prompt": "Explain workload fungibility in Kubernetes in two sentences.",
    "max_tokens": 100,
    "temperature": 0.7
}'
```

You can also test individual Pods to observe the latency difference between GPU and CPU execution:

```bash
POD_GPU=$(kubectl get pods -l app=vllm-fungible -o jsonpath='{.items[?(@.spec.containers[0].image=="vllm/vllm-openai:v0.7.2")].metadata.name}' | awk '{print $1}')
POD_CPU=$(kubectl get pods -l app=vllm-fungible -o jsonpath='{.items[?(@.spec.containers[0].image=="vllm/vllm-cpu:v0.7.2")].metadata.name}' | awk '{print $1}')

# Test GPU Pod
kubectl port-forward pod/${POD_GPU} 8001:8000 &
curl -s -w "\nTime: %{time_total}s\n" http://localhost:8001/v1/completions \
-H "Content-Type: application/json" \
-d '{"model": "google/gemma-4-2b-it", "prompt": "Tell me a short joke", "max_tokens": 50}'

# Test CPU Pod
kubectl port-forward pod/${POD_CPU} 8002:8000 &
curl -s -w "\nTime: %{time_total}s\n" http://localhost:8002/v1/completions \
-H "Content-Type: application/json" \
-d '{"model": "google/gemma-4-2b-it", "prompt": "Tell me a short joke", "max_tokens": 50}'
```

Both instances return valid completion responses from the `google/gemma-4-2b-it` model.

## **Understanding the Benefit**

This tutorial demonstrated the synergy of three advanced Kubernetes and GKE capabilities:

| Layer | Technology | Role in Workload Fungibility |
|---|---|---|
| **Infrastructure Autoscaling** | **Custom Compute Classes (CCC)** | Declaratively prioritizes node pools for Cluster Autoscaler (`gpu-pool` first up to `max-nodes`, falling back to `cpu-pool`). |
| **Pod Scheduling & Device Allocation** | **DRA Prioritized Allocation (`firstAvailable`)** | Declares prioritized device claims (`gpu.nvidia.com` first, `dra.cpu` fallback). Kube-scheduler scores nodes higher if they satisfy earlier `firstAvailable` requests. |
| **Runtime Configuration** | **DRA Device Binding Conditions (KEP-5007) & `dra-driver-image-configurator`** | Holds container startup until device allocation completes, dynamically mutates the container image to match the allocated hardware, and emits observable Kubernetes events. |

In traditional Kubernetes environments, supporting both GPU and CPU inference required maintaining separate Deployments, separate Horizontal Pod Autoscalers (HPAs), and bespoke application-level traffic routers to spill over when GPU capacity was exhausted. 

With Custom Compute Classes, DRA prioritized allocation, and runtime image mutation via Device Binding Conditions, your application scales as a **single, unified Deployment**. It automatically leverages high-performance GPUs whenever available and seamlessly spills over to CPUs when GPUs are constrained, maximizing elasticity, obtainability, and cost-efficiency.

## **Clean up** 

To avoid incurring charges to your Google Cloud account for the resources that you created in this guide, run the following command to delete the cluster:

```bash
gcloud container clusters delete ${CLUSTER_NAME} \
  --location=${LOCATION}
```
