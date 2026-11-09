---
linkTitle: "TPU Autoscaling"
title: "Slurm on GKE with TPU and Autoscaling"
description: "Configure scale-from-zero TPU autoscaling in Slurm-on-GKE clusters"
weight: 40
type: docs
draft: false
---

## Introduction

Slurm is a popular workload manager for ML researchers to orchestrate AI/ML training and HPC workloads, while Kubernetes provides powerful container management and cloud elasticity. What if you could get the best of both worlds? 

In this tutorial, we show how to deploy Slurm on GKE with Cloud TPUs, combining the familiar research interfaces of Slurm with the automated resource management of GKE. By using scale-from-zero TPU autoscaling, compute resources are provisioned on-demand only when a job enters the queue and automatically scaled down to zero when the queue is empty delivering the true cloud elasticity along with cost-saving benefits while preserving the familiar Slurm workflow.

## Prepare the Environment

To set up your environment, launch a terminal or Google Cloud Shell and follow these steps:

### 1. Set Environment Variables

These environment variables configure your project references, GKE version, and GCP reservation constraints:

```bash
export PROJECT_ID=$(gcloud config get project)
export CLUSTER_NAME=slurm-demo-v6e
# Choose a region where TPU v6e is available.
# Refer: https://cloud.google.com/tpu/docs/regions-zones
export LOCATION=us-east5
export ZONE=us-east5-b    # Choose a zone within the region
export GKE_VERSION=1.35.3-gke.2190000  # Minimum required version for SlurmOperator addon
export REGISTRY_PATH="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/slurm-repo"
# Replace with your actual reservation if applicable.
# Refer: https://docs.cloud.google.com/compute/docs/instances/reservations-single-project
export RESERVATION_NAME="projects/${PROJECT_ID}/reservations/<YOUR_RESERVATION_NAME>"

# TPU Node Pool Configuration Macros
export NODEPOOL_NAME="tpu-v6e-4x4-mh"
export TPU_MACHINE_TYPE="ct6e-standard-4t"
export TPU_ACCELERATOR="tpu-v6e-slice"
export TPU_TOPOLOGY="4x4"
```

### 2. Enable Required APIs

Enable the container and TPU services in your Google Cloud Project to allow GKE to orchestrate TPU VMs:

```bash
gcloud services enable container.googleapis.com tpu.googleapis.com
```

## Create and configure Google Cloud Resources

### 1. Create a GKE Cluster with Slinky Operator Addon

