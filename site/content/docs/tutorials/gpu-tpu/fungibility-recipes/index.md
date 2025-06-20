---
linkTitle: "vLLM GPU/TPU Fungibility"
title: "vLLM GPU/TPU Fungibility"
description: "This tutorial shows you who to serve a large language model (LLM) using both Tensor Processing Units (TPUs) and GPUs on Google Kubernetes Engine (GKE) using the same deployment with [vLLM](https://github.com/vllm-project/vllm)"
weight: 30
type: docs
owner:
  - name: "Edwin Hernandez" 
    link: "https://github.com/Edwinhr716"
tags:
 - Serving
 - Tutorials
 - Inference Servers
cloudShell: 
    enabled: true
    folder: site/content/docs/tutorials/gpu-tpu/fungibility-recipes
    editorFile: index.md
---

## **Background**

This user guide shows you how to optimize AI inference by configuring Google Kubernetes Engine (GKE) to dynamically scale workloads across TPU & GPUs, which helps you better manage demand fluctuations and capacity constraints. In this example we show you how to prioritize high-performance TPU nodes so you deliver optimal speed and responsiveness to your application while ensuring continuous service under peak demand by seamlessly transitioning workloads with additional TPU and GPU nodes, as needed.

### Caveats

1. This deployment could result in having Deployments with a heterogenous set of servers with different performance and operational characteristics.  
2. Time Per Output Token (TPOT) and Time to First Token (TTFT) characteristics could be different between GPU replicas and TPU replicas, although they belong in the same Deployment.  
3. TPU servers and GPU servers exhibit different failure characteristics and special attention needs to be paid to these Deployments.  
4. Since the TPU type is hardcoded as an environment variable, this yaml is not able to be fungible between different generations of TPUs (ie v5e to v6e)  
5. Users should ensure they have access to the TPUs they need for scaling purposes. If there is a GCE_STOCKOUT error when provisioning TPUs, it could take up to 10 hours to fall back from a TPU node to a GPU. Users can work around this issue by 1) limiting autoscaling to the number of TPUs nodes they have access to or 2) falling back from GPUs to TPUs. We plan to remove this limitation in the future.

## **Prepare the Environment**

To set up your environment with Cloud Shell, follow these steps:

1. In the Google Cloud console, launch a Cloud Shell session by clicking Cloud Shell activation icon Activate Cloud Shell in the Google Cloud console. This launches a session in the bottom pane of Google Cloud console.  
2. Set the default environment variables:

```bash
gcloud config set project PROJECT_ID
export PROJECT_ID=$(gcloud config get project)
export CLUSTER_NAME=vllm-fungibility
export LOCATION=<location-with-v6e> See https://cloud.google.com/tpu/docs/regions-zones
export CLUSTER_VERSION=<GKE version that supports Trillium, 1.31.4-gke.1256000+>
export REGION_NAME=REGION_NAME
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
export COMPUTE_CLASS=vllm-fallback
export HF_TOKEN=HUGGING_FACE_TOKEN
```

## **Create and configure Google Cloud Resources**

### Create a GKE Cluster

```bash
gcloud container clusters create $CLUSTER_NAME \
--location=$LOCATION \
--cluster-version=$VERSION \
--project=$PROJECT_ID \
--labels=created-by=ai-on-gke,guide=fungibility-recipes
```

### Create v6e TPU, L4 Preemptible, and L4 on demand node pools

