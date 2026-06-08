---
linkTitle: "TPU-TPU Fungibility with DRA"
title: "TPU-TPU Fungibility using DRA and Custom Compute Classes"
description: "This tutorial guides you through how to achieve TPU-TPU generation-level fallback (TPU v6e to TPU v5e) using Dynamic Resource Allocation (DRA) and GKE Custom Compute Classes (CCC)."
weight: 45
owner:
  - name: "AI on GKE Contributors"
type: docs
tags:
 - TPU
 - DRA
 - Custom Compute Classes
 - Fungibility
draft: false
cloudShell:
    enabled: true
    folder: site/content/docs/tutorials/dynamic-resource-allocation/tpu-prioritized-list
    editorFile: index.md
---

## **Background**

AI and large language model (LLM) serving workloads are traditionally bound to rigid node templates or specific, hardcoded hardware resource requests. If a preferred accelerator (e.g., a node with the newest TPU v6e generation) is temporarily out of stock or bounded by quota limits in the GKE cluster, the scheduling request will fail, leaving workload pods stuck in a `Pending` state—even if healthy fallback hardware (e.g., TPU v5e) is completely idle.

Historically, managing hardware alternatives required maintaining separate deployment configurations, implementing complex affinity rules, or using custom scripting wrappers inside the pod container to map resources at runtime.

This tutorial demonstrates how to achieve true, automated **TPU-TPU Fungibility** on Google Kubernetes Engine (GKE) by combining the power of **Dynamic Resource Allocation (DRA)** and **GKE Custom Compute Classes (CCC)**.

### **How it Works**
We achieve end-to-end coordinated hardware fallback by combining:
1. **Autoscaler-level Prioritization (CCC):** Decides which node pool to scale up first when a pending pod requests resources. Under the shared class label, GKE's Cluster Autoscaler targets our preferred TPU v6e node pool first, falling back to provision the TPU v5e pool only if the preferred pool fails to scale (e.g., due to stockout or quota limits).
2. **Scheduler-level Fallback (DRA):** Matches pods to physical hardware slices dynamically. In the `ResourceClaimTemplate`, the workload declares a list of hardware alternatives using the `firstAvailable` field. Once the GKE autoscaler provisions a node, the Kubernetes scheduler automatically binds the pod's claim to the matching physical TPU slice.

{{% include "/site/content/docs/tutorials/dynamic-resource-allocation/tpu-dra-common.md" %}}

Let’s get started and explore how to set up TPU-TPU fungibility.

---

## **Prepare the Environment**

To set up your environment with Cloud Shell, follow these steps:

1. In the Google Cloud console, click the **Activate Cloud Shell** icon to launch a session in the bottom pane.
2. Set the default environment variables:

```bash
export PROJECT_ID=$(gcloud config get project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
export CLUSTER_NAME=tpu-fungibility-cluster
export LOCATION=us-central1 # Region where regional cluster resides
export ZONE_V6=us-central1-b # Zone where TPU v6e is available
export ZONE_V5=us-central1-a # Zone where TPU v5e is available
export MACHINE_TYPE_V6=ct6e-standard-1t # Preferred TPU v6e VM with 1 TPU chip
export MACHINE_TYPE_V5=ct5lp-hightpu-1t # Fallback TPU v5e VM with 1 TPU chip
export HF_TOKEN=HUGGING_FACE_TOKEN # Replace with your actual Hugging Face token
export CLUSTER_VERSION="1.36" # Must be 1.34.1-gke.1127000 or later
export NAMESPACE=default
```

---

## **Create and configure Google Cloud Resources**

### Create a GKE Cluster

Let's create a GKE cluster that will host our workloads:

```bash
gcloud container clusters create $CLUSTER_NAME \
  --location=$LOCATION \
  --cluster-version=$CLUSTER_VERSION \
  --project=$PROJECT_ID \
  --num-nodes=1 \
  --labels=created-by=ai-on-gke,guide=tpu-fungibility
```

### Create TPU Node Pools with CCC Integration

Next, we create two distinct node pools: one for our preferred TPU v6e hardware and one for our fallback TPU v5e hardware.

> [!NOTE]
> Different TPU generations reside in distinct physical zones within the same Google Cloud region (e.g., in `us-central1`, TPU v6e is physically hosted in `us-central1-b`, whereas TPU v5e resides in `us-central1-a`). To run a multi-generation fallback tutorial, we create a **regional GKE cluster** (`--location=us-central1`), and pin each individual TPU node pool to its specific physical zone using the `--node-locations` argument during pool creation. This is a standard production topology pattern for accelerators on GKE.

