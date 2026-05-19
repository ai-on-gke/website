---
linkTitle: "Device Sharing of GPUs with DRA using MPS"
title: "Device Sharing of GPUs with DRA using Multi-Process Service (MPS)"
description: "This tutorial guides you through how to do device sharing of NVIDIA GPUs with Dynamic Resource Allocation on Google Kubernetes Engine (GKE) using Multi-Process Service (MPS) mode."
weight: 32
owner:
  - name: "Morten Torkildsen"
    link: "https://github.com/mortent"
type: docs
tags:
 - GPU
 - Device Sharing
 - DRA
 - MPS
draft: false
cloudShell: 
    enabled: true
    folder: site/content/docs/tutorials/dynamic-resource-allocation/gpu-mps
    editorFile: index.md
---

## **Background**

This tutorial guides you through how to do device sharing of NVIDIA GPUs with Dynamic Resource Allocation on Google Kubernetes Engine (GKE). This guide covers Multi-Process Service (MPS), with separate guides covering the other device sharing modes, time slicing and Multi-Instance GPU (MIG).

{{% include "/site/content/docs/tutorials/dynamic-resource-allocation/gpu-sharing-common.md" %}}

Let’s get started and explore how to share GPUs with MPS.

## **Prepare the Environment**

To set up your environment with Cloud Shell, follow these steps:

1. In the Google Cloud console, click the **Activate Cloud Shell** icon to launch a session in the bottom pane.
2. Set the default environment variables:

```bash
export PROJECT_ID=$(gcloud config get project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
export CLUSTER_NAME=gpu-vllm-mps
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
    --location=$LOCATION \
    --cluster-version=$CLUSTER_VERSION \
    --project=$PROJECT_ID \
    --num-nodes=1 \
    --labels=created-by=ai-on-gke,guide=gpu-mps
```

### Create a node pool with A100 GPUs

This creates a node pool with just a single machine with a single A100 GPU. We disable installation of the GPU Device Plugin since we will be installing the NVIDIA GPU DRA driver instead. We request spot capacity here to increase the chance of getting a node quickly.

```bash
gcloud container node-pools create a100-pool \
    --cluster=${CLUSTER_NAME} \
    --location=${LOCATION} \
    --node-locations=${ZONE} \
    --machine-type="a2-highgpu-1g" \
    --accelerator="type=nvidia-tesla-a100,count=1,gpu-driver-version=disabled" \
    --num-nodes=1 \
    --node-labels=gke-no-default-nvidia-gpu-device-plugin=true,nvidia.com/gpu.present=true,cloud.google.com/gke-nvidia-gpu-dra-driver=true \
    --spot
```

## **Configure Kubectl to communicate with your cluster**

To configure kubectl to communicate with your cluster, run the following command:

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

Since we disabled the installation of the GPU Device Plugin at node pool creation time, we need to install the NVIDIA GPU driver manually.

```bash
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml
```

## **Install the NVIDIA GPU DRA driver**

