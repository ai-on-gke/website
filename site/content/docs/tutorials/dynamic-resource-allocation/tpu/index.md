---
linkTitle: "TPUs with DRA"
title: "Dynamic Resource Allocation of TPUs"
description: "This tutorial guides you through how to dynamically allocate Google Cloud TPUs using Dynamic Resource Allocation (DRA) on Google Kubernetes Engine (GKE)."
weight: 40
owner:
  - name: "AI on GKE Contributors"
    link: "https://github.com/ai-on-gke"
type: docs
tags:
 - TPU
 - DRA
draft: false
cloudShell:
    enabled: true
    folder: site/content/docs/tutorials/dynamic-resource-allocation/tpu
    editorFile: index.md
---

## **Background**

This tutorial guides you through how to use **Dynamic Resource Allocation (DRA)** to provision and consume Tensor Processing Units (TPUs) on Google Kubernetes Engine (GKE). This guide covers basic TPU allocation, with separate guides covering advanced scheduling features like prioritized allocation.

{{% include "/site/content/docs/tutorials/dynamic-resource-allocation/tpu-dra-common.md" %}}

Let’s get started and explore how to allocate TPUs with DRA.

---

## **Prepare the Environment**

To set up your environment with Cloud Shell, follow these steps:

1. In the Google Cloud console, click the **Activate Cloud Shell** icon to launch a session in the bottom pane.
2. Set the default environment variables:

```bash
export PROJECT_ID=$(gcloud config get project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
export CLUSTER_NAME=tpu-dra-cluster
export LOCATION=us-central1-b # Choose a zone where TPU v6e is available
export MACHINE_TYPE=ct6e-standard-1t # TPU v6e virtual machine with 1 TPU chip
export HF_TOKEN=HUGGING_FACE_TOKEN # Replace with your actual Hugging Face token
export CLUSTER_VERSION="1.36" # Must be 1.34 or later
export NAMESPACE=default
```

---

## **Create and configure Google Cloud Resources**

### Create a GKE Cluster

Let's create a GKE cluster that will host our DRA-based TPU workloads:

```bash
gcloud container clusters create $CLUSTER_NAME \
  --location=$LOCATION \
  --cluster-version=$CLUSTER_VERSION \
  --project=$PROJECT_ID \
  --num-nodes=1 \
  --labels=created-by=ai-on-gke,guide=tpu-dra
```

### Create a TPU Node Pool configured for DRA

Next, we create a node pool using TPU v6e (`ct6e-standard-1t`).

> [!IMPORTANT]
> To allow the TPU DRA driver to manage the TPU hardware, we must disable GKE's default TPU Device Plugin. We do this by applying the node label `cloud.google.com/gke-tpu-dra-driver=true` during node pool creation.

> [!IMPORTANT]
> Since TPU resources can be highly sought after, we recommend using the `--spot` flag to request spot capacity. Spot nodes increase your chances of securing a TPU node quickly and cost-effectively.

```bash
gcloud container node-pools create tpu-nodepool \
  --cluster=$CLUSTER_NAME \
  --location=$LOCATION \
  --num-nodes=1 \
  --node-version=$CLUSTER_VERSION \
  --node-labels=cloud.google.com/gke-tpu-dra-driver=true \
  --machine-type=$MACHINE_TYPE \
  --spot
```

Wait a few moments for the node pool provisioning to complete.

---

## **Configure Kubectl to communicate with your cluster**

To configure kubectl to communicate with your newly created cluster, run:

```bash
gcloud container clusters get-credentials ${CLUSTER_NAME} --location=${LOCATION}
```

---

## **Create Kubernetes Secret for Hugging Face credentials**

> [!IMPORTANT]
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

### Verify that the TPU DRA driver is working

Verify that the controller and kubelet plugins are deployed and in the `Running` state:

```bash
kubectl get pods -n dra-driver-google-tpu
```

