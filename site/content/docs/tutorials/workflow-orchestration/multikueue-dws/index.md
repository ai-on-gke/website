---
linkTitle: "Multikueue, DWS and GKE Autopilot"
title: "Multikueue, DWS and GKE Autopilot"
description: "In this guide you will learn how to set up a multi-cluster environment where job computation is distributed across three GKE clusters in different regions using MultiKueue, Dynamic Workload Scheduler (DWS), and GKE Autopilot."
weight: 30
type: docs
owner:
  - name: "Jean-Baptiste Leroy"
    link: "https://github.com/leroyjb"
tags:
 - Orchestration
 - Tutorials
 - Kueue
 - DWS
draft: false
---

This repository provides the files needed to demonstrate how to use [MultiKueue](https://kueue.sigs.k8s.io/docs/concepts/multikueue/) with [Dynamic Workload Scheduler](https://cloud.google.com/blog/products/compute/introducing-dynamic-workload-scheduler?e=48754805) (DWS) and [GKE Autopilot](https://cloud.google.com/kubernetes-engine/docs/concepts/autopilot-overview).  This setup allows you to run workloads across multiple GKE clusters in different regions, automatically leveraging available GPU resources thanks to DWS.


## Prerequisites
- [Google Cloud](https://cloud.google.com/) account set up.
- [gcloud](https://pypi.org/project/gcloud/) command line tool installed and configured to use your GCP project.
- [kubectl](https://kubernetes.io/docs/tasks/tools/) command line utility is installed.
- [terraform](https://developer.hashicorp.com/terraform/install) command line installed.

## Check out the necessary code files:

```bash
git clone https://github.com/ai-on-gke/tutorials-and-examples.git
cd tutorials-and-examples/workflow-orchestration/multikueue-dws
```

### Repository Contents

This repository contains the following files:

* `create-clusters.sh`: Script to create the required GKE clusters (one manager and three workers).
* `tf folder`: contains the terraform script to create the required GKE clusters (one manager and three workers). You can use it instead of the bash script.
* `deploy-multikueue.sh`: Script to install and configure Kueue and MultiKueue on the clusters.
* `dws-multi-worker.yaml`: Kueue configuration for the worker clusters, including manager configuration.
* `job-multi-dws-autopilot.yaml`: Example job definition to be submitted to the MultiKueue setup.

### Setup and Usage

#### Create Clusters

```bash
terraform -chdir=tf init
terraform -chdir=tf plan
terraform -chdir=tf apply -var project_id=<YOUR PROJECT ID>
```

#### Install Kueue

After creating the GKE clusters and updating your kubeconfig files, install the Kueue components:

```bash
./deploy-multikueue.sh  
```

#### Validate installation

Verify the Kueue installation and the connection between the manager and worker clusters:

```bash
kubectl get clusterqueues dws-cluster-queue -o jsonpath="{range .status.conditions[?(@.type == \"Active\")]}CQ - Active: {@.status} Reason: {@.reason} Message: {@.message}{'\n'}{end}"
kubectl get admissionchecks sample-dws-multikueue -o jsonpath="{range .status.conditions[?(@.type == \"Active\")]}AC - Active: {@.status} Reason: {@.reason} Message: {@.message}{'\n'}{end}"
kubectl get multikueuecluster multikueue-dws-worker-asia -o jsonpath="{range .status.conditions[?(@.type == \"Active\")]}MC-ASIA - Active: {@.status} Reason: {@.reason} Message: {@.message}{'\n'}{end}"
kubectl get multikueuecluster multikueue-dws-worker-us -o jsonpath="{range .status.conditions[?(@.type == \"Active\")]}MC-US - Active: {@.status} Reason: {@.reason} Message: {@.message}{'\n'}{end}"
kubectl get multikueuecluster multikueue-dws-worker-eu -o jsonpath="{range .status.conditions[?(@.type == \"Active\")]}MC-EU - Active: {@.status} Reason: {@.reason} Message: {@.message}{'\n'}{end}"
```

A successful output should look like this:

```bash
CQ - Active: True Reason: Ready Message: Can admit new workloads
AC - Active: True Reason: Active Message: The admission check is active
MC-ASIA - Active: True Reason: Active Message: Connected
MC-US - Active: True Reason: Active Message: Connected
MC-EU - Active: True Reason: Active Message: Connected
```

#### Launch job

Submit your job to the Kueue controller, which will run it on a worker cluster with available resources:

```bash
kubectl create -f job-multi-dws-autopilot.yaml
kubectl create -f job-multi-dws-cpuonly.yaml
```

#### Get the status of the job

To check the job status and see where it's scheduled:

```bash
kubectl get workloads.kueue.x-k8s.io -o jsonpath='{range .items[*]}{.status.admissionChecks}{"\n"}{end}'
```

In the output message, you can find where the job is scheduled

```bash
[{"lastTransitionTime":"2025-05-13T13:13:45Z","message":"The workload got reservation on \"multikueue-dws-worker-asia\"","name":"sample-dws-multikueue","state":"Ready"}]
[{"lastTransitionTime":"2025-05-13T13:13:46Z","message":"The workload got reservation on \"multikueue-dws-worker-us\"","name":"sample-dws-multikueue","state":"Ready"}]
[{"lastTransitionTime":"2025-05-13T13:13:45Z","message":"The workload got reservation on \"multikueue-dws-worker-asia\"","name":"sample-dws-multikueue","state":"Ready"}]
[{"lastTransitionTime":"2025-05-13T13:13:45Z","message":"The workload got reservation on \"multikueue-dws-worker-eu\"","name":"sample-dws-multikueue","state":"Ready"}]
```

#### Destroy resources

```bash
terraform -chdir=tf destroy -var project_id=<YOUR PROJECT ID>
```