We install the NVIDIA GPU DRA driver using a Helm chart from the official Kubernetes OCI registry. Make sure that you have Helm installed, if not, you can follow the [Helm documentation](https://helm.sh/docs/intro/install/) to install it.

To use Multi-Process Service (MPS), we must enable it by setting the `featureGates.MPSSupport=true` flag.

```bash
helm install dra-driver-nvidia-gpu oci://registry.k8s.io/dra-driver-nvidia/charts/dra-driver-nvidia-gpu \
    --version="0.4.0" --create-namespace --namespace=dra-driver-nvidia-gpu \
    --set nvidiaDriverRoot="/home/kubernetes/bin/nvidia/" \
    --set gpuResourcesEnabledOverride=true \
    --set resources.computeDomains.enabled=false \
    --set featureGates.MPSSupport=true \
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

You should see the description of the GPU with standard Ampere/A100 properties, confirming that the physical GPU is ready to be claimed.

## **Create the DRA ResourceClaim**

We will be using a single GPU that will be shared between two vLLM replicas. Therefore, we create a single `ResourceClaim` that will be referenced from the Pod spec in the Deployment. By having multiple pods reference the exact same `ResourceClaim`, we explicitly tell Kubernetes to share the identical GPU slice across those pods.

Inspect the following `claim.yaml`. We configure `sharing.strategy` to `MPS` and specify `defaultActiveThreadPercentage: 50` to divide CUDA compute resource capacity limits among the clients sharing this GPU slice. We use 50% here since we are planning to share the GPU between exactly two replicas, ensuring each gets an equal share of the compute execution resources. We also set `defaultPinnedDeviceMemoryLimit: 15Gi` to restrict the maximum pinned GPU device memory each sharing replica can allocate to 15 GiB.

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: gpu-claim
spec:
  devices:
    requests:
    - name: gpu
      exactly:
        deviceClassName: gpu.nvidia.com
    config:
      - requests: ["gpu"]
        opaque:
          driver: gpu.nvidia.com
          parameters:
            apiVersion: resource.nvidia.com/v1beta1
            kind: GpuConfig
            sharing:
              strategy: MPS
              mpsConfig:
                defaultActiveThreadPercentage: 50
                defaultPinnedDeviceMemoryLimit: 15Gi
```

Apply the manifest:

```bash
kubectl apply -f claim.yaml --namespace=${NAMESPACE}
```

## **Deploy the vllm workload**

We are using the Gemma 3 models as they are smaller and therefore let us run the tutorial using hardware (i.e., GPUs) that are more easily available than what would be needed to run larger foundation models.

We create a Deployment that runs two replicas of vLLM. We reference the ResourceClaim `gpu-claim` in the pod spec, so both pods will reference the same claim.

Notice the new DRA syntax in the Pod spec: `spec.resourceClaims` defines the claim to be used, and `resources.claims` in the container spec links the container to that defined claim.

We set the `gpu-memory-utilization` parameter for vLLM to `0.35`. Because vLLM does not automatically detect the CUDA MPS memory limit (`defaultPinnedDeviceMemoryLimit: 15Gi`) and still sees the total physical GPU size (approx. 40 GiB), its default allocation strategy (which tries to pre-allocate 92% of the physical GPU, i.e., ~36 GiB) will exceed our 15 GiB MPS memory cap and cause the model server to crash. Setting `--gpu-memory-utilization=0.35` ensures that vLLM only attempts to pre-allocate `40 GiB * 0.35 = 14 GiB` of memory, fitting safely within the 15 GiB limit.

Inspect the following `vllm.yaml`.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-gpu
spec:
  replicas: 2
  selector:
    matchLabels:
      app: vllm-gpu
  template:
    metadata:
      labels:
        app: vllm-gpu
    spec:
      tolerations:
      - key: "nvidia.com/gpu"
        operator: "Exists"
        effect: "NoSchedule"
      resourceClaims:
      - name: gpu
        resourceClaimName: gpu-claim
      containers:
      - name: vllm-gpu
        image: vllm/vllm-openai:v0.21.0
        command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
        args:
        - --host=0.0.0.0
        - --port=8000
        - --model=google/gemma-3-1b-it
        - --gpu-memory-utilization=0.35
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

Apply the manifest:

```bash
kubectl apply -f vllm.yaml --namespace=${NAMESPACE}
```

View the logs from the running model servers:

```bash
kubectl logs -l app=vllm-gpu --prefix -f
```

You should see something like this from both replicas:

```
(APIServer pid=1) INFO 04-25 21:39:25 [launcher.py:46] Route: /v1/completions/render, Methods: POST
(APIServer pid=1) INFO:     Started server process [1]
(APIServer pid=1) INFO:     Waiting for application startup.
(APIServer pid=1) INFO:     Application startup complete.
```

## **Generate traffic to the model**

We will send requests to the model servers and then use the logs to verify that we are getting responses from both replicas.

First we get the external IP of the service:

> [!NOTE]
> Provisioning the external IP for the `vllm-service` LoadBalancer may take a few minutes. If the `export` command fails or returns empty, wait a moment and try again.

```bash
export vllm_service=$(kubectl get service vllm-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}' -n ${NAMESPACE})
```

Send a bunch of requests to the model servers. We use a loop to ensure that traffic hits both replicas.

```bash
for i in {1..5}; do
  curl http://$vllm_service:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-3-1b-it",
      "prompt": "Write a story about san francisco",
      "max_tokens": 100,
      "temperature": 0
  }'
done
```

The output from both replicas should contain lines similar to this:

```
(APIServer pid=1) INFO:     10.0.3.1:47463 - "POST /v1/completions HTTP/1.1" 200 OK
```

Despite having only a single GPU, we were able to run two concurrent replicas of vLLM with active simultaneous hardware utilization, because we enabled **MPS** in the NVIDIA GPU DRA driver.

## **Understanding the DRA Benefit**

This tutorial demonstrated one of the key benefits of using Dynamic Resource Allocation (DRA) for GPU sharing using Multi-Process Service (MPS). With the traditional Device Plugin, you would have had to provision a node pool statically configured with MPS enabled.

With DRA, we were able to run multiple replicas of a workload sharing the **same** physical A100 GPU with concurrent spatial sharing without any node-level reconfiguration. Kubernetes and the NVIDIA DRA driver dynamically configured the MPS control daemon on the fly to fulfill the `ResourceClaim` requests defined in the workload manifests. This provides significant flexibility, maximizes hardware utilization, and allows developers to request exactly what they need at the application level.

## **Clean up** 

To avoid incurring charges to your Google Cloud account for the resources that you created in this guide, run the following command to delete the cluster:

```bash
gcloud container clusters delete ${CLUSTER_NAME} \
  --location=${LOCATION}
```
