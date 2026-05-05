---
linkTitle: "Time slicing of GPUs with DRA"
title: "Time slicing of GPUs with DRA"
description: "This tutorial guides you through how to do device sharing of NVIDIA GPUs with Dynamic Resource Allocation on Google Kubernetes Engine (GKE) with the time slicing mode"
weight: 30
owner:
  - name: "Morten Torkildsen"
    link: "https://github.com/mortent"
type: docs
tags:
 - GPU
 - Device Sharing
 - DRA
 - Time Slicing
draft: false
cloudShell: 
    enabled: true
    folder: site/content/docs/tutorials/dynamic-resource-allocation/gpu-time-slicing
    editorFile: index.md
---

## **Background**

This tutorial guides you through how to do device sharing of NVIDIA GPUs with Dynamic Resource Allocation on Google Kubernetes Engine (GKE). This guide covers time slicing, with separate guides covering the other device sharing modes, MIG and Multi-Process Service (MPS).

{{% include "/site/content/docs/tutorials/dynamic-resource-allocation/gpu-sharing-common.md" %}}

Let’s get started and explore how to share GPUs with time slicing.

## **Prepare the Environment**

To set up your environment with Cloud Shell, follow these steps:

1. In the Google Cloud console, click the **Activate Cloud Shell** icon to launch a session in the bottom pane.
2. Set the default environment variables:

```bash
export PROJECT_ID=$(gcloud config get project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
export CLUSTER_NAME=gpu-vllm-timeslicing
export LOCATION=us-central1 # Choose a region that has NVIDIA A100 GPUs available
export ZONE=us-central1-c # Choose a zone within the region that has A100 GPUs available. Look at https://cloud.google.com/compute/docs/gpus/gpu-regions-zones for availability.
export HF_TOKEN=HUGGING_FACE_TOKEN # Replace with your actual Hugging Face token
export CLUSTER_VERSION="1.35.2-gke.1269001" # Must be 1.34 or later
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
--labels=created-by=ai-on-gke,guide=gpu-time-slicing
```

### Create a node pool with A100 GPUs

This creates a node pool with just a single machine with a single A100 GPU. We disable installation of the GPU Device Plugin since we will be installing
the NVIDIA GPU DRA driver instead. We request spot capacity here to increase the chance of getting a node quickly.

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

We install the NVIDIA GPU DRA driver using a Helm chart. Make sure that you have Helm installed, if not,
you can follow the [Helm documentation](https://helm.sh/docs/intro/install/) to install it. Time slicing is
still a beta feature in the NVIDIA GPU DRA driver, so we need to enable it by setting
`featureGates.TimeSlicingSettings=true`.

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update

helm install nvidia-dra-driver-gpu nvidia/nvidia-dra-driver-gpu \
    --version="25.12.0" --create-namespace --namespace=nvidia-dra-driver-gpu \
    --set nvidiaDriverRoot="/home/kubernetes/bin/nvidia/" \
    --set gpuResourcesEnabledOverride=true \
    --set resources.computeDomains.enabled=false \
    --set featureGates.TimeSlicingSettings=true \
    --set kubeletPlugin.priorityClassName="" \
    --set 'kubeletPlugin.tolerations[0].operator=Exists' # Needed to ensure the driver can run on tainted GPU nodes
```

### Verify that the NVIDIA GPU DRA driver is working

Check that the NVIDIA GPU DRA driver is installed and working by inspecting the driver pod:

```bash
kubectl -n nvidia-dra-driver-gpu get pods
```

The pod should be in a Running state. If not, you can inspect the logs with:

```bash
kubectl -n nvidia-dra-driver-gpu logs -l app.kubernetes.io/name=nvidia-dra-driver-gpu -c gpus
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
  nodeName: gke-gpu-vllm-timeslicing-a100-pool-efc3ca7a-0w1g
  pool:
    generation: 1
    name: gke-gpu-vllm-timeslicing-a100-pool-efc3ca7a-0w1g
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
        version: 580.126.9
      productName:
        string: NVIDIA A100-SXM4-40GB
      resource.kubernetes.io/pciBusID:
        string: "0000:00:04.0"
      resource.kubernetes.io/pcieRoot:
        string: pci0000:00
      type:
        string: gpu
      uuid:
        string: GPU-83d857e2-7326-75a9-c355-4270d0605409
    capacity:
      memory:
        value: 40Gi
```

## **Create the DRA ResourceClaim**

We will be using a single GPU that will be shared between two vllm replicas. Therefore we create a single `ResourceClaim` that will be referenced from the Pod spec in the Deployment, rather than using a `ResourceClaimTemplate`. By having multiple pods reference the exact same `ResourceClaim`, we explicitly tell Kubernetes to share the identical GPU slice across those pods.

Inspect the following `claim.yaml`. We use `interval: Long` in the time slicing configuration to set a long time slice interval, which is suitable for inference workloads to minimize context switching overhead.

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
              strategy: TimeSlicing
              timeSlicingConfig:
                interval: Long
```

Apply the manifest

```bash
kubectl apply -f claim.yaml --namespace=${NAMESPACE}
```

## **Deploy the vllm workload**

We are using the Gemma 3 models as they are smaller and therefore let us run the tutorial using hardware (i.e., GPUs) that are more easily available than what would be needed to run larger foundation models.

We create a Deployment that runs two replicas of vllm. We reference the ResourceClaim `gpu-claim` in
the pod spec, so both pods will reference the same claim.

Notice the new DRA syntax in the Pod spec: `spec.resourceClaims` defines the claim to be used, and `resources.claims` in the container spec links the container to that defined claim.

We set the `gpu-memory-utilization` parameter for vllm to `0.42`, which means that each
replica will use 42% of the GPU memory. This prevents the first replica from consuming
the entire GPU, leaving memory available for the second replica.

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
        image: vllm/vllm-openai:v0.7.2
        command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
        args:
        - --host=0.0.0.0
        - --port=8000
        - --model=google/gemma-3-1b-it
        - --gpu-memory-utilization=0.42
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

We will send requests to the model servers and then use the logs to verify that we are getting
responses from both replicas.

First we get the external IP of the service

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

Despite having only a single GPU, we were able to run two replicas of vllm, because we 
enabled time slicing in the NVIDIA GPU DRA driver.

## **Understanding the DRA Benefit**

This tutorial demonstrated one of the key benefits of using Dynamic Resource Allocation (DRA) for GPU sharing. With the traditional Device Plugin, you would have had to provision a node pool statically configured with time slicing enabled.

With DRA, we were able to run multiple replicas of a workload sharing the **same** physical A100 GPU without any node-level reconfiguration. Kubernetes and the NVIDIA DRA driver dynamically fulfilled the `ResourceClaim` requests defined in the workload manifests. This provides significant flexibility, maximizes hardware utilization, and allows developers to request exactly what they need at the application level.

## **Clean up** 

To avoid incurring charges to your Google Cloud account for the resources that you created in this guide, run the following command to delete the cluster:

```bash
gcloud container clusters delete ${CLUSTER_NAME} \
  --location=${LOCATION}
```
