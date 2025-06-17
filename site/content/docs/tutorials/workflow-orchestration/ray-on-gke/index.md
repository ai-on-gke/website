---
linkTitle: "Ray"
title: "Ray on GKE"
description: "This guide provides instructions and examples for deploying and managing Ray clusters on Google Kubernetes Engine (GKE) using KubeRay and Terraform. It covers setting up a GKE cluster, deploying a Ray cluster, submitting Ray jobs, and using the Ray Client for interactive sessions. The guide also points to various resources, including tutorials, best practices, and examples for running different types of Ray applications on GKE, such as serving LLMs, using TPUs, and integrating with GCS."
weight: 30
type: docs
owner:
  - name: "Francisco Cabrera"
    link: "https://github.com/fcabrera23"
tags: 
    - Ray on GKE
    - Blueprints
draft: false
cloudShell: 
    enabled: true
    folder: site/content/docs/tutorials/workflow-orchestration/ray-on-gke
    editorFile: index.md
---
This directory contains examples, guides and best practices for running [Ray](https://www.ray.io/) on Google Kubernetes Engine.
Most examples use the [`ray-on-gke`](https://github.com/ai-on-gke/quick-start-guides/tree/main/ray-on-gke) terraform module to install KubeRay and deploy RayCluster resources.

## Getting Started

It is highly recommended to use the [infrastructure](https://github.com/ai-on-gke/common-infra/tree/main/common/infrastructure) terraform module to create your GKE cluster.

### Create a RayCluster on a GKE cluster

1. Clone the [`quick-start-guides`](https://github.com/ai-on-gke/quick-start-guides/) repository.
    ```bash
    git clone https://github.com/ai-on-gke/quick-start-guides.git
    ```

1. Edit `ray-on-gke/workloads.tfvars` with your environment specific variables and configurations.
    The following variables require configuration:
    * project_id
    * cluster_name
    * cluster_location

    If you need a new cluster, you can specify `create_cluster: true`.

1. Run the following commands to install KubeRay and deploy a Ray cluster onto your existing cluster.
    ```bash
    cd ray-on-gke/
    terraform init
    terraform apply --var-file=workloads.tfvars
    ```

1. Validate that the RayCluster is ready:
    ```bash
    $ kubectl get raycluster
    NAME                  DESIRED WORKERS   AVAILABLE WORKERS   STATUS   AGE
    ray-cluster-kuberay   1                 1                   ready    3m41s
    ```

>[!NOTE]
> See [tfvars examples](https://github.com/ai-on-gke/quick-start-guides/tree/main/ray-on-gke/tfvars_examples) to explore different configuration options for the Ray cluster using the [terraform templates](https://github.com/ai-on-gke/quick-start-guides/tree/main/ray-on-gke).

### Install Ray

Ensure Ray is installed in your environment. See [Installing Ray](https://docs.ray.io/en/latest/ray-overview/installation.html) for more details.

### Submit a Ray job

1. To submit a Ray job, first establish a connection to the Ray head. For this example we'll use `kubectl port-forward`
to connect to the Ray head via localhost.

    ```bash
    kubectl -n ai-on-gke port-forward service/ray-cluster-kuberay-head-svc 8265 &
    ```

1. Submit a Ray job that prints resources available in your Ray cluster:
    ```bash
    $ ray job submit --address http://localhost:8265 -- python -c "import ray; ray.init(); print(ray.cluster_resources())"
    Job submission server address: http://localhost:8265
    
    -------------------------------------------------------
    Job 'raysubmit_4JBD9mLhh9sjqm8g' submitted successfully
    -------------------------------------------------------
    
    Next steps
      Query the logs of the job:
        ray job logs raysubmit_4JBD9mLhh9sjqm8g
      Query the status of the job:
        ray job status raysubmit_4JBD9mLhh9sjqm8g
      Request the job to be stopped:
        ray job stop raysubmit_4JBD9mLhh9sjqm8g
    
    Tailing logs until the job exits (disable with --no-wait):
    2024-03-19 20:46:28,668 INFO worker.py:1405 -- Using address 10.80.0.19:6379 set in the environment variable RAY_ADDRESS
    2024-03-19 20:46:28,668 INFO worker.py:1540 -- Connecting to existing Ray cluster at address: 10.80.0.19:6379...
    2024-03-19 20:46:28,677 INFO worker.py:1715 -- Connected to Ray cluster. View the dashboard at 10.80.0.19:8265
    {'node:__internal_head__': 1.0, 'object_store_memory': 2295206707.0, 'memory': 8000000000.0, 'CPU': 4.0, 'node:10.80.0.19': 1.0}
    Handling connection for 8265
    
    ------------------------------------------
    Job 'raysubmit_4JBD9mLhh9sjqm8g' succeeded
    ------------------------------------------
    ```

### Ray Client for interactive sessions

The RayClient API enables Python scripts to interactively connect to remote Ray clusters. See [Ray Client](https://docs.ray.io/en/latest/cluster/running-applications/job-submission/ray-client.html) for more details.

1. To use the client, first establish a connection to the Ray head. For this example we'll use `kubectl port-forward`
to connect to the Ray head Service via localhost.

    ```bash
    kubectl -n ai-on-gke port-forward service/ray-cluster-kuberay-head-svc 10001 &
    ```

1. Next, define a Python script containing remote code you want to run on your Ray cluster. Similar to the previous example,
this remote function will print the resources available in the cluster:
    ```python
    # cluster_resources.py
    import ray
    
    ray.init("ray://localhost:10001")
    
    @ray.remote
    def cluster_resources():
      return ray.cluster_resources()
    
    print(ray.get(cluster_resources.remote()))
    ```

1. Run the Python script:
    ```bash
    $ python cluster_resources.py
    {'CPU': 4.0, 'node:__internal_head__': 1.0, 'object_store_memory': 2280821145.0, 'node:10.80.0.22': 1.0, 'memory': 8000000000.0}
    ```

## Guides & Tutorials

See the following guides and tutorials for running Ray applications on GKE:
* [Getting Started with KubeRay](https://docs.ray.io/en/latest/cluster/kubernetes/getting-started.html)
* [Serve an LLM on L4 GPUs with Ray](https://cloud.google.com/kubernetes-engine/docs/how-to/serve-llm-l4-ray)
* [TPU Guide](https://github.com/ai-on-gke/kuberay-tpu-webhook)
* [Priority Scheduling with RayJob and Kueue](https://docs.ray.io/en/master/cluster/kubernetes/examples/rayjob-kueue-priority-scheduling.html)
* [Gang Scheduling with RayJob and Kueue](https://docs.ray.io/en/master/cluster/kubernetes/examples/rayjob-kueue-gang-scheduling.html)
* [Configuring KubeRay to use Google Cloud Storage Buckets in GKE](https://docs.ray.io/en/latest/cluster/kubernetes/user-guides/gke-gcs-bucket.html)
* [Example templates for Ray clusterse](https://github.com/ai-on-gke/quick-start-guides/tree/main/ray-on-gke/tfvars_examples)

## Blogs & Best Practices

* [Getting started with Ray on Google Kubernetes Engine](https://cloud.google.com/blog/products/containers-kubernetes/use-ray-on-kubernetes-with-kuberay)
* [Why GKE for your Ray AI workloads?](https://cloud.google.com/blog/products/containers-kubernetes/the-benefits-of-using-gke-for-running-ray-ai-workloads)
* [Advanced scheduling for AI/ML with Ray and Kueue](https://cloud.google.com/blog/products/containers-kubernetes/using-kuberay-and-kueue-to-orchestrate-ray-applications-in-gke)
* [How to secure Ray on Google Kubernetes Engine](https://cloud.google.com/blog/products/containers-kubernetes/securing-ray-to-run-on-google-kubernetes-engine)
* [4 ways to reduce cold start latency on Google Kubernetes Engine](https://cloud.google.com/blog/products/containers-kubernetes/tips-and-tricks-to-reduce-cold-start-latency-on-gke)
