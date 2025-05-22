---
linkTitle: "65k-nodes-benchmark"
title: "GKE at 65,000 Nodes: Simulated AI Workload Benchmark"
description: "This guide outlines the process of benchmarking a 65,000-node Google Kubernetes Engine (GKE) cluster using CPU-only machines to simulate AI workloads and evaluate the Kubernetes control plane's performance. It details how to deploy the cluster with Terraform, run diverse simulated AI workloads (including training and inference) using ClusterLoader2, and collect performance metrics to assess scalability and stability. The benchmark results provide insights into pod state transitions, scheduling throughput, and API server latency under extreme load, allowing for a comprehensive evaluation of the control plane's capabilities."
weight: 30
owner: 
  - name: "Besher Massri"
    link: "https://github.com/besher-massri"
type: docs
tags:
 - Benchmarking
 - 65,000-nodes
 - AI
draft: false
cloudShell: 
    enabled: true
    folder: site/content/docs/benchmarking/cpu-based-benchmark
    editorFile: index.md
---

This guide describes the benchmark of Google Kubernetes Engine (GKE) at a massive scale (65,000 nodes) with simulated AI workloads, using Terraform for infrastructure automation and ClusterLoader2 for performance testing.

The findings from this benchmark were published on the [Google Cloud Blog](https://cloud.google.com/blog/products/containers-kubernetes/benchmarking-a-65000-node-gke-cluster-with-ai-workloads).

## Introduction

This benchmark simulates mixed AI workloads, specifically AI training and AI inference, on a 65,000-node GKE cluster. It focuses on evaluating the performance and scalability of the Kubernetes control plane under demanding conditions, characterized by a high number of nodes and dynamic workload changes.

To achieve this efficiently and cost-effectively, the benchmark uses **CPU-only** machines and simulates the behavior of AI workloads with simple containers. This approach allows for stress-testing the Kubernetes control plane without the overhead and complexity of managing actual AI workloads and specialized hardware like GPUs or TPUs.

## Benchmark Scenario
The benchmark is designed to mimic real-life scenarios encountered in the LLM development and deployment lifecycle. It consists of the following 5 phases:

1.  **Single Training Workload**: A large training job (65,000 pods on 65,000 nodes) is created and run to completion, starting and ending with an empty cluster.
2.  **Mixed Workloads**: A training workload (50,000 pods) and a higher-priority inference workload (15,000 pods) run concurrently, utilizing the full cluster.
3.  **Inference Scale-Up & Training Disruption**: The inference workload scales up (to 65,000 pods), interrupting the lower-priority training workload. The training workload is recreated but remains pending.
4.  **Inference Scale-Down & Training Recovery**: The inference workload scales back down (to 15,000 pods), allowing the pending training workload (50,000 pods) to be scheduled and resume.
5.  **Training Completion**: The training workload finishes and is deleted, freeing up cluster resources.

## Benchmark Overview and Configurations

This section outlines the specific configurations used for the benchmark.

### Terraform Scenario

Terraform automates the provisioning of the Google Cloud infrastructure required for the benchmark. This includes setting up the VPC network, subnetwork, Cloud NAT for internet access from private nodes, and the GKE cluster itself with specified node pools. Key aspects of the provisioned environment include a private GKE cluster, VPC-native networking, and defined IP allocation policies, all configured through the parameters detailed below.

**Terraform Scenario Parameters:**

The following table details the parameters used in the Terraform scenario to provision the infrastructure for the 65K scale benchmark:

| Parameter                | Description                                                                 | Default Value (for 65K scale)                |
| :----------------------- | :-------------------------------------------------------------------------- | :------------------------------------------- |
| `project_name`           | Name of the project.                                                        | `$PROJECT_ID` (User-defined environment variable) |
| `cluster_name`           | Name of the cluster.                                                        | `gke-benchmark`                              |
| `region`                 | Region to deploy the cluster.                                               | `us-central1`                                |
| `min_master_version`     | Minimum master version for the cluster.                                     | `1.31.2`                                     |
| `vpc_network`            | Name of the VPC network to use for the cluster.                             | `$NETWORK` (User-defined environment variable)    |
| `node_locations`         | List of zones where nodes will be deployed.                                 | `["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"]` |
| `datapath_provider`      | Datapath provider for the cluster (e.g., 'LEGACY_DATAPATH' or 'ADVANCED_DATAPATH'). | `ADVANCED_DATAPATH`                          |
| `master_ipv4_cidr_block` | The IP address range for the GKE cluster's control plane.                   | `172.16.0.0/28`                              |
| `ip_cidr_range`          | The primary IP address range for the cluster's subnetwork.                  | `10.0.0.0/9`                                 |
| `cluster_ipv4_cidr_block`| The IP address range for the Pods within the cluster.                       | `/10` (relative to `ip_cidr_range`)          |
| `services_ipv4_cidr_block`| The IP address range for the Services within the cluster.                   | `/18` (relative to `ip_cidr_range`)          |
| `node_pool_count`        | Number of additional node pools to create.                                  | `16`                                         |
| `node_pool_size`         | Number of nodes per zone in each additional node pool.                               | `1000`                                       |
| `initial_node_count`     | Initial number of nodes in the cluster per zone.                     | `250`                                        |
| `node_pool_create_timeout`| Timeout for creating node pools.                                            | `60m` (60 minutes)                           |

---

### ClusterLoader2 (CL2) Scenario

ClusterLoader2 (CL2) is used to execute the benchmark phases against the provisioned Kubernetes cluster. The behavior of the test is driven by a `config.yaml` file, which orchestrates the creation, scaling, and deletion of workloads according to the defined phases.

**`config.yaml` Overview:**

The `config.yaml` file defines the structure and sequence of the CL2 test.
* It declares variables to capture workload sizes from environment variables (prefixed with `CL2_`), which dictate the scale of training and inference workloads.
* Basic test parameters like the test name and namespace configuration are set.
* Tuning sets control aspects like global Queries Per Second (QPS) and parallelism.
* The core logic resides in the `steps`, which include:
    1.  Starting and gathering performance measurements.
    2.  Creating necessary Kubernetes resources like a headless service and priority classes (for differentiating training and inference workloads).
    3.  Executing the main benchmark logic via an external `modules/statefulsets.yaml` module. This module handles the five benchmark phases, driven by the `config.yaml` and parameterized by the `CL2_` environment variables.

For a detailed look at CL2 configuration patterns, refer to the [ClusterLoader2 load tests examples](https://github.com/kubernetes/perf-tests/blob/master/clusterloader2/testing/load/config.yaml).

**CL2 Environment Parameters:**

The following table describes the `CL2_` environment variables used to configure the ClusterLoader2 test scenario for the 65K scale benchmark:

| Parameter                                   | Description                                                                                                | Default Value (for 65K scale) |
| :------------------------------------------ | :--------------------------------------------------------------------------------------------------------- | :---------------------------- |
| `CL2_DEFAULT_QPS`                           | Default Queries Per Second for the global QPS load tuning set in ClusterLoader2.                             | `500`                         |
| `CL2_ENABLE_VIOLATIONS_FOR_API_CALL_PROMETHEUS_SIMPLE` | A boolean flag to enable or disable violation checking for API call latencies using Prometheus.          | `true`                        |
| `CL2_INFERENCE_WORKLOAD_INITIAL_SIZE`       | The initial number of pods for the inference workload (e.g., in Phase #2 and after scale-down in Phase #4).    | `15000`                       |
| `CL2_INFERENCE_WORKLOAD_SCALED_UP_SIZE`     | The target number of pods for the inference workload when it's scaled up (e.g., in Phase #3).                   | `65000`                       |
| `CL2_SCHEDULER_NAME`                        | The name of the Kubernetes scheduler to be used for placing the pods.                                      | `default-scheduler`           |
| `CL2_TRAINING_WORKLOAD_MIXED_WORKLOAD_SIZE` | The number of pods for the training workload when running concurrently with the inference workload (Phase #2). | `50000`                       |
| `CL2_TRAINING_WORKLOAD_SINGLE_WORKLOAD_SIZE`| The number of pods for the training workload when it's the only large workload running (Phase #1).             | `65000`                       |

---
## Setting up the benchmark

### Prerequisites

* **Google Cloud Project:** A Google Cloud project with billing enabled.
* **Terraform:** Terraform installed and configured.
* **gcloud CLI:** gcloud CLI installed and configured with appropriate permissions.
* **Git:** Git installed and configured.

### Creating the Cluster

1.  **Clone this repository:**
    ```bash
    git clone https://github.com/ai-on-gke/scalability-benchmarks.git
    cd scalability-benchmarks
    ```
2.  **Create and configure `terraform.tfvars`:**

    Create a `terraform.tfvars` file within the `infrastructure/65k-cpu-cluster/` directory. An example is provided at `infrastructure/65k-cpu-cluster/sample-tfvars/65k-sample.tfvars`. Copy this example and update the `project_id`, `region`, and `network` variables with your own values.
    ```bash
    cd infrastructure/65k-cpu-cluster/
    cp ./sample-tfvars/65k-sample.tfvars terraform.tfvars
    ```
3.  **Login to gcloud:**
    ```bash
    gcloud auth application-default login
    ```
4.  **Initialize, plan, and apply Terraform:**
    (Ensure you are in the `infrastructure/65k-cpu-cluster/` directory)
    ```bash
    terraform init
    terraform plan
    terraform apply
    ```
5.  **Authenticate with the cluster:**
    ```bash
    gcloud container clusters get-credentials <CLUSTER_NAME> --region=<REGION>
    ```
    Replace `<CLUSTER_NAME>` and `<REGION>` with the values used in your `terraform.tfvars` file.

### Running the Benchmark

1.  **Navigate to the `perf-tests` directory:**
    If you haven't already, clone the `perf-tests` repository. For this guide, we'll assume you clone it outside the `scalability-benchmarks` directory.
    ```bash
    # Example: if scalability-benchmarks is in ~/scalability-benchmarks
    # git clone https://github.com/kubernetes/perf-tests ~/perf-tests
    # cd ~/perf-tests
    git clone https://github.com/kubernetes/perf-tests
    cd perf-tests
    ```
2.  **Set environment variables:**
    ```bash
    export CL2_DEFAULT_QPS=500
    export CL2_ENABLE_VIOLATIONS_FOR_API_CALL_PROMETHEUS_SIMPLE=true
    export CL2_INFERENCE_WORKLOAD_INITIAL_SIZE=15000
    export CL2_INFERENCE_WORKLOAD_SCALED_UP_SIZE=65000
    export CL2_SCHEDULER_NAME=default-scheduler
    export CL2_TRAINING_WORKLOAD_MIXED_WORKLOAD_SIZE=50000
    export CL2_TRAINING_WORKLOAD_SINGLE_WORKLOAD_SIZE=65000
    ```
3.  **Run the ClusterLoader2 test:**
    (Ensure your current directory is the root of the `perf-tests` repository)
    ```bash
    # Adjust the path to --testconfig based on where you cloned scalability-benchmarks
    # Example: if scalability-benchmarks is in the parent directory of perf-tests:
    # --testconfig=../scalability-benchmarks/CL2/65k-benchmark/config.yaml

    ./run-e2e-with-prometheus-fw-rule.sh cluster-loader2 \
      --nodes=65000 \
      --report-dir=./output/ \
      --testconfig=<PATH_TO_SCALABILITY_BENCHMARKS_REPO>/CL2/65k-benchmark/config.yaml \
      --provider=gke \
      --enable-prometheus-server=true \
      --kubeconfig=${HOME}/.kube/config \
      --v=2
    ```
    * The flag `--enable-prometheus-server=true` deploys a Prometheus server using `prometheus-operator`.
    * Make sure the `--testconfig` flag points to the correct path of the `config.yaml` file within your cloned `scalability-benchmarks` repository.

## Results

The benchmark results are stored in the `./output/` directory (relative to where you ran the CL2 test, typically within the `perf-tests` repository). You can use these results to analyze the performance and scalability of your GKE cluster.

The results include metrics such as:

* Pod state transition durations
* Pod startup latency
* Scheduling throughput
* Cluster creation/deletion time (can be inferred from Terraform logs/timing)
* API server latency

## Cleanup

To avoid incurring unnecessary costs, it's important to clean up the resources created by this benchmark when you're finished.

Navigate to the Terraform configuration directory and run `terraform destroy`:
```bash
cd <PATH_TO_SCALABILITY_BENCHMARKS_REPO>/infrastructure/65k-cpu-cluster/ # Ensure you are in the correct directory
terraform destroy
```