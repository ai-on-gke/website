---
linkTitle: "GPU fungibility with DRA and Custom Compute Classes"
title: "GPU fungibility with DRA and Custom Compute Classes"
description: "This tutorial guides you through how to achieve GPU fungibility on GKE using Custom Compute Classes and Dynamic Resource Allocation (DRA)."
weight: 30
owner:
  - name: "Morten Torkildsen"
    link: "https://github.com/mortent"
type: docs
tags:
 - GPU
 - DRA
 - Custom Compute Classes
 - Fungibility
draft: false
cloudShell: 
    enabled: true
    folder: site/content/docs/tutorials/dynamic-resource-allocation/gpu-fungibility
    editorFile: index.md
---

## **Background**

In a typical GPU cluster environment, AI/ML workloads are often tightly coupled to specific GPU hardware
configurations, node pools, or counts. For example, a large language model (LLM) serving deployment might be
configured to run on a single A100 80GB GPU. If that specific GPU type is out of stock, or if the corresponding
node pool reaches its resource limits, the workload cannot schedule—even if viable alternative hardware
(such as a node with two A100 40GB GPUs) is completely idle.

Managing these alternatives traditionally requires maintaining separate deployment configurations, using complex
node affinity rules, and manually updating container startup arguments (such as the model's tensor parallel size)
to match the selected hardware.

This tutorial demonstrates how to achieve **GPU Fungibility** using Dynamic Resource Allocation (DRA) and GKE
**Custom Compute Classes**. GPU fungibility allows a single, unmodified workload configuration to run seamlessly
across different hardware setups depending on real-time cluster availability and priorities.

### Key Concepts

1. **GKE Custom Compute Classes**:
   A [ComputeClass](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/about-custom-compute-classes) is an API that groups
   multiple node pools under a single class and defines a prioritized provisioning order. When GKE's cluster
   autoscaler detects unschedulable Pods requesting a specific `ComputeClass`, it automatically provisions nodes
   from the node pools in the exact priority order defined.

2. **DRA `firstAvailable` Resource Allocations**:
   Under the DRA framework, the `ResourceClaimTemplate` can specify a list of device allocation alternatives using
   the `firstAvailable` field. This instructs the Kubernetes scheduler to try allocating the preferred hardware
   option first, and automatically fall back to the subsequent options if the primary option cannot be allocated.

3. **Dynamic Application Discovery**:
   By combining prioritized hardware claims with runtime discovery (e.g., using `nvidia-smi -L` at container
   startup), the application can dynamically configure its parameters—such as vLLM's `tensor-parallel-size`—to
   match the exact resources assigned by GKE, keeping the deployment manifest fully generic and portable.

By combining these capabilities, you can build resilient, portable AI workloads that maximize cluster utilization
and automatically adapt to real-time hardware availability in GKE.

## **Prepare the Environment**

To set up your environment with Cloud Shell, follow these steps:

1. In the Google Cloud console, click the **Activate Cloud Shell** icon to launch a session in the bottom pane.
2. Set the default environment variables:

```bash
export PROJECT_ID=$(gcloud config get project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
export CLUSTER_NAME=gpu-dra-fungibility
export LOCATION=us-central1 # Choose a region that has NVIDIA A100 GPUs available
export ZONE=us-central1-c # Choose a zone within the region that has A100 GPUs available. Look at https://cloud.google.com/compute/docs/gpus/gpu-regions-zones for availability.
export HF_TOKEN=HUGGING_FACE_TOKEN # Replace with your actual Hugging Face token
export CLUSTER_VERSION="1.36" # Must be 1.34 or later
export NAMESPACE=default
```

## **Create and configure Google Cloud Resources**

### Create a GKE Cluster

```bash
gcloud container clusters create $CLUSTER_NAME \
    --project=$PROJECT_ID \
    --location=$LOCATION \
    --cluster-version=$CLUSTER_VERSION \
    --labels=created-by=ai-on-gke,guide=gpu-fungibility
```

### Create node pools with different types of A100 GPUs

This creates two different node pools. One with the a2-ultragpu-1g machine type with a single A100 80GB GPU and
another with the a2-highgpu-2g machine type with two A100 40GB GPUs. This means that every node that will be created
will have a total of 80GB of aggregate GPU memory.

Since we will be using these node pools with Custom Compute Classes, we add the necessary labels and taints to the
node pools. We also disable the default GPU Device Plugin installation and set the
`cloud.google.com/gke-nvidia-gpu-dra-driver=true` label to let the cluster autoscaler know that the node pool uses
the DRA driver.

We start with a single node in the `a100-80gb-pool` and zero nodes in the `a100-40gb-pool`. Since A100 GPUs are highly scarce,
we request Spot VMs for both node pools to increase our chances of successfully allocating these resources.

```bash
gcloud container node-pools create a100-80gb-pool \
    --project=$PROJECT_ID \
    --cluster=${CLUSTER_NAME} \
    --location=${LOCATION} \
    --node-locations=${ZONE} \
    --machine-type="a2-ultragpu-1g" \
    --accelerator="type=nvidia-a100-80gb,count=1,gpu-driver-version=disabled" \
    --enable-autoscaling \
    --num-nodes=1 \
    --min-nodes=1 \
    --max-nodes=2 \
    --node-labels=gke-no-default-nvidia-gpu-device-plugin=true,nvidia.com/gpu.present=true,cloud.google.com/compute-class=vllm-gpu-ccc,cloud.google.com/gke-nvidia-gpu-dra-driver=true \
    --node-taints="cloud.google.com/compute-class=vllm-gpu-ccc:NoSchedule" \
    --disk-size=300

gcloud container node-pools create a100-40gb-pool \
    --project=$PROJECT_ID \
    --cluster=${CLUSTER_NAME} \
    --location=${LOCATION} \
    --node-locations=${ZONE} \
    --machine-type="a2-highgpu-2g" \
    --accelerator="type=nvidia-tesla-a100,count=2,gpu-driver-version=disabled" \
    --enable-autoscaling \
    --num-nodes=0 \
    --min-nodes=0 \
    --max-nodes=2 \
    --node-labels=gke-no-default-nvidia-gpu-device-plugin=true,nvidia.com/gpu.present=true,cloud.google.com/compute-class=vllm-gpu-ccc,cloud.google.com/gke-nvidia-gpu-dra-driver=true \
    --node-taints="cloud.google.com/compute-class=vllm-gpu-ccc:NoSchedule" \
    --disk-size=300 \
    --spot
```

## **Configure Kubectl to communicate with your cluster**
To configure kubectl to communicate with your cluster, run the following command:

```bash
gcloud container clusters get-credentials ${CLUSTER_NAME} --location=${LOCATION}
```

## **Create Kubernetes Secret for Hugging Face credentials**

To create a Kubernetes Secret that contains the Hugging Face token, run the following command:

```bash
kubectl create secret generic hf-secret --from-literal=hf_api_token=${HF_TOKEN} --namespace=${NAMESPACE}
```

## **Install the NVIDIA GPU driver**

Since we disabled the installation of the GPU Device Plugin at node pool creation time, we need to install the
NVIDIA GPU driver manually.

```bash
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml
```

## **Install the NVIDIA GPU DRA driver**

We install the NVIDIA GPU DRA driver using a Helm chart from the official Kubernetes OCI registry. Make sure that you have Helm installed, if not,
you can follow the [Helm documentation](https://helm.sh/docs/intro/install/) to install it.

```bash
helm install dra-driver-nvidia-gpu oci://registry.k8s.io/dra-driver-nvidia/charts/dra-driver-nvidia-gpu \
    --version="0.4.0" --create-namespace --namespace=dra-driver-nvidia-gpu \
    --set nvidiaDriverRoot="/home/kubernetes/bin/nvidia/" \
    --set gpuResourcesEnabledOverride=true \
    --set resources.computeDomains.enabled=false \
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
kubectl get resourceslices -o yaml
```

You should see the description of the GPU:

```
spec:
  driver: gpu.nvidia.com
  nodeName: gke-gpu-a100-80gb-pool-c78060b4-cg6n
  pool:
    generation: 1
    name: gke-gpu-a100-80gb-pool-c78060b4-cg6n
    resourceSliceCount: 1
  devices:
  - name: gpu-0
    attributes:
      addressingMode:
        string: None
      architecture:
        string: Ampere
      brand:
        string: Nvidia
      cudaComputeCapability:
        version: 8.0.0
      cudaDriverVersion:
        version: 13.0.0
      driverVersion:
        version: 580.126.20
      productName:
        string: NVIDIA A100-SXM4-80GB
      resource.kubernetes.io/pciBusID:
        string: "0000:00:05.0"
      resource.kubernetes.io/pcieRoot:
        string: pci0000:00
      type:
        string: gpu
      uuid:
        string: GPU-e89d84ee-93b4-a63d-ac3f-8c242831ac09
    capacity:
      memory:
        value: 80Gi
```

## **Create the Custom Compute Class**

We use a Custom Compute Class to tell the cluster autoscaler which node pools to consider
when scaling up. GKE doesn't yet have support for DRA for node pool auto-creation, so we
need to use already existing node pools. However, they can be empty.
Inspect the following `ccc.yaml`. We reference the two node pools we created earlier, with the `a100-80gb-pool`
having higher priority than `a100-40gb-pool`. This means that the cluster autoscaler will only scale up the
`a100-40gb-pool` if it is unable to scale up the `a100-80gb-pool`. For this tutorial we capped the number of nodes
allowed in the `a100-80gb-pool` to 2 nodes, thereby forcing the cluster autoscaler to consider the lower priority
pool if we need more than 2 GPU nodes. In a production scenario you would likely not set a cap and only fall back
if the nodes with higher priority is stocked out or have other resource constraints.

```yaml
apiVersion: cloud.google.com/v1
kind: ComputeClass
metadata:
  name: vllm-gpu-ccc
spec:
  autoscalingPolicy:
    consolidationDelayMinutes: 3
  priorities:
  - nodepools:
    - a100-80gb-pool
  - nodepools:
    - a100-40gb-pool
```

```bash
kubectl apply -f ccc.yaml --namespace=${NAMESPACE}
```

## **Create the DRA ResourceClaimTemplate**

We prefer to use a single a100 with 80Gb of memory if one is available in the cluster, and we only want to fall back
to using two a100 40gb GPUs if no single 80gb a100 is available. We specify a prioritized list of the devices we
can use for our workload and specify that a single NVIDIA GPU with exactly 80Gi of memory is our first priority, with
the second alternative being two NVIDIA GPUs.

This mirrors the configuration in the Custom Compute Class we created above. We can think of this as the Custom
Compute Class being an API for instructing the Cluster Autoscaler which nodes it should create when it detects
unschedulable Pods, while the ResourceClaim/ResourceClaimTemplate is an API for configuring the scheduler to
select the right devices when it schedules Pods.

Inspect the following `claim.yaml`.

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: gpu-claim
spec:
  spec:
    devices:
      requests:
      - name: gpu
        firstAvailable:
        - name: a100-80gb
          allocationMode: ExactCount
          count: 1
          deviceClassName: gpu.nvidia.com
          selectors:
          - cel:
              expression: "device.capacity['gpu.nvidia.com'].memory == quantity('80Gi')"
        - name: a100-40gb-2x
          allocationMode: ExactCount
          count: 2
          deviceClassName: gpu.nvidia.com
```

Apply the manifest

```bash
kubectl apply -f claim.yaml --namespace=${NAMESPACE}
```

## **Deploy the vllm workload**

We are using vllm to serve the Gemma 4 31B model. We create a Deployment that initially
will run just a single replica of vllm, which will be scheduled on the GPU node we already
have running in our cluster.

Inspect the following `vllm.yaml`. We use a special entrypoint for vllm that allows us to
check how many GPUs are available to the container, and use that to configure the tensor parallel size.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-gpu
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-gpu
  template:
    metadata:
      labels:
        app: vllm-gpu
    spec:
      nodeSelector:
        cloud.google.com/compute-class: vllm-gpu-ccc
      tolerations:
      - key: "nvidia.com/gpu"
        operator: "Exists"
        effect: "NoSchedule"
      resourceClaims:
      - name: gpu
        resourceClaimTemplateName: gpu-claim
      containers:
      - name: vllm-gpu
        image: vllm/vllm-openai:gemma4
        command: ["/bin/bash", "-c"]
        args:
        - >
          NUM_GPUS=$(nvidia-smi -L | wc -l);
          python3 -m vllm.entrypoints.openai.api_server --host=0.0.0.0 --port=8000 --model=google/gemma-4-31B-it --tensor-parallel-size=${NUM_GPUS} --max-model-len 65536
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
  name: vllm-service
spec:
  selector:
    app: vllm-gpu
  type: LoadBalancer
  ports:
    - name: http
      protocol: TCP
      port: 8000
      targetPort: 8000
```

Apply the manifest

```bash
kubectl apply -f vllm.yaml --namespace=${NAMESPACE}
```

View the logs from the running model servers:

```bash
kubectl logs -l app=vllm-gpu --prefix -f --namespace=${NAMESPACE}
```

You should see something like this when vllm is ready to serve the model:

```
(APIServer pid=1) INFO 04-25 21:39:25 [launcher.py:46] Route: /v1/completions/render, Methods: POST
(APIServer pid=1) INFO:     Started server process [1]
(APIServer pid=1) INFO:     Waiting for application startup.
(APIServer pid=1) INFO:     Application startup complete.
```

## **Scale up to 3 replicas**

Scale up to 3 replicas to observe how the second Pod gets scheduled on another
a100 with 80Gi of memory, while the third pod gets scheduled on a node with
two a100-40Gi GPUs. Or in the event that machines with the a100-80gb GPUs 
are stocked out, you might see two nodes with two a100-40Gi GPUs each.

```bash
kubectl scale deployment vllm-gpu --replicas=3 --namespace=${NAMESPACE}
```

Again, wait for the Pods to be scheduled and become Running and Ready. You can do this by running
`kubectl get pods -n ${NAMESPACE}` and observing the `STATUS` column.

## **Generate traffic to the model**

We will send requests to the model servers and then use the logs to verify that we are getting
responses from all replicas.

First we get the external IP of the service

> [!NOTE]
> Provisioning the external IP for the `vllm-service` LoadBalancer may take a few minutes. If the `export` command
> fails or returns empty, wait a moment and try again.

```bash
export vllm_service=$(kubectl get service vllm-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}' -n ${NAMESPACE})
```

Send a bunch of requests to the model servers. We use a loop to ensure that traffic hits all replicas.

```bash
for i in {1..5}; do
  curl http://$vllm_service:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-4-31B-it",
      "messages": [
          {"role": "user", "content": "Tell a story about the city of Boston"}
      ],
      "max_tokens": 100,
      "temperature": 0
  }'
done
```

The output from the replicas should contain lines similar to this:

```
(APIServer pid=1) INFO:     10.0.3.1:47463 - "POST /v1/chat/completions HTTP/1.1" 200 OK
```

## **Understanding the DRA Benefit**

This tutorial demonstrated one of the key benefits of using Dynamic Resource Allocation (DRA) for GPU fungibility.
With traditional Device Plugins, workloads are tightly coupled to specific node pools and static hardware
configurations. If you wanted to run a workload on different GPU types, you would have to maintain separate
Deployment configurations or manually adjust your resource requests based on the node type.

With DRA and Custom Compute Classes, we achieved **GPU fungibility**:
- **Flexible Hardware Choice:** The `ResourceClaimTemplate` allowed us to express a prioritized list of acceptable
  GPU setups (either a single A100 80GB or two A100 40GBs).
- **Simplified Scheduling:** We didn't have to rely on rigid node selector or node affinity rules targeting specific
  GPU node pools to ensure Pods land on the right nodes. Instead, the scheduler automatically routed the Pods to
  the correct nodes based on the hardware requirements expressed directly in the `ResourceClaim`.
- **Automatic Dynamic Scaling:** Custom Compute Classes steered GKE's cluster autoscaler to scale up the best
  available node pool dynamically.
- **Workload Portability:** The workload adjusted its tensor parallel configuration at runtime by discovering the
  allocated GPU devices, keeping the main deployment manifest entirely generic.

This level of dynamism ensures higher workload resilience, reduces manual operations, and optimizes cluster
resource utilization.

## **Clean up** 

To avoid incurring charges to your Google Cloud account for the resources that you created in this guide,
run the following command to delete the cluster:

```bash
gcloud container clusters delete ${CLUSTER_NAME} \
  --location=${LOCATION}
```
