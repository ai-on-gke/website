---
linkTitle: "Inference Gateway"
title: "Enable Inference Gateway for VLLM deployments"
description: "Overviews how to set up Inference Gateway for models deployed on VLLM servers"
weight: 30
type: docs
owner:
  - name: "Vlado Djerek"
    link: https://github.com/volatilemolotov
tags:
    - Tutorials
    - Inference Servers
draft: false
---

This guide will show how to set up an [Inference Gateway](https://cloud.google.com/kubernetes-engine/docs/concepts/about-gke-inference-gateway) to serve models from deployed VLLM server

## Overview

This guide will use our [Terraform module](https://github.com/ai-on-gke/common-infra/tree/inference-gateway/common/modules/inference-gateway) in order to set up an Inference Gateway for the application from one of our tutorials.

## Prepare cluster for installing inference gateway

This guide expects that you already have an existing GKE cluster.

1. Install the `InferencePool` and `InferenceModel` Custom Resource Definition (CRDs) in your GKE cluster, run the following command:

```
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v0.3.0/manifests.yaml
```

2. If you are using GKE version earlier than `v1.32.2-gke.1182001` and you want to use Model Armor with GKE Inference Gateway, you must install the traffic and routing extension CRDs:

```
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/gke-gateway-api/refs/heads/main/config/crd/networking.gke.io_gcptrafficextensions.yaml
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/gke-gateway-api/refs/heads/main/config/crd/networking.gke.io_gcproutingextensions.yaml
```

3. Specify the name of the namespace for the `ClusterRole` object that we will create next.  
   For an Autopilot cluster (expected by default) the name has to be `gke-gmp-system`, for Standard cluster \- `gmp-system.`  
 


```
export COLLECTOR_CLUSTER_ROLE_NAMESPACE="gke-gmp-system"
```

4. To set up authorization to scrape metrics, create the inference-gateway-sa-metrics-reader-secret secret and other objects by running this command:

```
kubectl apply -f - <<EOF
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: inference-gateway-metrics-reader
rules:
- nonResourceURLs:
  - /metrics
  verbs:
  - get
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: inference-gateway-sa-metrics-reader
  namespace: default
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: inference-gateway-sa-metrics-reader-role-binding
  namespace: default
subjects:
- kind: ServiceAccount
  name: inference-gateway-sa-metrics-reader
  namespace: default
roleRef:
  kind: ClusterRole
  name: inference-gateway-metrics-reader
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: Secret
metadata:
  name: inference-gateway-sa-metrics-reader-secret
  namespace: default
  annotations:
    kubernetes.io/service-account.name: inference-gateway-sa-metrics-reader
type: kubernetes.io/service-account-token
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: inference-gateway-sa-metrics-reader-secret-read
rules:
- resources:
  - secrets
  apiGroups: [""]
  verbs: ["get", "list", "watch"]
  resourceNames: ["inference-gateway-sa-metrics-reader-secret"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: ${COLLECTOR_CLUSTER_ROLE_NAMESPACE}:collector:inference-gateway-sa-metrics-reader-secret-read
  namespace: default
roleRef:
  name: inference-gateway-sa-metrics-reader-secret-read
  kind: ClusterRole
  apiGroup: rbac.authorization.k8s.io
subjects:
- name: collector
  namespace: ${COLLECTOR_CLUSTER_ROLE_NAMESPACE}
  kind: ServiceAccount
EOF
```

## \[Optional\] Deploy vllm server with models.

\!\[NOTE\] If you do not have an existing model server deployment, you can deploy an example from this section.  If you have a model server deployment, then this section can be skipped.

In this example we use the manifest from the [GKE documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#create-model-deployment) with the base Llama3 model and 2 LoRa models.

1. Create a Kubernetes Secret to store your Hugging Face token. This token is used to access the base model and LoRA adapters. Replace the `YOUR_HF_TOKEN` placeholder with your HuggingFace token::

```
kubectl create secret generic hf-token --from-literal=token=<YOUR_HF_TOKEN>
```

2. Save the following manifest as `vllm-llama3-8b-instruct.yaml`. This manifest defines a Kubernetes Deployment with your model and model server and uses `nvidia-l4` accelerator type:

```
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-llama3-8b-instruct
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-llama3-8b-instruct
  template:
    metadata:
      labels:
        app: vllm-llama3-8b-instruct
    spec:
      containers:
        - name: vllm
          image: "vllm/vllm-openai:v0.8.5"
          imagePullPolicy: Always
          command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
          args:
          - "--model"
          - "meta-llama/Llama-3.1-8B-Instruct"
          - "--tensor-parallel-size"
          - "1"
          - "--port"
          - "8000"
          - "--enable-lora"
          - "--max-loras"
          - "2"
          - "--max-cpu-loras"
          - "12"
          env:
            # Enabling LoRA support temporarily disables automatic v1, we want to force it on
            # until 0.8.3 vLLM is released.
            - name: VLLM_USE_V1
              value: "1"
            - name: PORT
              value: "8000"
            - name: HUGGING_FACE_HUB_TOKEN
              valueFrom:
                secretKeyRef:
                  name: hf-token
                  key: token
            - name: VLLM_ALLOW_RUNTIME_LORA_UPDATING
              value: "true"
          ports:
            - containerPort: 8000
              name: http
              protocol: TCP
          lifecycle:
            preStop:
              # vLLM stops accepting connections when it receives SIGTERM, so we need to sleep
              # to give upstream gateways a chance to take us out of rotation. The time we wait
              # is dependent on the time it takes for all upstreams to completely remove us from
              # rotation. Older or simpler load balancers might take upwards of 30s, but we expect
              # our deployment to run behind a modern gateway like Envoy which is designed to
              # probe for readiness aggressively.
              sleep:
                # Upstream gateway probers for health should be set on a low period, such as 5s,
                # and the shorter we can tighten that bound the faster that we release
                # accelerators during controlled shutdowns. However, we should expect variance,
                # as load balancers may have internal delays, and we don't want to drop requests
                # normally, so we're often aiming to set this value to a p99 propagation latency
                # of readiness -> load balancer taking backend out of rotation, not the average.
                #
                # This value is generally stable and must often be experimentally determined on
                # for a given load balancer and health check period. We set the value here to
                # the highest value we observe on a supported load balancer, and we recommend
                # tuning this value down and verifying no requests are dropped.
                #
                # If this value is updated, be sure to update terminationGracePeriodSeconds.
                #
                seconds: 30
              #
              # IMPORTANT: preStop.sleep is beta as of Kubernetes 1.30 - for older versions
              # replace with this exec action.
              #exec:
              #  command:
              #  - /usr/bin/sleep
              #  - 30
          livenessProbe:
            httpGet:
              path: /health
              port: http
              scheme: HTTP
            # vLLM's health check is simple, so we can more aggressively probe it.  Liveness
            # check endpoints should always be suitable for aggressive probing.
            periodSeconds: 1
            successThreshold: 1
            # vLLM has a very simple health implementation, which means that any failure is
            # likely significant. However, any liveness triggered restart requires the very
            # large core model to be reloaded, and so we should bias towards ensuring the
            # server is definitely unhealthy vs immediately restarting. Use 5 attempts as
            # evidence of a serious problem.
            failureThreshold: 5
            timeoutSeconds: 1
          readinessProbe:
            httpGet:
              path: /health
              port: http
              scheme: HTTP
            # vLLM's health check is simple, so we can more aggressively probe it.  Readiness
            # check endpoints should always be suitable for aggressive probing, but may be
            # slightly more expensive than readiness probes.
            periodSeconds: 1
            successThreshold: 1
            # vLLM has a very simple health implementation, which means that any failure is
            # likely significant,
            failureThreshold: 1
            timeoutSeconds: 1
          # We set a startup probe so that we don't begin directing traffic or checking
          # liveness to this instance until the model is loaded.
          startupProbe:
            # Failure threshold is when we believe startup will not happen at all, and is set
            # to the maximum possible time we believe loading a model will take. In our
            # default configuration we are downloading a model from HuggingFace, which may
            # take a long time, then the model must load into the accelerator. We choose
            # 10 minutes as a reasonable maximum startup time before giving up and attempting
            # to restart the pod.
            #
            # IMPORTANT: If the core model takes more than 10 minutes to load, pods will crash
            # loop forever. Be sure to set this appropriately.
            failureThreshold: 3600
            # Set delay to start low so that if the base model changes to something smaller
            # or an optimization is deployed, we don't wait unnecessarily.
            initialDelaySeconds: 2
            # As a startup probe, this stops running and so we can more aggressively probe
            # even a moderately complex startup - this is a very important workload.
            periodSeconds: 1
            httpGet:
              # vLLM does not start the OpenAI server (and hence make /health available)
              # until models are loaded. This may not be true for all model servers.
              path: /health
              port: http
              scheme: HTTP

          resources:
            limits:
              nvidia.com/gpu: 1
            requests:
              nvidia.com/gpu: 1
          volumeMounts:
            - mountPath: /data
              name: data
            - mountPath: /dev/shm
              name: shm
            - name: adapters
              mountPath: "/adapters"
      initContainers:
        - name: lora-adapter-syncer
          tty: true
          stdin: true
          image: us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/lora-syncer:main
          restartPolicy: Always
          imagePullPolicy: Always
          env:
            - name: DYNAMIC_LORA_ROLLOUT_CONFIG
              value: "/config/configmap.yaml"
          volumeMounts: # DO NOT USE subPath, dynamic configmap updates don't work on subPaths
          - name: config-volume
            mountPath:  /config
      restartPolicy: Always

      # vLLM allows VLLM_PORT to be specified as an environment variable, but a user might
      # create a 'vllm' service in their namespace. That auto-injects VLLM_PORT in docker
      # compatible form as `tcp://<IP>:<PORT>` instead of the numeric value vLLM accepts
      # causing CrashLoopBackoff. Set service environment injection off by default.
      enableServiceLinks: false

      # Generally, the termination grace period needs to last longer than the slowest request
      # we expect to serve plus any extra time spent waiting for load balancers to take the
      # model server out of rotation.
      #
      # An easy starting point is the p99 or max request latency measured for your workload,
      # although LLM request latencies vary significantly if clients send longer inputs or
      # trigger longer outputs. Since steady state p99 will be higher than the latency
      # to drain a server, you may wish to slightly this value either experimentally or
      # via the calculation below.
      #
      # For most models you can derive an upper bound for the maximum drain latency as
      # follows:
      #
      #   1. Identify the maximum context length the model was trained on, or the maximum
      #      allowed length of output tokens configured on vLLM (llama2-7b was trained to
      #      4k context length, while llama3-8b was trained to 128k).
      #   2. Output tokens are the more compute intensive to calculate and the accelerator
      #      will have a maximum concurrency (batch size) - the time per output token at
      #      maximum batch with no prompt tokens being processed is the slowest an output
      #      token can be generated (for this model it would be about 100ms TPOT at a max
      #      batch size around 50)
      #   3. Calculate the worst case request duration if a request starts immediately
      #      before the server stops accepting new connections - generally when it receives
      #      SIGTERM (for this model that is about 4096 / 10 ~ 40s)
      #   4. If there are any requests generating prompt tokens that will delay when those
      #      output tokens start, and prompt token generation is roughly 6x faster than
      #      compute-bound output token generation, so add 20% to the time from above (40s +
      #      16s ~ 55s)
      #
      # Thus we think it will take us at worst about 55s to complete the longest possible
      # request the model is likely to receive at maximum concurrency (highest latency)
      # once requests stop being sent.
      #
      # NOTE: This number will be lower than steady state p99 latency since we stop       receiving
      #       new requests which require continuous prompt token computation.
          # NOTE: The max timeout for backend connections from gateway to model servers should
      #       be configured based on steady state p99 latency, not drain p99 latency
      #
      #   5. Add the time the pod takes in its preStop hook to allow the load balancers have
      #      stopped sending us new requests (55s + 30s ~ 85s)
      #
      # Because the termination grace period controls when the Kubelet forcibly terminates a
      # stuck or hung process (a possibility due to a GPU crash), there is operational safety
      # in keeping the value roughly proportional to the time to finish serving. There is also
      # value in adding a bit of extra time to deal with unexpectedly long workloads.
      #
      #   6. Add a 50% safety buffer to this time since the operational impact should be low
      #      (85s * 1.5 ~ 130s)
      #
      # One additional source of drain latency is that some workloads may run close to
      # saturation and have queued requests on each server. Since traffic in excess of the
      # max sustainable QPS will result in timeouts as the queues grow, we assume that failure
      # to drain in time due to excess queues at the time of shutdown is an expected failure
      # mode of server overload. If your workload occasionally experiences high queue depths
      # due to periodic traffic, consider increasing the safety margin above to account for
      # time to drain queued requests.
      terminationGracePeriodSeconds: 130
      nodeSelector:
        cloud.google.com/gke-accelerator: "nvidia-tesla-a100"
      volumes:
        - name: data
          emptyDir: {}
        - name: shm
          emptyDir:
            medium: Memory
        - name: adapters
          emptyDir: {}
        - name: config-volume
          configMap:
            name: vllm-llama3-8b-adapters
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: vllm-llama3-8b-adapters
data:
  configmap.yaml: |
      vLLMLoRAConfig:
        name: vllm-llama3.1-8b-instruct
        port: 8000
        defaultBaseModel: meta-llama/Llama-3.1-8B-Instruct
        ensureExist:
          models:
          - id: food-review
            source: Kawon/llama3.1-food-finetune_v14_r8
          - id: cad-fabricator
            source: redcathode/fabricator
```

3. Apply the sample manifest to your cluster:

```

kubectl apply -f vllm-llama3-8b-instruct.yaml
```

## Set up Inference Gateway

1. Create a directory for a new terraform config.

```
mkdir <YOUR_PATH>
```

2. Move to a newly created directory:

```
cd <YOUR_PATH>
```

### Apply the Inference Gateway Terraform module

1. Create a directory for the Inference Gateway terraform config. If you came here from another guide, then you should already have this directory created, otherwise, create it now:

```shell
mkdir inference-gateway
cd inference-gateway
```

2. Create terraform files with the following contents:  
     
   File `main.tf`:

```
module "inference-gateway" {
  source = "github.com/ai-on-gke/common-infra//common/modules/inference-gateway?ref=main"

  project_id =  var.project_id
  cluster_name = var.cluster_name
  cluster_location = var.cluster_location
}
```

   File `variables.tf`:

```
variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "cluster_name" {
  type = string
  description = "Name of a target GKE cluster where the target application is deployed"
}

variable "cluster_location" {
  type = string
  description = "Location of a target GKE cluster where the target application is deployed"
}
```

3. Create a tfvars file with the following values.  
     
   \[\!NOTE\] If you came from another guide, then you may already have this file.  
     
   The file must have the following variables:

```
project_id               = "<PROJECT_ID>"
cluster_name             = "<CLUSTER_NAME>"
cluster_location         = "<CLUSTER_LOCATION>"
```

   Where:

   

   * `project_id` \-  The project ID.  
   * `cluster_name` \- Name of a target cluster where app resources are deployed.  
   * `cluster_location` \- Location of a target cluster.

   

   For information about other variables please refer to the `variables.tf` file.

   

4. Init the Terraform config:

```shell
terraform init
```

5. Apply the Terraform config:

```shell
terraform apply -var-file values.tfvars
```

## Cleanup

```shell
terraform destroy -var-file values.tfvars
```