To integrate with Custom Compute Classes, we configure a hybrid shape:
* **Preferred Pool (`tpu-v6e-nodepool`):** Set to `--num-nodes=1 --min-nodes=1 --max-nodes=1`. This node pool is *always active with 1 node*, ensuring the initial workload schedules instantly. We cap the pool at a maximum of `1` node so that scaling the deployment triggers GKE's fallback logic.
* **Fallback Pool (`tpu-v5e-nodepool`):** Set to `--num-nodes=0 --min-nodes=0 --max-nodes=1`. This node pool starts at *zero active nodes* to prevent resource waste and save costs, autoscaling up only under pressure.
* **Labels and Taints:** We add the compute class labels and taints to tie both node pools to our fallback class:
  * Label: `cloud.google.com/compute-class=tpu-fallback`
  * Taint: `cloud.google.com/compute-class=tpu-fallback:NoSchedule`

> [!IMPORTANT]
> To allow the TPU DRA driver to manage the TPU hardware, we must disable GKE's default TPU Device Plugin by applying the node label `cloud.google.com/gke-tpu-dra-driver=true` during node pool creation.

> [!NOTE]
> Since TPU resources can be highly sought after, we recommend using the `--spot` flag to request spot capacity. Spot nodes increase your chances of securing a TPU node quickly and cost-effectively.

Create the preferred TPU v6e node pool:

```bash
gcloud container node-pools create tpu-v6e-nodepool \
  --project=$PROJECT_ID \
  --cluster=$CLUSTER_NAME \
  --location=$LOCATION \
  --node-locations=$ZONE_V6 \
  --enable-autoscaling \
  --num-nodes=1 \
  --min-nodes=0 \
  --max-nodes=1 \
  --node-version=$CLUSTER_VERSION \
  --node-labels=cloud.google.com/gke-tpu-dra-driver=true,cloud.google.com/compute-class=tpu-fallback \
  --node-taints=cloud.google.com/compute-class=tpu-fallback:NoSchedule \
  --machine-type=$MACHINE_TYPE_V6 \
  --spot
```

Create the fallback TPU v5e node pool:

```bash
gcloud container node-pools create tpu-v5e-nodepool \
  --project=$PROJECT_ID \
  --cluster=$CLUSTER_NAME \
  --location=$LOCATION \
  --node-locations=$ZONE_V5 \
  --enable-autoscaling \
  --num-nodes=0 \
  --min-nodes=0 \
  --max-nodes=1 \
  --node-version=$CLUSTER_VERSION \
  --node-labels=cloud.google.com/gke-tpu-dra-driver=true,cloud.google.com/compute-class=tpu-fallback \
  --node-taints=cloud.google.com/compute-class=tpu-fallback:NoSchedule \
  --machine-type=$MACHINE_TYPE_V5 \
  --spot
```

Wait a few moments for the node pools to complete creation.

---

## **Configure Kubectl to communicate with your cluster**

To configure kubectl to communicate with your newly created cluster, run:

```bash
gcloud container clusters get-credentials ${CLUSTER_NAME} --location=${LOCATION}
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
  --set 'kubeletPlugin.tolerations[0].effect=NoSchedule' \
  --set 'kubeletPlugin.tolerations[1].key=cloud.google.com/compute-class' \
  --set 'kubeletPlugin.tolerations[1].operator=Exists' \
  --set 'kubeletPlugin.tolerations[1].effect=NoSchedule'
```

### Verify that the TPU DRA driver is working

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

You should see representations of the TPU chips under `devices`, with device attributes indicating their generation (e.g., `tpuGen: v6e` and `tpuGen: v5litepod`).

---

## **Configure GKE Custom Compute Class (CCC)**

We define a `ComputeClass` to instruct GKE's Cluster Autoscaler to prioritize scaling the preferred `tpu-v6e-nodepool` and fallback to the `tpu-v5e-nodepool` under resource limits or stockouts.

Create and inspect `ccc.yaml`:

```yaml
apiVersion: cloud.google.com/v1
kind: ComputeClass
metadata:
  name: tpu-fallback
spec:
  priorities:
  - nodepools:
    - tpu-v6e-nodepool
  - nodepools:
    - tpu-v5e-nodepool
```

Apply the Custom Compute Class to your cluster:

```bash
kubectl apply -f ccc.yaml --namespace=${NAMESPACE}
```

---

## **Create the Prioritized DRA ResourceClaimTemplate**

To enable automated hardware selection at scheduling time, we use a `ResourceClaimTemplate` configured with a prioritized `firstAvailable` list.

Inspect the following `claim-template.yaml`:
* **Preferred subrequest (`tpu-v6e`):** Attempts to match a TPU device where `device.attributes["tpu.google.com"].tpuGen == "v6e"`.
* **Fallback subrequest (`tpu-v5e`):** If no `v6e` devices are available, automatically matches a device where `device.attributes["tpu.google.com"].tpuGen == "v5litepod"`.