Create a zonal GKE Standard cluster with `--addons=SlurmOperator`. This enables the managed Slurm Operator (from ([Slinky](https://github.com/slinkyproject/slinky)) project) on GKE. The control plane automatically manages the operator's installation, lifecycle updates, and CRD resources.

```bash
gcloud container clusters create $CLUSTER_NAME \
    --project=$PROJECT_ID \
    --zone=$ZONE \
    --cluster-version=$GKE_VERSION \
    --addons=SlurmOperator \
    --num-nodes=3 \
    --machine-type="e2-standard-2" \
    --enable-dataplane-v2 \
    --autoscaling-profile=optimize-utilization
```

### 2. Create a TPU Node Pool with Autoscaling Enabled

Create the TPU v6e node pool. 

When deploying Cloud TPUs on GKE, you must specify the TPU VM machine type and the physical interconnect topology layout:
*   **Machine Type (`--machine-type="ct6e-standard-4t"`):** Configures Cloud TPU v6e instances. The `4t` suffix specifies that each VM host contains 4 physical TPU v6e chips. Refer to the [Cloud TPU v6e Guide](https://cloud.google.com/tpu/docs/v6e) for details.
*   **Topology (`--tpu-topology="4x4"`):** Defines the layout of the interconnected TPU chips. A `4x4` configuration requests a slice containing 16 TPU chips spanning 4 VM hosts.

```bash
gcloud container node-pools create $NODEPOOL_NAME \
    --cluster=$CLUSTER_NAME \
    --project=$PROJECT_ID \
    --region=$LOCATION \
    --node-locations=$ZONE \
    --machine-type=$TPU_MACHINE_TYPE \
    --tpu-topology=$TPU_TOPOLOGY \
    --num-nodes=0 \
    --enable-autoscaling \
    --min-nodes=0 \
    --max-nodes=4 \
    --enable-image-streaming \
    --reservation-affinity=specific \
    --reservation="${RESERVATION_NAME}" \
    --scopes=https://www.googleapis.com/auth/cloud-platform \
    --accelerator-network-profile=auto \
    --node-labels=cloud.google.com/gke-networking-dra-driver=true \
    --node-taints=google.com/tpu=present:NoSchedule,slurm-worker=true:NoSchedule \
    --consolidation-delay=60s
```

Note: Because we set `--num-nodes=0` and `--min-nodes=0`, this command initializes the pool structure in GKE without provisioning any physical TPU VM instances yet. Compute nodes are only created dynamically when a Slurm job enters the queue. 

**Alternative Node Provisioning Models:**
Depending on your cost requirements and capacity availability, you can configure GKE TPU node pools using:
*   **Spot VMs:** To utilize lower-cost, preemptible compute capacity for batch workloads, refer [GKE Spot VMs Guide](https://cloud.google.com/kubernetes-engine/docs/how-to/spot-vms) for details.
*   **Dynamic Workload Scheduler (DWS):** To obtain capacity allocations via queueing when on-demand capacity is limited, refer [Dynamic Workload Scheduler on GKE Guide](https://cloud.google.com/kubernetes-engine/docs/how-to/dynamic-workload-scheduler).

### 3. Configure Kubectl

Configure your local `kubectl` context to authenticate with the GKE cluster:

```bash
gcloud container clusters get-credentials ${CLUSTER_NAME} --region=${LOCATION}
```

## Deploy the Slurm Cluster with Custom JAX Image

### 1. Build and Push Custom Worker Image

For TPU workloads, installing dependencies dynamically at runtime (using `pip install` inside training scripts) can take several minutes. Because worker nodes are provisioned dynamically on demand with autoscaling, baking JAX directly into the base container image ensures that newly booted instances are immediately ready to execute jobs upon startup, avoiding redundant package download and compilation latency on every scale-up event.

**Note:** This step is optional. If you do not wish to build a custom image or are not running JAX-based TPU workloads, you can skip this section and configure the Helm chart in the next step to reference the official Slinky `slurmd` image (`gcr.io/gke-release/slinky/slurmd:25.11-ubuntu24.04-gke.9`) directly.

Create a local `Dockerfile`:

```bash
cat << 'EOF' > Dockerfile
FROM gcr.io/gke-release/slinky/slurmd:25.11-ubuntu24.04-gke.9
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    python3-pip python3-dev build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages "jax[tpu]" \
    -f https://storage.googleapis.com/jax-releases/libtpu_releases.html
EOF
```

Build and push the image to Google Artifact Registry:
```bash
gcloud auth configure-docker ${LOCATION}-docker.pkg.dev --quiet
docker build -t ${REGISTRY_PATH}/slurmd-jax:latest .
docker push ${REGISTRY_PATH}/slurmd-jax:latest
```

### 2. Deploy Slurm Helm Chart

Create the `values.yaml` file targeting your image registry and configuring TPU resources.

**Note:** The `nodeSelector` labels below (`cloud.google.com/gke-nodepool`, `cloud.google.com/gke-tpu-accelerator`, and `cloud.google.com/gke-tpu-topology`) target your physical TPU v6e multi-host node pool. Ensure these match the exact values of the GKE node pool you created in Step 1 (defined by the environment variables `$NODEPOOL_NAME`, `$TPU_ACCELERATOR`, and `$TPU_TOPOLOGY` in Step 1).

```bash
cat << EOF > values.yaml
controller:
    slurmctld:
        image:
            repository: gcr.io/gke-release/slinky/slurmctld
            tag: 25.11-ubuntu24.04-gke.9
    extraConf: |
        GresTypes=tpu
    metrics:
        enabled: true
        serviceMonitor:
            enabled: true
            labels:
                release: prometheus

loginsets:
    slinky:
        enabled: true

configFiles:
    gres.conf: |
        Name=tpu Count=4

nodesets:
    slinky:
        replicas: 0 # Start at 0 for scale-from-zero
        extraConfMap:
            Gres: ["tpu:4"]
        slurmd:
            image:
                repository: ${REGISTRY_PATH}/slurmd-jax
                tag: latest
            resources:
                requests:
                    # Allocates all 4 physical TPU v6e chips on the ct6e-standard-4t VM host
                    google.com/tpu: "4"
                limits:
                    google.com/tpu: "4"
        podSpec:
            hostNetwork: true                  
            dnsPolicy: ClusterFirstWithHostNet
            nodeSelector:
                cloud.google.com/gke-nodepool: tpu-v6e-4x4-mh
                cloud.google.com/gke-tpu-accelerator: tpu-v6e-slice
                cloud.google.com/gke-tpu-topology: 4x4
            tolerations:
                - key: "slurm-worker"
                  operator: "Equal"
                  value: "true"
                  effect: "NoSchedule"
                - key: "google.com/tpu"
                  operator: "Exists"
                  effect: "NoSchedule"
EOF
```

**Understanding the TPU VM Resource Limits and Selectors:**
*   **TPU allocation (`google.com/tpu: "4"`):** Requesting 4 TPUs maps to the 4 physical v6e chips present on the host VM. This ensures that exactly one Slurm worker pod is scheduled per physical TPU VM host (since no other pod requesting TPUs can fit on the same node).
*   **CPU and Memory Requests (Optional):** While omitting them allows the pod to access host resources by default, you can explicitly define CPU and memory requests and limits to guarantee host resource allocations.

Install the Helm release:
```bash
helm install slurm oci://ghcr.io/slinkyproject/charts/slurm \
    --namespace=slurm \
    --create-namespace \
    --version 1.0.2 \
    -f values.yaml
```

## Access and Verify the Slurm Cluster

Before enabling autoscaling, log into the Slurm control plane environment to verify that your scheduler and partitions are ready.

### 1. Check Control Plane Pods

Confirm that all core Slurm control plane pods are running:

```bash
kubectl get pods -n slurm
```
*Expected Output:*
```
NAME                                  READY   STATUS    RESTARTS   AGE
slurm-controller-0                    3/3     Running   0          8h
slurm-login-slinky-7cfd979966-kqndx   1/1     Running   0          19h
slurm-restapi-655657bc4c-cf2k7        1/1     Running   0          19h
```

Understanding the core components:
*   **`slurm-controller-0`**: Runs the central Slurm scheduler daemon (`slurmctld`).
*   **`slurm-login-slinky`**: Provides the interactive login shell. Users log into this container to submit and manage jobs using standard Slurm CLI commands.
*   **`slurm-restapi`**: Exposes Slurm REST API endpoints, allowing the Prometheus exporter and KEDA to read partition queues and job metrics programmatically.

**Note: Why do we deploy a Slurm Helm Chart if we enabled `--addons=SlurmOperator`?**
The GKE `SlurmOperator` addon only installs the operator controller (the background service that watches for Slinky custom resources). It does not install the actual Slurm cluster components. We use the Slurm Helm Chart to deploy the Slurm database (`slurmdbd`), the Slurm controller (`slurmctld`), the login node interface, and the default `slurm.conf` configurations in our user namespace.

## Enable Autoscaling with Slurm

Autoscaling dynamic TPU compute resources can be achieved either through a declarative Kubernetes-native operator (Method 1) or via Slurm's native scheduler-directed power saving loops (Method 2).

### Method 1: KEDA (Kubernetes Event-driven Autoscaling)

KEDA acts as a bridge between the Slurm scheduler queue and standard Kubernetes Horizontal Pod Autoscalers (HPA).

#### How KEDA and GKE Autoscaling Work Together

1. **Queue Scraped:** Prometheus scrapes partition metrics from the Slurm controller.
2. **HPA Scaled:** KEDA polls Prometheus. When it detects a pending job, it scales the Slinky `NodeSet` CR replicas from `0` to the requested number of nodes.
3. **GKE scale-up:** GKE Cluster Autoscaler detects the newly created pending pods, allocates VM instances in the TPU node pool, and joins them to the cluster.
4. **Execution and scale-down:** The worker pods run the job. Once the queue is empty, KEDA scales the replicas back to `0`, allowing GKE to terminate the idle TPU VM instances.

#### 1. Install Prometheus Operator

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install prometheus prometheus-community/kube-prometheus-stack \
  --set 'installCRDs=true' \
  --namespace prometheus \
  --create-namespace
```

#### 2. Install KEDA

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update
helm install keda kedacore/keda \
  --namespace keda \
  --create-namespace
```

#### 3. Deploy the KEDA ScaledObject

To trigger scaling, KEDA evaluates a Prometheus metric query to determine the target replica count for the Slinky `NodeSet`. 

```bash
cat << EOF > keda-scaler.yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: scale-slinky
  namespace: slurm
spec:
  scaleTargetRef:
    apiVersion: slinky.slurm.net/v1beta1
    kind: NodeSet
    name: slurm-worker-slinky
  pollingInterval: 10 # Interval in seconds to poll Prometheus metrics
  cooldownPeriod: 600 # Wait period in seconds before scaling down after the queue is empty
  idleReplicaCount: 0 # Target replica count when there are no pending or running jobs
  minReplicaCount: 0
  maxReplicaCount: 4
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleDown:
          stabilizationWindowSeconds: 300  # 5-minute window to allow log collection before pods scale down
          policies:
            - type: Percent
              value: 100                  # Scale down all nodes concurrently
              periodSeconds: 15
  triggers:
    - type: prometheus
      metricType: Value
      metadata:
        serverAddress: http://prometheus-kube-prometheus-prometheus.prometheus.svc.cluster.local:9090
        query: slurm_partition_jobs_min_job_nodes{partition="slinky"} + slurm_partition_nodes_alloc{partition="slinky"} + slurm_partition_nodes_mixed{partition="slinky"}
        threshold: '1'
EOF
```

Apply the ScaledObject:
```bash
kubectl apply -f keda-scaler.yaml
```

Check the status of KEDA's ScaledObject and verify that it matches the target NodeSet resource:

```bash
kubectl get scaledobject scale-slinky -n slurm
```
*Expected Output:*
```text
NAME           SCALETARGETKIND   SCALETARGETNAME       MIN   MAX   READY   ACTIVE   FALLBACK   PAUSED   TRIGGERS   AUTHENTICATIONS   AGE
scale-slinky                     slurm-worker-slinky   0     4     True    False    False      False    prometheus                   30s
```

**Important:** The target replica count must represent the total number of worker nodes needed. To achieve this, the query sums:
1. **`slurm_partition_jobs_min_job_nodes`**: The number of nodes required by pending jobs in the partition queue. (Using the standard `slurm_partition_jobs_pending` only returns `1` when jobs are pending, which would deadlock multi-node jobs).
2. **`slurm_partition_nodes_alloc`** and **`slurm_partition_nodes_mixed`**: The number of nodes currently running active jobs. Omitting these would cause KEDA to scale the cluster down to zero as soon as a pending job starts executing.

### Method 2: Slurm Native Power Saving

If you require sub-second scale-up responsiveness, you can configure Slurm's native power-saving mechanism to bypass KEDA and patch the `NodeSet` directly. 

This utilizes the same underlying GKE scaling mechanism as Method 1: the resume script patches the Slinky `NodeSet` CR, which creates pending pods, triggering GKE's Cluster Autoscaler to provision the physical TPU nodes. By directly updating the Kubernetes API, it eliminates the metric scraping and evaluation intervals introduced by KEDA and Prometheus.

For details on Slurm power saving configuration and parameters, refer to the official [Schedmd Slurm Power Saving Guide](https://slurm.schedmd.com/power_save.html).

#### 1. Inject Resume/Suspend Scripts in values.yaml

Modify the Helm values to include the `ResumeProgram` and `SuspendProgram` scripts:

```yaml
controller:
    extraConf: |
        GresTypes=gpu,tpu
        ResumeProgram="/etc/slurm/resume.sh"
        SuspendProgram="/etc/slurm/suspend.sh"
        SuspendTime=30
        ResumeTimeout=600

configFiles:
    resume.sh: |
        #!/bin/bash
        node_name="$1"
        nodes_count=$(echo "$node_name" | tr ',' '\n' | wc -l)
        NODESET_NAME="slurm-worker-slinky"
        
        curl -s -k -XPATCH \
          -H "Authorization: Bearer $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)" \
          -H "Content-Type: application/merge-patch+json" \
          -d "{\"spec\": {\"replicas\": $nodes_count}}" \
          https://kubernetes.default.svc/apis/slinky.slurm.net/v1beta1/namespaces/slurm/nodesets/${NODESET_NAME} > /dev/null
        exit 0

    suspend.sh: |
        #!/bin/bash
        NODESET_NAME="slurm-worker-slinky"
        curl -s -k -XPATCH \
          -H "Authorization: Bearer $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)" \
          -H "Content-Type: application/merge-patch+json" \
          -d '{"spec": {"replicas": 0}}' \
          https://kubernetes.default.svc/apis/slinky.slurm.net/v1beta1/namespaces/slurm/nodesets/${NODESET_NAME} > /dev/null
        exit 0
```
Update the release:
```bash
helm upgrade slurm oci://ghcr.io/slinkyproject/charts/slurm -n slurm -f values.yaml
```

#### 2. Grant RBAC Patch Permissions

Create and apply `slurm-rbac.yaml`:
```bash
cat << EOF > slurm-rbac.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: slurmctld-nodeset-patcher
  namespace: slurm
rules:
- apiGroups: ["slinky.slurm.net"]
  resources: ["nodesets"]
  verbs: ["get", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: slurmctld-nodeset-patcher-binding
  namespace: slurm
subjects:
- kind: ServiceAccount
  name: default
  namespace: slurm
roleRef:
  kind: Role
  name: slurmctld-nodeset-patcher
  apiGroup: rbac.authorization.k8s.io
EOF
```
```bash
kubectl apply -f slurm-rbac.yaml
```

## Enter the Slurm Environment

As an ML researcher or cluster operator, you submit and monitor training jobs directly within the Slurm shell environment using native commands. Once autoscaling is configured, submitting a job automatically triggers the GKE autoscaler under the hood.

### 1. Log In to the Login Node

Access the interactive login shell container:
```bash
kubectl exec -it deployment/slurm-login-slinky -n slurm -- bash
```

### 2. Inspect registered Partitions and Nodes

Query the partition queue state:
```bash
sinfo
```
*Expected Output:*
```text
PARTITION AVAIL  TIMELIMIT  NODES  STATE NODELIST
slinky       up   infinite      0    n/a 
all*         up   infinite      0    n/a 
```

Verify that no compute nodes are registered in the scheduler at startup:
```bash
scontrol show nodes
```
*Expected Output:*
```text
No nodes in the system
```

Because the partition is set up for scale-from-zero, `sinfo` shows `0` active nodes at startup.

To verify that the partition points to the Slinky nodeset, inspect the partition configuration:

```bash
scontrol show partition slinky
```
*Expected Output:*
```text
PartitionName=slinky
   AllowGroups=ALL AllowAccounts=ALL AllowQos=ALL
   AllocNodes=ALL Default=NO QoS=N/A
   ...
   NodeSets=slinky
   Nodes=(null)
   ...
   State=UP TotalCPUs=0 TotalNodes=0
```

### 3. Create the Slurm Batch Script

SSH to the Slurm login pod container and create the batch script below. 

This script targets the `slinky` partition and requests 4 nodes (`--nodes=4`), matching your TPU v6e `4x4` topology slice (16 chips total) exactly. This is a placeholder job that discovers and prints the available TPU nodes in the slice.

```bash
cat << 'EOF' > /tmp/tpu-job.sh
#!/bin/bash
#SBATCH --job-name=tpu-jax-demo
#SBATCH --partition=slinky
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=1
#SBATCH --gres=tpu:4
#SBATCH --output=tpu_demo_%j.out

# Add a short warmup sleep to allow network interfaces to initialize completely after nodes boot
sleep 15


# Resolve coordinator host (Node 0 alphabetically) IP dynamically
COORDINATOR_NAME=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -n 1)
COORDINATOR_IP=$(scontrol show node "$COORDINATOR_NAME" | grep -oP 'NodeAddr=\S+' | cut -d= -f2)

echo "=== Running JAX verification on TPU nodes ==="

srun bash -c "
  export JAX_COORDINATOR_ADDRESS=\"${COORDINATOR_IP}:12345\"
  export JAX_NUM_PROCESSES=\$SLURM_NNODES
  export JAX_PROCESS_ID=\$SLURM_NODEID
  python3 -u -c '
import jax
import socket
import time

# Initialize the JAX distributed system (reads coordinator configuration from env)
jax.distributed.initialize()

# Print detailed local node hostname and visible device mappings
hostname = socket.gethostname()
print(f\"Process Index (Rank): {jax.process_index()} | Pod Hostname: {hostname} | Local TPU Chips: {jax.local_device_count()} | Local Devices: {[d.id for d in jax.local_devices()]}\", flush=True)

# Quick JAX global metadata verify print on Process 0
if jax.process_index() == 0:
    print(\"\n--- JAX Distributed Initialized ---\", flush=True)
    print(f\"Total JAX Processes:     {jax.process_count()}\", flush=True)
    print(f\"Total Device Count:      {jax.device_count()}\", flush=True)
    print(f\"Available Devices:       {jax.devices()}\", flush=True)
    print(\"----------------------------------\", flush=True)

# Placeholder: Replace the block below with your actual JAX code
# Sleep for 5 minutes
time.sleep(300)
'
"

EOF
```

### 4. Submit the Job

Submit the job and verify it has entered the queue:

```bash
sbatch /tmp/tpu-job.sh
```

*Expected Output:*
```text
             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
                 1    slinky tpu-jax-     root PD       0:00      4 (PartitionConfig)
```

*   **State (`ST = PD`):** Indicates the job is currently `PENDING` (waiting in the queue).
*   **Reason (`(PartitionConfig)`):** Under the GKE Slurm integration, this reason indicates that the partition does not currently have any active worker nodes available to satisfy the resource request. The scheduler is waiting for KEDA and GKE to boot the required compute instances and register them as active nodes.

### 5. Verify Job Execution and Scaling

1. **Verify Pod Scale-Up:**

  As soon as the job is submitted, verify that the Slinky worker replica count scales up:
  
  ```bash
   kubectl get nodeset slurm-worker-slinky -n slurm
  ```
  *Expected Output:*
  
  ```text
   NAME                  REPLICAS   UPDATED   READY   AGE
   slurm-worker-slinky   4          4                 40m
  ```
  
2. **Verify Worker Pod Status:**
   Monitor the worker pods in the `slurm` namespace. As new TPU nodes register, the pods will transition from `Pending` -> `Init` -> `Running`:
   ```bash
   kubectl get pods -n slurm
   ```
   *Expected Output:*
   ```text
   NAME                                  READY   STATUS     RESTARTS   AGE
   slurm-controller-0                    3/3     Running    0          21m
   slurm-login-slinky-59f59d9c68-lfjpb   1/1     Running    0          16m
   slurm-restapi-6b4ccb479f-pmx8s        1/1     Running    0          44m
   slurm-worker-slinky-0                 0/2     Pending    0          80s
   slurm-worker-slinky-1                 0/2     Pending    0          80s
   slurm-worker-slinky-2                 0/2     Pending    0          80s
   slurm-worker-slinky-3                 0/2     Pending    0          80s
   ```

3. **Verify Node Provisioning:**

  GKE Cluster Autoscaler will detect the pending worker pods and provision the TPU instances. Verify that new physical GKE worker nodes join the cluster:

  ```bash
   kubectl get nodes -l cloud.google.com/gke-nodepool=tpu-v6e-4x4-mh
  ```
  
  *Expected Output:*

  ```text
   NAME                                               STATUS     ROLES    AGE   VERSION
   gke-tpu-322d3c90-6s19   Ready    <none>   4m45s   v1.35.3-gke.2190000
   gke-tpu-322d3c90-8j1w   Ready    <none>   6m13s   v1.35.3-gke.2190000
   gke-tpu-322d3c90-vhl3   Ready    <none>   4m45s   v1.35.3-gke.2190000
   gke-tpu-322d3c90-vsh0   Ready    <none>   6m9s    v1.35.3-gke.2190000
  ```

  **NOTE:** GKE node pool scale-up can take a few minutes for the physical TPU VM hosts to be provisioned in Google Compute Engine and register as active `Ready` nodes in Kubernetes.

4. **Verify Slurm Nodes Registration:**

   Once the physical TPU GKE nodes register, log back into the Slurm login container pod and verify that the Slurm scheduler has detected and registered the new dynamic hosts:
   ```bash
   scontrol show nodes
   ```
   *Expected Output:*
   ```text
   NodeName=gke-tpu-322d3c90-6s19 Arch=x86_64 CoresPerSocket=90 
      CPUAlloc=0 CPUEfctv=180 CPUTot=180 CPULoad=0.43
      AvailableFeatures=slinky
      ActiveFeatures=slinky
      Gres=tpu:4
      NodeAddr=10.202.0.69 NodeHostName=gke-tpu-322d3c90-6s19 Version=25.11.6-pre1
      State=IDLE+DYNAMIC_NORM ThreadsPerCore=2 TmpDisk=0 Weight=1
      ...
   NodeName=gke-tpu-322d3c90-8j1wArch=x86_64 CoresPerSocket=90 
      CPUAlloc=0 CPUEfctv=180 CPUTot=180 CPULoad=0.78
      AvailableFeatures=slinky
      ActiveFeatures=slinky
      Gres=tpu:4
      NodeAddr=10.202.0.70 NodeHostName=gke-tpu-322d3c90-8j1w Version=25.11.6-pre1
      State=IDLE+DYNAMIC_NORM ThreadsPerCore=2 TmpDisk=0 Weight=1
      ...
   ```

5. **Verify Running Queue State:**

   Once node registration completes, check the Slurm queue status again. The job state will transition to `R` (Running) and list the allocated TPU hostnames:
   ```bash
   squeue
   ```
   *Expected Output:*
   ```text
                JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
                    1    slinky tpu-jax-     root  R       0:53      4 gke-tpu-322d3c90-6s19,gke-tpu-322d3c90-8j1w,gke-tpu-322d3c90-vhl3,gke-tpu-322d3c90-vsh0
   ```

   **Understanding the Running Queue State:**
   *   **State (`ST = R`):** Indicates the job has successfully transitioned from pending to active execution (`Running`).
   *   **Nodes (`NODES = 4`):** Displays the count of active TPU nodes allocated to this workload.
   *   **Nodelist:** Lists the hostnames of the GKE TPU nodes that are running the distributed tasks.

6. **Check Results:**

  To check the output without leaving the interactive Slurm login shell, run a single-task `srun` job targeting the node list where the job ran. Attach this task as a job step using the `--jobid` and `--overlap` flags to allow your check task to run concurrently:
  
  ```bash
   # Set the active JobID
   JOB_ID=32

   # Automatically extract the BatchHost node allocated to the job
   NODE_NAME=$(scontrol show job $JOB_ID | grep -oP 'BatchHost=\S+' | cut -d= -f2)

   # Run srun to print the output file from the target node
   srun --jobid=$JOB_ID --overlap --nodelist=$NODE_NAME cat /tmp/tpu_demo_${JOB_ID}.out
   ```

   *Expected output:*
  ```text
  === Running JAX verification on TPU nodes ===
  Process Index (Rank): 1 | Pod Hostname: gke-tpu-322d3c90-vhl3 | Local TPU Chips: 4 | Local Devices: [2, 3, 6, 7]
  Process Index (Rank): 2 | Pod Hostname: gke-tpu-322d3c90-vsh0 | Local TPU Chips: 4 | Local Devices: [8, 9, 12, 13]
  Process Index (Rank): 0 | Pod Hostname: gke-tpu-322d3c90-8j1w | Local TPU Chips: 4 | Local Devices: [0, 1, 4, 5]
  Process Index (Rank): 3 | Pod Hostname: gke-tpu-322d3c90-6s19 | Local TPU Chips: 4 | Local Devices: [10, 11, 14, 15]

  --- JAX Distributed Initialized ---
  Total JAX Processes:     4
  Total Device Count:      16
  Available Devices:       [TpuDevice(id=0, process_index=0, coords=(0,0,0), core_on_chip=0), TpuDevice(id=1, process_index=0, coords=(1,0,0), core_on_chip=0), TpuDevice(id=4, process_index=0, coords=(0,1,0), core_on_chip=0), TpuDevice(id=5, process_index=0, coords=(1,1,0), core_on_chip=0), TpuDevice(id=2, process_index=1, coords=(2,0,0), core_on_chip=0), TpuDevice(id=3, process_index=1, coords=(3,0,0), core_on_chip=0), TpuDevice(id=6, process_index=1, coords=(2,1,0), core_on_chip=0), TpuDevice(id=7, process_index=1, coords=(3,1,0), core_on_chip=0), TpuDevice(id=8, process_index=2, coords=(0,2,0), core_on_chip=0), TpuDevice(id=9, process_index=2, coords=(1,2,0), core_on_chip=0), TpuDevice(id=12, process_index=2, coords=(0,3,0), core_on_chip=0), TpuDevice(id=13, process_index=2, coords=(1,3,0), core_on_chip=0), TpuDevice(id=10, process_index=3, coords=(2,2,0), core_on_chip=0), TpuDevice(id=11, process_index=3, coords=(3,2,0), core_on_chip=0), TpuDevice(id=14, process_index=3, coords=(2,3,0), core_on_chip=0), TpuDevice(id=15, process_index=3, coords=(3,3,0), core_on_chip=0)]
  ----------------------------------
  ```

  **Alternative: use Kubectl to check output:**

  If you have access to `kubectl` from your local machine, you can query the active execution node, locate the corresponding pod, and retrieve the logs directly:
  
  ```bash
   # Set the active Slurm JobID
   JOB_ID=1

   # Resolve the pod name and print its output file
   BATCH_HOST=$(kubectl exec deployment/slurm-login-slinky -n slurm -- scontrol show job $JOB_ID | grep -oP 'BatchHost=\S+' | cut -d= -f2)
   POD_NAME=$(kubectl get pods -n slurm -o jsonpath="{.items[?(@.spec.nodeName=='$BATCH_HOST')].metadata.name}")
   kubectl exec $POD_NAME -n slurm -c slurmd -- cat /tmp/tpu_demo_${JOB_ID}.out
  ```

7. **Verify Scale-Down back to Zero:**

When a Slurm job completes execution and there are no other active workloads queued or running on the TPU partition, the GKE Cluster Autoscaler automatically scales down the idle TPU nodes back to zero. This scale-down trigger occurs after a short cooldown window, which is determined by the cluster's `--autoscaling-profile` configuration.

1. **Verify Slurm Scale-Down:**

  First, run this from your local shell terminal to confirm that the Kubernetes worker pods are terminating or deleted:
  ```bash
  kubectl get pods -n slurm -l app.kubernetes.io/name=slurmd
  ```
  *Expected Output:*
  ```text
   No resources found in slurm namespace.
  ```
  *(Or you will see the pods transition to a `Terminating` state).*

  Next, log back into the Slurm login container pod and verify that the scheduler has removed all nodes from the active partition database:
  ```bash
   scontrol show nodes
  ```
  *Expected Output:*
  ```text
  No nodes in the system
  ```

2. **Verify GKE Node Pool Scale-Down:**

  Finally, run this from your local shell terminal to verify that the physical TPU GKE VM host instances have been terminated and removed from the Kubernetes cluster:

  ```bash
   kubectl get nodes -l cloud.google.com/gke-tpu-accelerator
  ```
  *Expected Output:*
   ```text
   No resources found.
  ```

## Results and Key Takeaways

By deploying the Slurm-on-GKE with GKE Cluster Autoscaler, TPU accelerator resources are provisioned on-demand only when a job is active in the queue. Slurm jobs request specific TPU topologies (such as a 16-chip v6e slice), and the Kubernetes control plane automatically boots and allocates the exact hardware nodes required to fulfill that request without manual intervention. When the queue is empty, the TPU nodes automatically scale down to zero, avoiding the cost of idle accelerator hardware runtime.

## Clean up

To avoid incurring charges to your Google Cloud account for the resources that you created in this guide, run the following commands to delete your GKE cluster and Artifact Registry repository:

```bash
# Delete the GKE cluster (this will automatically delete the nodes and node pools)
gcloud container clusters delete "${CLUSTER_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}"

# Delete the Artifact Registry repository created for the custom image
gcloud artifacts repositories delete slurm-repo \
    --location="${LOCATION}" \
    --project="${PROJECT_ID}"
```

