---
linkTitle: "DWS"
title: "DWS"
description: "This guide provides examples of how to use Dynamic Workload Scheduler (DWS) within Google Kubernetes Engine (GKE), leveraging Kueue for queue management and resource provisioning. It includes sample configurations for Kueue queues with DWS support (dws-queue.yaml) and a sample job definition (job.yaml) that demonstrates how to request resources and set a maximum run duration using DWS."
weight: 30
type: docs
owner:
  - name: "Jean-Baptiste Leroy"
    link: "https://github.com/leroyjb"
tags:
 - Orchestration
 - Tutorials
---
The repository contains examples on how to use DWS in GKE. More information about DWS is
available [here](https://cloud.google.com/kubernetes-engine/docs/how-to/provisioningrequest).

# Setup and Usage

## Prerequisites
- [Google Cloud](https://cloud.google.com/) account set up.
- [gcloud](https://pypi.org/project/gcloud/) command line tool installed and configured to use your GCP project.
- [kubectl](https://kubernetes.io/docs/tasks/tools/) command line utility is installed.
- [terraform](https://developer.hashicorp.com/terraform/install) command line installed.

## Check out the necessary code files:

```bash
git clone https://github.com/ai-on-gke/tutorials-and-examples.git
cd tutorials-and-examples/workflow-orchestration/dws-example
```

## Create Clusters

```bash
terraform -chdir=tf init
terraform -chdir=tf plan
terraform -chdir=tf apply -var project_id=<YOUR PROJECT ID>
```

## Install Kueue


```bash
VERSION=v0.12.0
kubectl apply --server-side -f https://github.com/kubernetes-sigs/kueue/releases/download/$VERSION/manifests.yaml
```

# Create Kueue resources

```bash
kubectl apply -f dws-queues.yaml 
```

### Validate installation

Verify the Kueue installation in your GKE cluster

```bash
kubectl get clusterqueues dws-cluster-queue -o jsonpath="{range .status.conditions[?(@.type == \"Active\")]}CQ - Active: {@.status} Reason: {@.reason} Message: {@.message}{'\n'}{end}"
kubectl get admissionchecks dws-prov -o jsonpath="{range .status.conditions[?(@.type == \"Active\")]}AC - Active: {@.status} Reason: {@.reason} Message: {@.message}{'\n'}{end}"

```

If the installation and configuration were successful, you should see the following output:

```bash
CQ - Active: True Reason: Ready Message: Can admit new workloads
AC - Active: True Reason: Active Message: The admission check is active
```

# Create a job

```bash
kubectl create -f job-autopilot.yaml
```

# How Kueue and DWS work

After creating the job, you can review the provisioning request:

```bash
kubectl get provisioningrequests
```

You should see output similar to this:

```bash
NAME                                 ACCEPTED   PROVISIONED   FAILED   AGE
job-dws-job-bq9r9-9409b-dws-prov-1   True       False                   158m
```

Kueue creates the provisioning request, which is integrated with DWS. If DWS receives and accepts the request, the ACCEPTED value will be True. Then, as soon as DWS can secure access to your resources, the PROVISIONED value will change to TRUE. At that point, the node is created, and the job schedules on that node. Once the job finishes, GKE automatically releases the node.


```bash
kubectl get provisioningrequests
kubectl get nodes
kubectl get job
```