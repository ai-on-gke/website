---
linkTitle: "Prioritized TPUs with DRA"
title: "Prioritized Device Allocation of TPUs with DRA"
description: "This tutorial guides you through how to use Dynamic Resource Allocation (DRA) prioritized lists on Google Kubernetes Engine (GKE) to prefer TPU v6e hardware and automatically fallback to TPU v5p when v6e is unavailable."
weight: 45
owner:
  - name: "AI on GKE Contributors"
type: docs
tags:
 - TPU
 - DRA
 - Prioritized Allocation
draft: false
cloudShell:
    enabled: true
    folder: site/content/docs/tutorials/dynamic-resource-allocation/tpu-prioritized-list
    editorFile: index.md
---

## **Background**

This tutorial guides you through how to use **Dynamic Resource Allocation (DRA)** prioritized lists (`firstAvailable`) to provision and consume Tensor Processing Units (TPUs) across different hardware generations on Google Kubernetes Engine (GKE). This guide covers prioritized allocation, with separate guides covering basic TPU allocation.

{{% include "/site/content/docs/tutorials/dynamic-resource-allocation/tpu-dra-common.md" %}}

Let’s get started and explore how to allocate TPUs using prioritized lists.

---

## **Prepare the Environment**

To set up your environment with Cloud Shell, follow these steps:

1. In the Google Cloud console, click the **Activate Cloud Shell** icon to launch a session in the bottom pane.
2. Set the default environment variables:

```bash
export PROJECT_ID=$(gcloud config get project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
export CLUSTER_NAME=tpu-dra-prioritized-cluster
export REGION=us-central1-b # Region where both TPU v6e and TPU v5p are available
export MACHINE_TYPE_V6=ct6e-standard-4t # TPU v6e virtual machine with 4 TPU chips
export MACHINE_TYPE_V5=ct5p-hightpu-4t  # TPU v5p virtual machine with 4 TPU chips
export HF_TOKEN=HUGGING_FACE_TOKEN # Replace with your actual Hugging Face token
export CLUSTER_VERSION="1.36.0-gke.1759000" # Must be 1.34.1-gke.1127000 or later
export NAMESPACE=default
```

---

## **Create and configure Google Cloud Resources**

### Create a GKE Cluster

Let's create a GKE cluster that will host our DRA-based TPU workloads:

```bash
gcloud container clusters create $CLUSTER_NAME \
  --region=$REGION \
  --cluster-version=$CLUSTER_VERSION \
  --project=$PROJECT_ID \
  --num-nodes=1 \
  --labels=created-by=ai-on-gke,guide=tpu-dra-prioritized
```

### Create TPU Node Pools configured for DRA

Next, we create two distinct node pools: one for our preferred TPU v6e hardware and one for our fallback TPU v5p hardware.

> [!IMPORTANT]
> To allow the TPU DRA driver to manage the TPU hardware, we must disable GKE's default TPU Device Plugin. We do this by applying the node label `cloud.google.com/gke-tpu-dra-driver=true` during node pool creation.

> [!NOTE]
> Since TPU resources can be highly sought after, we recommend using the `--spot` flag to request spot capacity. Spot nodes increase your chances of securing a TPU node quickly and cost-effectively.

Create the preferred TPU v6e node pool:

```bash
gcloud container node-pools create tpu-v6e-nodepool \
  --cluster=$CLUSTER_NAME \
  --region=$REGION \
  --num-nodes=1 \
  --node-version=$CLUSTER_VERSION \
  --node-labels=cloud.google.com/gke-tpu-dra-driver=true \
  --machine-type=$MACHINE_TYPE_V6 \
  --spot
```

Create the fallback TPU v5p node pool:

```bash
gcloud container node-pools create tpu-v5p-nodepool \
  --cluster=$CLUSTER_NAME \
  --region=$REGION \
  --num-nodes=1 \
  --node-version=$CLUSTER_VERSION \
  --node-labels=cloud.google.com/gke-tpu-dra-driver=true \
  --machine-type=$MACHINE_TYPE_V5 \
  --spot
```

Wait a few moments for both node pools to complete provisioning.

---

## **Configure Kubectl to communicate with your cluster**

To configure kubectl to communicate with your newly created cluster, run:

```bash
gcloud container clusters get-credentials ${CLUSTER_NAME} --region=${REGION}
```

---

## **Create Kubernetes Secret for Hugging Face credentials**

> [!NOTE]
> Make sure you have accepted the model license terms on Hugging Face for the `google/gemma-3-1b-it` model before proceeding. Your Hugging Face token must have access to this model.

To create a Kubernetes Secret that contains the Hugging Face token, run:

```bash
kubectl create secret generic hf-secret --from-literal=hf_api_token=${HF_TOKEN} --namespace=${NAMESPACE}
```

---

## **Install the TPU DRA driver**

We install the TPU DRA driver using a Helm chart. Ensure that you have [Helm installed](https://helm.sh/docs/intro/install/) before proceeding.

```bash
helm install dra-driver-google-tpu oci://registry.k8s.io/dra-driver-google/charts/dra-driver-google-tpu \
  --version "0.1.0" \
  --create-namespace --namespace=dra-driver-google-tpu \
  --set kubeletPlugin.priorityClassName="" \
  --set 'kubeletPlugin.tolerations[0].key=google.com/tpu' \
  --set 'kubeletPlugin.tolerations[0].operator=Exists' \
  --set 'kubeletPlugin.tolerations[0].effect=NoSchedule'
```

### Verify that the TPU DRA Driver is working

Verify that the controller and kubelet plugins are deployed and in the `Running` state:

```bash
kubectl get pods -n dra-driver-google-tpu
```

Once the driver starts, it automatically discovers the physical TPU devices across both node pools and publishes them to Kubernetes as **ResourceSlices**. Let's verify that the cluster has registered the TPU `ResourceSlice` objects:

> [!NOTE]
> It might take a minute or two for the driver to fully initialize and publish the `ResourceSlice` after installation.

```bash
kubectl get resourceslices -o yaml
```

You should see representations of the TPU chips under `devices`, with device attributes indicating their generation (e.g., `tpuGen: v6e` and `tpuGen: v5p`).

---

## **Create the Prioritized DRA ResourceClaimTemplate**

To enable automated hardware fallback, we use a `ResourceClaimTemplate` configured with the `firstAvailable` field. This tells the scheduler to evaluate the subrequests in order:
1. **Preferred (`tpu-v6e`):** Requests TPU hardware where `device.attributes["tpu.google.com"].tpuGen == "v6e"`.
2. **Fallback (`tpu-v5p`):** If `v6e` devices are unavailable, requests TPU hardware where `device.attributes["tpu.google.com"].tpuGen == "v5p"`.

> [!IMPORTANT]
> The TPU DRA driver does not currently support TPU subslicing. Therefore, a Pod must claim all TPU chips on a node (`allocationMode: All`).

Inspect the following `claim-template.yaml`:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: tpu-prioritized-claim-template
spec:
  spec:
    devices:
      requests:
      - name: tpus
        firstAvailable:
        - name: tpu-v6e
          deviceClassName: tpu.google.com
          allocationMode: All
          selectors:
          - cel:
              expression: 'device.attributes["tpu.google.com"].tpuGen == "v6e"'
        - name: tpu-v5p
          deviceClassName: tpu.google.com
          allocationMode: All
          selectors:
          - cel:
              expression: 'device.attributes["tpu.google.com"].tpuGen == "v5p"'
```

Apply the manifest to your cluster:

```bash
kubectl apply -f claim-template.yaml --namespace=${NAMESPACE}
```

---

## **Deploy Workloads and Demonstrate Fallback**

### Scenario 1: Deploy Preferred Workload (TPU v6e)

First, we deploy an instance of vLLM running `google/gemma-3-1b-it`. Since our TPU v6e node pool has available capacity, the scheduler will satisfy the primary subrequest in our prioritized list.

Create and inspect `vllm-preferred.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-preferred
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-preferred
  template:
    metadata:
      labels:
        app: vllm-preferred
    spec:
      tolerations:
      - key: "google.com/tpu"
        operator: "Exists"
        effect: "NoSchedule"
      containers:
      - name: vllm-tpu
        image: vllm/vllm-tpu:v0.19.0
        command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
        args:
        - --host=0.0.0.0
        - --port=8000
        - --model=google/gemma-3-1b-it
        - --max-model-len=8192
        env:
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef:
              name: hf-secret
              key: hf_api_token
        ports:
        - containerPort: 8000
        securityContext:
          privileged: true
        resources:
          claims:
          - name: tpus
        volumeMounts:
        - name: dshm
          mountPath: /dev/shm
      volumes:
      - name: dshm
        emptyDir:
          medium: Memory
      resourceClaims:
      - name: tpus
        resourceClaimTemplateName: tpu-prioritized-claim-template

---

apiVersion: v1
kind: Service
metadata:
  name: vllm-preferred-service
spec:
  selector:
    app: vllm-preferred
  type: LoadBalancer
  ports:
    - name: http
      protocol: TCP
      port: 8000
      targetPort: 8000
```

Apply the manifest:

```bash
kubectl apply -f vllm-preferred.yaml --namespace=${NAMESPACE}
```

Verify that the pod is scheduled onto the TPU v6e node:

```bash
kubectl get pods -l app=vllm-preferred -o wide --namespace=${NAMESPACE}
```

View the logs from the running model server to verify the model loaded successfully:

```bash
kubectl logs -l app=vllm-preferred --namespace=${NAMESPACE} --prefix -f
```

You should see something like this once the server is fully ready:

```
(APIServer pid=1) INFO:     Started server process [1]
(APIServer pid=1) INFO:     Waiting for application startup.
(APIServer pid=1) INFO:     Application startup complete.
```

---

### Scenario 2: Verify Fallback Behavior (TPU v5p)

Because our `ResourceClaimTemplate` uses `allocationMode: All`, the first workload (`vllm-preferred`) has fully claimed all available TPU chips on our single TPU v6e node. 

Now, we will deploy a second vLLM workload using the exact same `ResourceClaimTemplate`. When the Kubernetes scheduler evaluates the resource claim for this new pod, it checks the primary `tpu-v6e` subrequest. Seeing that all TPU v6e capacity in the cluster is already allocated, the scheduler automatically evaluates the second option in our `firstAvailable` list and schedules the workload onto the available TPU v5p node.

Create and inspect `vllm-fallback.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-fallback
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-fallback
  template:
    metadata:
      labels:
        app: vllm-fallback
    spec:
      tolerations:
      - key: "google.com/tpu"
        operator: "Exists"
        effect: "NoSchedule"
      containers:
      - name: vllm-tpu
        image: vllm/vllm-tpu:v0.19.0
        command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
        args:
        - --host=0.0.0.0
        - --port=8000
        - --model=google/gemma-3-1b-it
        - --max-model-len=8192
        env:
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef:
              name: hf-secret
              key: hf_api_token
        ports:
        - containerPort: 8000
        securityContext:
          privileged: true
        resources:
          claims:
          - name: tpus
        volumeMounts:
        - name: dshm
          mountPath: /dev/shm
      volumes:
      - name: dshm
        emptyDir:
          medium: Memory
      resourceClaims:
      - name: tpus
        resourceClaimTemplateName: tpu-prioritized-claim-template

---

apiVersion: v1
kind: Service
metadata:
  name: vllm-fallback-service
spec:
  selector:
    app: vllm-fallback
  type: LoadBalancer
  ports:
    - name: http
      protocol: TCP
      port: 8000
      targetPort: 8000
```

Apply the manifest:

```bash
kubectl apply -f vllm-fallback.yaml --namespace=${NAMESPACE}
```

Verify that the fallback pod is successfully scheduled onto the TPU v5p node:

```bash
kubectl get pods -l app=vllm-fallback -o wide --namespace=${NAMESPACE}
```

View the logs from the running model server to verify the model loaded successfully:

```bash
kubectl logs -l app=vllm-fallback --namespace=${NAMESPACE} --prefix -f
```

You should see something like this once the server is fully ready:

```
(APIServer pid=1) INFO:     Started server process [1]
(APIServer pid=1) INFO:     Waiting for application startup.
(APIServer pid=1) INFO:     Application startup complete.
```

---

## **Generate traffic to the models**

We will send requests to both vLLM model servers to verify that both TPU v6e and TPU v5p are successfully processing inference queries.

First, retrieve the external IP addresses of the LoadBalancer services:

> [!NOTE]
> Provisioning external IPs for LoadBalancer services may take a few minutes. If the commands return empty, wait a moment and try again.

```bash
export vllm_preferred_service=$(kubectl get service vllm-preferred-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}' -n ${NAMESPACE})
export vllm_fallback_service=$(kubectl get service vllm-fallback-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}' -n ${NAMESPACE})
```

Send an inference request to the preferred (TPU v6e) service:

```bash
curl http://$vllm_preferred_service:8000/v1/completions \
-H "Content-Type: application/json" \
-d '{
    "model": "google/gemma-3-1b-it",
    "prompt": "Write a short poem about a robot exploring the stars",
    "max_tokens": 100,
    "temperature": 0.7
}'
```

Send an inference request to the fallback (TPU v5p) service:

```bash
curl http://$vllm_fallback_service:8000/v1/completions \
-H "Content-Type: application/json" \
-d '{
    "model": "google/gemma-3-1b-it",
    "prompt": "Write a short poem about a robot exploring the deep sea",
    "max_tokens": 100,
    "temperature": 0.7
}'
```

Receiving successful completion responses from both endpoints confirms that your workloads seamlessly utilized prioritized list allocation across multiple TPU hardware generations!

---

## **Clean Up**

To avoid incurring charges to your Google Cloud account for the resources created in this guide, delete the cluster:

```bash
gcloud container clusters delete ${CLUSTER_NAME} \
  --region=${REGION} \
  --quiet
```