> [!NOTE]
> GKE's TPU DRA driver publishes the v5e TPU generation attribute inside `ResourceSlices` as `"v5litepod"` (referencing TPU v5 Lite podslices). The CEL expression selector must target `"v5litepod"` exactly to ensure matching is successful.

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
        - name: tpu-v5e
          deviceClassName: tpu.google.com
          allocationMode: All
          selectors:
          - cel:
              expression: 'device.attributes["tpu.google.com"].tpuGen == "v5litepod"'
```

Apply the manifest to your cluster:

```bash
kubectl apply -f claim-template.yaml --namespace=${NAMESPACE}
```

---

## **Deploy Workloads and Demonstrate Fallback**

Unlike complex multi-container or wrapper setups, our asymmetrical single-chip design allows using a single standard Deployment manifest running a standard, un-wrapped container image!

Notice the details inside `vllm-dra-tpu.yaml`:
* `spec.template.spec.nodeSelector` targets `cloud.google.com/compute-class: tpu-fallback`. GKE's compute class admission controller will automatically inject the corresponding toleration for the compute class taint, steering scheduling.
* `resourceClaims` requests physical TPU device allocation through our dynamic prioritised template.
* Containers request access to the claimed devices via standard resources limits claims, using the default vLLM image.

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
      nodeSelector:
        cloud.google.com/compute-class: tpu-fallback
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

---

### **Scenario 1: Verify Preferred Allocation (TPU v6e)**

Since our preferred TPU v6e node pool was initialized with 1 active node and is completely idle, the workload schedules immediately!

Verify that the single pod is running and inspect which node GKE placed it on:

```bash
kubectl get pods -l app=vllm-dra-tpu -o wide --namespace=${NAMESPACE}
```

Observe the `NODE` column. You will see that the pod is hosted on a node belonging to the **`tpu-v6e-nodepool`** pool. GKE steered node placement using our custom compute class priorities, and the scheduler successfully satisfied the pod's first DRA claim priority (`v6e`).

View the logs to verify vLLM served the Gemma model:

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

### **Scenario 2: Verify Generation Fallback (TPU v5e)**

Since our single TPU v6e node has been fully claimed by the first pod replica, there is no remaining TPU v6e capacity in our preferred node pool.

Now, let's scale the deployment:

```bash
kubectl scale deployment vllm-dra-tpu --replicas=2
```

When the second replica is created:
1. GKE's Cluster Autoscaler detects an unschedulable pending pod and evaluates the target `ComputeClass`.
2. It attempts to scale up the first-priority node pool: `tpu-v6e-nodepool`.
3. However, since the preferred node pool is capped at `max-nodes=1`, the autoscaling request cannot be fulfilled.
4. GKE automatically falls back to the second priority: **`tpu-v5e-nodepool`**.
5. GKE scales up the fallback node pool from **`0` to `1` node** (Spot VM).
6. The new fallback node registers a `v5e` TPU device slice.
7. The Kubernetes scheduler evaluates the pod's claim template: since the preferred choice (`v6e`) has no available capacity, the scheduler matches the second choice (`v5e`) and dynamically maps the pod claim to the newly created physical device slice.
8. **The second replica schedules successfully on the fallback TPU v5e node!**

Wait a few moments for the new node to spin up and the pod to initialize. Verify that both replicas are running and observe their placement:

```bash
kubectl get pods -l app=vllm-dra-tpu -o wide --namespace=${NAMESPACE}
```

Under the `NODE` column:
* The first replica pod is running on a **`tpu-v6e-nodepool`** node.
* The second replica pod is running on a **`tpu-v5e-nodepool`** node!

We achieved fully-automated, multi-generation TPU fungibility across the exact same Deployment layout without changing a single line of workload code!

---

## **Generate traffic to the models**

Let's send requests to both replicas to confirm that both the preferred (v6e) and fallback (v5e) servers are successfully processing queries.

First, retrieve the external IP address of the Service LoadBalancer:

> [!NOTE]
> Provisioning the external IP for the service may take a few minutes. If the command returns empty, wait a moment and try again.

```bash
export vllm_service=$(kubectl get service vllm-dra-tpu-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}' -n ${NAMESPACE})
```

Send 4 parallel inference requests using `curl` in a loop:

```bash
for i in {1..4}; do
  curl http://$vllm_service:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-3-1b-it",
      "prompt": "Write a short poem about the beauty of fallback hardware",
      "max_tokens": 100,
      "temperature": 0.7
  }'
done
```

You should see generated completions. Inspect the logs of both replicas to verify that GKE's load balancer routed requests to both hardware nodes:

```bash
kubectl logs -l app=vllm-dra-tpu --namespace=${NAMESPACE} --prefix
```

Receiving successful outputs confirms that both generations of TPU accelerators are serving traffic seamlessly under a unified workload deployment!

---

## **Clean Up**

To avoid incurring charges to your Google Cloud account for the resources created in this guide, delete the cluster:

```bash
gcloud container clusters delete ${CLUSTER_NAME} \
  --location=${LOCATION} \
  --quiet
```