All node pools will have autoscaling enabled in order to demonstrate that [custom compute class (CCC)](https://cloud.google.com/kubernetes-engine/docs/concepts/about-custom-compute-classes) is able to autoscale any type of node pool. We will also add a label and taint with the CCC name so that it can be used in the priority list.

Create a TPU v6e-1 [Spot](https://cloud.google.com/kubernetes-engine/docs/concepts/spot-vms) node pool:

```bash
gcloud container node-pools create v6e-1-spot \
	--location=$LOCATION \
	--num-nodes=0 \
	--machine-type=ct6e-standard-1t \
	--cluster=$CLUSTER_NAME \
	--node-labels=cloud.google.com/compute-class=$COMPUTE_CLASS \
	--node-taints=cloud.google.com/compute-class=$COMPUTE_CLASS:NoSchedule \
	--enable-autoscaling \
	--min-nodes=0 \
	--max-nodes=2 \
  --spot
```

Create a GPU L4 node pool:

```bash
gcloud container node-pools create l4 \
	--cluster=$CLUSTER_NAME \
	--location=$LOCATION \
	--num-nodes=1 \
	--machine-type "g2-standard-4" \
	--accelerator "type=nvidia-l4,gpu-driver-version=LATEST" \
	--node-labels=cloud.google.com/compute-class=$COMPUTE_CLASS \
	--node-taints=cloud.google.com/compute-class=$COMPUTE_CLASS:NoSchedule \
	--enable-autoscaling \
	--min-nodes=1 \
	--max-nodes=2
```

## **Configure Kubectl to communicate with your cluster**
To configure kubectl to communicate with your cluster, run the following command:

```bash
  gcloud container clusters get-credentials ${CLUSTER_NAME} --region=${REGION}
```

## **Create Kubernetes Secret for Hugging Face credentials**
To create a Kubernetes Secret that contains the Hugging Face token, run the following command:

```bash
kubectl create secret generic hf-secret --from-literal=hf_api_token=${HF_TOKEN}
```


## **Setup Custom Compute Class**

Inspect the following `ccc.yaml`, where we define the priority order of the nodepools.  l4 scales up first, and v6e-1-spot scales up last.

```yaml
apiVersion: cloud.google.com/v1
kind: ComputeClass
metadata:
  name: vllm-fallback
spec:
  priorities:
  - nodepools: [l4]
  - nodepools: [v6e-1-spot]
```

Apply the manifest

```bash
kubectl apply -f ccc.yaml
```

## **Build the vLLM Fungibility images**

We need a bash script that determines what type of hardware is present in the machine before starting up the vLLM server so that  if the machine has TPUs, the TPU container will start the server, while the GPU container sleeps, and vice versa.

### Build the TPU Image

Inspect the `tpu_entrypoint.sh` and `tpu-image.Dockerfile` files:

```bash
#!/usr/bin/env bash

if ! [ -c /dev/vfio/0 ]; then
    echo "machine doesn't contain TPU machines, shutting down container"
    while true; do sleep 10000; done
fi

python3 -m vllm.entrypoints.openai.api_server $@
```

```docker
FROM docker.io/vllm/vllm-tpu:2e33fe419186c65a18da6668972d61d7bbc31564
COPY tpu_entrypoint.sh /vllm-workspace/tpu_entrypoint.sh
RUN chmod +x /vllm-workspace/tpu_entrypoint.sh
ENTRYPOINT [ "/vllm-workspace/tpu_entrypoint.sh" ]
```

Build the image

```bash
docker build -f tpu-image.Dockerfile . -t vllm-tpu
```

Push the TPU image to the Artifact Registry

```bash
gcloud artifacts repositories create vllm --repository-format=docker --location=$REGION_NAME && \
gcloud auth configure-docker $REGION_NAME-docker.pkg.dev && \
docker image tag vllm-tpu $REGION_NAME-docker.pkg.dev/$PROJECT_ID/vllm/vllm-fungibility:TPU && \
docker push $REGION_NAME-docker.pkg.dev/$PROJECT_ID/vllm/vllm-fungibility:TPU
```

### Build the GPU Image

Inspect the `gpu_entrypoint.sh` and `gpu-image.Dockerfile` files:

```bash
#!/usr/bin/env bash

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "machine doesn't contain GPU machines, shutting down container"
    sleep 9999 & wait
fi

python3 -m vllm.entrypoints.openai.api_server $@
```

```docker
FROM docker.io/vllm/vllm-openai:latest
COPY gpu_entrypoint.sh /vllm-workspace/gpu_entrypoint.sh
RUN chmod +x /vllm-workspace/gpu_entrypoint.sh
ENTRYPOINT [ "/vllm-workspace/gpu_entrypoint.sh" ]
```

Build the image

```bash
docker build -f gpu-image.Dockerfile . -t vllm-gpu
```

Push the GPU image to the Artifact Registry

```bash
docker image tag vllm-gpu $REGION_NAME-docker.pkg.dev/$PROJECT_ID/vllm/vllm-fungibility:GPU && \
docker push $REGION_NAME-docker.pkg.dev/$PROJECT_ID/vllm/vllm-fungibility:GPU
```

## **Deploy the vLLM Server**

Inspect the following manifest `vllm.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
 name: vllm
spec:
 replicas: 1
 selector:
   matchLabels:
     app: vllm
 template:
   metadata:
     labels:
       app: vllm
   spec:
     nodeSelector:
       cloud.google.com/compute-class: vllm-fallback
     containers:
     - name: vllm-gpu
       image: $REGION_NAME-docker.pkg.dev/$PROJECT_ID/vllm/vllm-fungibility:GPU
       args:
       - --host=0.0.0.0
       - --port=8000
       - --tensor-parallel-size=1
       - --model=Qwen/Qwen2-1.5B
       securityContext:
         privileged: true
       env:
       - name: HUGGING_FACE_HUB_TOKEN
         valueFrom:
           secretKeyRef:
             name: hf-secret
             key: hf_api_token
       volumeMounts:
       - name: gpu
         mountPath: /usr/local/nvidia
       resources:
         limits:
           ephemeral-storage: 43Gi
     - name: vllm-tpu
       securityContext:
         privileged: true
       image: $REGION_NAME-docker.pkg.dev/$PROJECT_ID/vllm/vllm-fungibility:TPU
       args:
       - --host=0.0.0.0
       - --port=8000
       - --max-model-len=8192
       - --model=Qwen/Qwen2-1.5B
       env:
       - name: HUGGING_FACE_HUB_TOKEN
         valueFrom:
           secretKeyRef:
             name: hf-secret
             key: hf_api_token
       - name: TPU_TOPOLOGY
         value: 1x1
       - name: TPU_WORKER_ID
         value: "0"
       - name: TPU_SKIP_MDS_QUERY
         value: "true"
       - name: TPU_TOPOLOGY_WRAP
         value: "false,false,false"
       - name: TPU_CHIPS_PER_HOST_BOUNDS
         value: "1,1,1"
       - name: TPU_ACCELERATOR_TYPE
         value: v6e-1
       - name: TPU_RUNTIME_METRICS_PORTS
         value: "8431"
       - name: TPU_TOPOLOGY_ALT
         value: "false"
       - name: CHIPS_PER_HOST_BOUNDS
         value: "1,1,1"
       - name: TPU_WORKER_HOSTNAMES
         value: localhost
       - name: HOST_BOUNDS
         value: "1,1,1"
       - name: TPU_HOST_BOUNDS
         value: "1,1,1"
       - name: NODE_IP
         valueFrom:
           fieldRef:
             fieldPath: status.hostIP
       - name: VBAR_CONTROL_SERVICE_URL
         value: $(NODE_IP):8353
     volumes:
     - name: gpu
       hostPath:
         path: /home/kubernetes/bin/nvidia
         type: DirectoryOrCreate
     tolerations:
     - key: "nvidia.com/gpu"
       operator: "Exists"
       effect: "NoSchedule"
     - key: "google.com/tpu"
       operator: "Exists"
       effect: "NoSchedule"

---

apiVersion: v1
kind: Service
metadata:
 name: vllm-service
spec:
 selector:
   app: vllm
 type: LoadBalancer
 ports:
   - name: http
     protocol: TCP
     port: 8000
     targetPort: 8000


```

Apply the manifest by running the following command: 

```bash
kubectl apply -f vllm.yaml
```

View the logs from the running model server:

```bash
kubectl logs -f -l app=vllm
```

The output should be similar to the following:

```
INFO 09-20 19:03:48 launcher.py:19] Available routes are:
INFO 09-20 19:03:48 launcher.py:27] Route: /openapi.json, Methods: GET, HEAD
INFO 09-20 19:03:48 launcher.py:27] Route: /docs, Methods: GET, HEAD
INFO 09-20 19:03:48 launcher.py:27] Route: /docs/oauth2-redirect, Methods: GET, HEAD
INFO 09-20 19:03:48 launcher.py:27] Route: /redoc, Methods: GET, HEAD
INFO 09-20 19:03:48 launcher.py:27] Route: /health, Methods: GET
INFO 09-20 19:03:48 launcher.py:27] Route: /tokenize, Methods: POST
INFO 09-20 19:03:48 launcher.py:27] Route: /detokenize, Methods: POST
INFO 09-20 19:03:48 launcher.py:27] Route: /v1/models, Methods: GET
INFO 09-20 19:03:48 launcher.py:27] Route: /version, Methods: GET
INFO 09-20 19:03:48 launcher.py:27] Route: /v1/chat/completions, Methods: POST
INFO 09-20 19:03:48 launcher.py:27] Route: /v1/completions, Methods: POST
INFO 09-20 19:03:48 launcher.py:27] Route: /v1/embeddings, Methods: POST
INFO:     Started server process [1]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
```

## **Setup the autoscaling configuration**

Follow this portion of the guide to set up HPA (Horizontal Pod Autoscaling) using custom Prometheus metrics via Google Managed Prometheus (GMP)  metrics from the vLLM server. 

### Deploy the custom stackdriver metrics adapter 

Run the following command to set up the custom stackdriver metrics adapter on your cluster. Note you need to have Kubernetes Engine Cluster Administrator and Kubernetes Engine Administrator in order to run this.

```bash
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/k8s-stackdriver/master/custom-metrics-stackdriver-adapter/deploy/production/adapter_new_resource_model.yaml
```

### Deploy a PodMonitoring spec to set up prometheus metric scraping 

Now deploy a Pod Monitoring spec for the prometheus metrics scraper. Refer to [https://cloud.google.com/stackdriver/docs/managed-prometheus/setup-managed](https://cloud.google.com/stackdriver/docs/managed-prometheus/setup-managed) for more details on Google Managed Prometheus and setup. This should be enabled by default on the GKE cluster though. Save the following yaml as vllm\_pod\_monitor.yaml

```yaml
apiVersion: monitoring.googleapis.com/v1
kind: PodMonitoring
metadata:
 name: vllm-pod-monitoring
spec:
 selector:
   matchLabels:
     app: vllm
 endpoints:
 - path: /metrics
   port: 8000
   interval: 15s
```

Apply it to the cluster by running the following command: 

```bash
kubectl apply -f vllm_pod_monitor.yaml 
```

With this configuration, GMP is now configured to scrap vLLM server metrics and the metrics should now be visible to the HPA controller. Let’s introduce some load to the vLLM server and deploy `vllm-hpa.yaml` to see how autoscaling with custom vLLM metrics works.

## Create some load on the vLLM endpoint

Create and run the following bash script (`load.sh`) which will send N number of parallel requests to the vLLM endpoint:

```bash
#!/bin/bash
N=1000  # Replace with the desired number of parallel processes
export vllm_service=$(kubectl get service vllm-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
for i in $(seq 1 $N); do
  while true; do
    curl http://$vllm_service:8000/v1/completions -H "Content-Type: application/json" -d '{"model": "Qwen/Qwen2-1.5B", "prompt": "Write a story about san francisco", "max_tokens": 100, "temperature": 0}'
  done &  # Run in the background
done
wait
```

```bash
nohup ./load.sh &
```

### Deploy the HPA Configuration

Now, create a HPA configuration yaml file `vllm-hpa.yaml` and apply it to the cluster. vLLM metrics in GMP are in the format of `vllm:<metric name>`. We will use `num_requests_waiting` which we recommend for scaling throughput. Alternatively, you could use `gpu_cache_usage_perc` for latency sensitive use cases. Despite the naming convention, this metric works for TPU as well.

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
 name: vllm-hpa
spec:
 scaleTargetRef:
   apiVersion: apps/v1
   kind: Deployment
   name: vllm
 minReplicas: 1
 maxReplicas: 4
 metrics:
   - type: Pods
     pods:
       metric:
         name: prometheus.googleapis.com|vllm:num_requests_waiting|gauge
       target:
         type: AverageValue
         averageValue: 1
```

Deploy the Horizontal Pod Autoscaler configuration by running the following command:

```bash
 kubectl apply -f vllm-hpa.yaml 
```

GKE schedules another Pod to deploy, which triggers the node pool autoscaler to add a second node before it deploys the second vLLM replica.

Watch the progress of the Pod autoscaling: 

```bash
kubectl get hpa --watch
```

The output should be similar to the following: 

```
NAME       REFERENCE             TARGETS       MINPODS   MAXPODS   REPLICAS   AGE
vllm-hpa   Deployment/vllm       <unknown>/1   1         6         0          6s
vllm-hpa   Deployment/vllm       34972m/1      1         6         1          16s
vllm-hpa   Deployment/vllm       25112m/1      1         6         2          31s
vllm-hpa   Deployment/vllm       35301m/1      1         6         2          46s
vllm-hpa   Deployment/vllm       25098m/1      1         6         3          62s
vllm-hpa   Deployment/vllm       35348m/1      1         6         3          77s
```

## **Clean up** 

### Delete the deployed resources:

To avoid incurring charges to your Google Cloud account for the resources that you created in this guide, run the following commands:

Stop the bash script that simulates load:

```bash
ps -ef | grep load.sh | awk '{print $2}' | xargs -n1 kill -9
```

Delete the cluster:

```bash
gcloud container clusters delete ${CLUSTER_NAME} \
  --location=${ZONE}
```