Once the driver starts, it automatically discovers the physical TPU devices and publishes them to Kubernetes as **ResourceSlices**. Let's verify that the cluster has registered the TPU `ResourceSlice` objects:

> [!IMPORTANT]
> It might take a minute or two for the driver to fully initialize and publish the `ResourceSlice` after installation.

```bash
kubectl get resourceslices -o yaml
```

You should see a representation of the TPU chips under `devices`.

---

## **Create the DRA ResourceClaimTemplate**

To claim TPU hardware dynamically, we use a `ResourceClaimTemplate`. This template defines the type of device class we are requesting (in this case, `tpu.google.com`). When a pod references this template, GKE dynamically generates a `ResourceClaim` to allocate the hardware.

> [!IMPORTANT]
> The TPU DRA driver does not currently support allocating a subset of TPU chips on a node. Therefore, a Pod must claim all TPU chips on a node.

Inspect the following `claim-template.yaml`:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: tpu-claim-template
spec:
  spec:
    devices:
      requests:
      - name: tpus
        exactly:
          deviceClassName: tpu.google.com
          allocationMode: All
```

Apply the manifest to your cluster:

```bash
kubectl apply -f claim-template.yaml --namespace=${NAMESPACE}
```

---

## **Deploy the vLLM Workload**

Now we deploy a real workload using **vLLM** to run inference on the TPU. We create a Deployment that runs a replica of the `google/gemma-3-1b-it` model. We reference our `ResourceClaimTemplate` in the pod spec to dynamically obtain TPU resources.

Notice the DRA syntax in the Pod spec:
* `spec.resourceClaims` defines the resource claim based on our `ResourceClaimTemplate`.
* `resources.claims` in the container spec links the container to the requested TPU hardware.

Create and inspect `vllm-dra-tpu.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-dra-tpu
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-dra-tpu
  template:
    metadata:
      labels:
        app: vllm-dra-tpu
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
        resourceClaimTemplateName: tpu-claim-template

---

apiVersion: v1
kind: Service
metadata:
  name: vllm-dra-tpu-service
spec:
  selector:
    app: vllm-dra-tpu
  type: LoadBalancer
  ports:
    - name: http
      protocol: TCP
      port: 8000
      targetPort: 8000
```

Apply the manifest:

```bash
kubectl apply -f vllm-dra-tpu.yaml --namespace=${NAMESPACE}
```

View the logs from the running model server to verify the model loaded successfully:

```bash
kubectl logs -l app=vllm-dra-tpu --namespace=${NAMESPACE} --prefix -f
```

You should see something like this once the server is fully ready:

```
(APIServer pid=1) INFO:     Started server process [1]
(APIServer pid=1) INFO:     Waiting for application startup.
(APIServer pid=1) INFO:     Application startup complete.
```

---

## **Generate traffic to the model**

We will send a request to the vLLM model server to verify that the TPU is processing inference queries.

First, retrieve the external IP address of the LoadBalancer service:

> [!IMPORTANT]
> Provisioning the external IP for the `vllm-dra-tpu-service` LoadBalancer may take a few minutes. If the command returns empty, wait a moment and try again.

```bash
export vllm_service=$(kubectl get service vllm-dra-tpu-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}' -n ${NAMESPACE})
```

Send an inference request using `curl`:

```bash
curl http://$vllm_service:8000/v1/completions \
-H "Content-Type: application/json" \
-d '{
    "model": "google/gemma-3-1b-it",
    "prompt": "Write a short poem about a robot learning to paint",
    "max_tokens": 100,
    "temperature": 0.7
}'
```

You should see a generated completion response back from the model server, indicating that vLLM successfully utilized the dynamically allocated GKE TPUs via DRA!

---

## **Clean Up**

To avoid incurring charges to your Google Cloud account for the resources created in this guide, delete the cluster:

```bash
gcloud container clusters delete ${CLUSTER_NAME} \
  --location=${LOCATION} \
  --quiet
```
