---
linkTitle: "Device alignment of GPU, NIC, and CPU with DRA"
title: "Device alignment of GPU, NIC, and CPU with DRA"
description: "Learn how to achieve optimal hardware alignment for GPUs, NICs, and exclusive CPUs on GKE using Dynamic Resource Allocation (DRA) to maximize performance."
weight: 40
owner:
  - name: "Praveen Krishna"
    link: "https://github.com/pravk03"
type: docs
tags:
 - DRA
 - GPU
 - NIC
 - CPU
 - Resource Alignment
draft: false
cloudShell:
    enabled: true
    folder: site/content/docs/tutorials/dynamic-resource-allocation/gpu-nic-cpu-alignment
    editorFile: index.md
---

## Background

In High Performance Computing (HPC) and AI training and inference workloads, device alignment is paramount to achieving peak performance. To maximize throughput and minimize latency, accelerators (GPUs) and high-speed network interfaces (NICs) must be physically attached to the same PCIe root complex. This precise co-location unlocks technologies like **GPUDirect RDMA**, allowing GPU-to-GPU communication across nodes to bypass host system memory and CPU involvement entirely.

Similarly, host CPU processes should run on the specific physical cores directly connected to the same socket as the GPU and NIC. Spanning execution across different sockets forces data packets through slow cross-socket interconnects (such as QPI/UPI), introducing severe memory access latency and resource bottlenecks that degrade overall workload performance.

Traditionally, Kubernetes resource alignment is not topology-aware. The standard `CPUManager` and individual device plugins operate independently, making it impossible to guarantee that an allocated GPU, a specific NIC, and exclusive CPU cores all land on the same hardware locality.

This tutorial demonstrates how to achieve **optimal CPU, GPU, and high-speed multi-NIC device alignment** on GKE using **Dynamic Resource Allocation (DRA)**.

### GKE DRA Core Topology Drivers

1. **GKE Managed DRANet**:
   A GKE-managed feature building on the open-source DRANET project. It exposes node-level network interfaces to Pods
   using the Kubernetes DRA API, supporting RDMA-capable devices (`mrdma.google.com` DeviceClass) and other network
   devices (`netdev.google.com` DeviceClass). It allows Pods to claim dedicated, non-shareable network interfaces
   directly, ensuring full bandwidth and low-latency access for performance-sensitive workloads. For more details,
   see the official [Allocate Network Resources via GKE DRA](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/allocate-network-resources-dra) guide.

2. **DRA Driver Nvidia GPU**:
   This driver manages the dynamic allocation and sharing of physical NVIDIA GPU devices. Installed via the
   `dra-driver-nvidia-gpu` Helm chart ([kubernetes-sigs/dra-driver-nvidia-gpu](https://github.com/kubernetes-sigs/dra-driver-nvidia-gpu)),
   it publishes node GPU capacities and topology details through `ResourceSlice` objects. Rather than hardcoding GPU
   sharing configurations at the node pool level, this driver shifts control to the workload manifest—allowing pods
   to dynamically request dedicated physical GPUs or configure sharing modes (such as dynamic Multi-Instance GPU (MIG)
   partitioning, Multi-Process Service (MPS), or Time-Slicing) directly inside their `ResourceClaim` specifications.

3. **DRA Driver CPU**:
   This is the reference CPU DRA driver ([kubernetes-sigs/dra-driver-cpu](https://github.com/kubernetes-sigs/dra-driver-cpu)).
   This driver leverages the **DRA Consumable Capacity** feature to discover and publish host CPU topology as devices
   (one device per physical socket or NUMA node) in the `ResourceSlice`. Workloads then declaratively request fractions
   of these pool's capacity (e.g., 10 out of 112 CPUs in a NUMA node). The driver performs **exclusive CPU core allocation**,
   dynamically pinning the container cgroups exclusively to the allocated physical cores to completely avoid host resource
   contention and guarantee consistent compute performance.

4. **Resource Alignment**:
   In a multi-driver layout, physical device alignment is enforced declaratively via the native DRA **`matchAttribute`**
   constraint. Under the Kubernetes API, when a `ResourceClaim` requests devices from different classes (e.g., a GPU,
   a NIC, and a set of CPU cores), the scheduler evaluates these constraints using **set-intersection semantics**.
   The constraint requires that the attribute sets across all selected devices have a non-empty intersection. By specifying:
   * Align **GPU and NIC** on `resource.kubernetes.io/pcieRoot` (forces them onto the same PCIe root switch complex).
   * Align **CPU and NIC** on `dra.net/numaNode` (forces them onto the same physical NUMA socket).
   * **Transitive result**: Because the GPU and exclusive CPU cores are both aligned directly to the shared NIC's topology
     bounds, all three resources are pinned to the exact same physical NUMA node, completely bypassing cross-socket
     host bus overhead and latency.

## Prepare the Environment

To set up your environment with Cloud Shell, follow these steps:

1. In the Google Cloud console, click the **Activate Cloud Shell** icon to launch a session in the bottom pane.
2. Set the default environment variables:

```bash
export PROJECT_ID=$(gcloud config get project)
export CLUSTER_NAME="gpu-nic-cpu-alignment"
# Choose a region with A3 Ultra (NVIDIA H200 GPUs).
# Refer: https://docs.cloud.google.com/compute/docs/regions-zones/gpu-regions-zones.
export LOCATION="us-south1"
export ZONE="us-south1-b"
export CLUSTER_VERSION="1.36"
# Replace with your actual reservation if applicable.
# Refer: https://docs.cloud.google.com/compute/docs/instances/reservations-single-project
export RESERVATION_NAME="projects/${PROJECT_ID}/reservations/YOUR_RESERVATION_NAME"
```

## Create and configure Google Cloud Resources

### Create a GKE Cluster

Create a GKE zonal cluster with Dataplane V2 enabled:

```bash
gcloud container clusters create "${CLUSTER_NAME}" \
    --cluster-version="${CLUSTER_VERSION}" \
    --enable-dataplane-v2 \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}" \
    --num-nodes 1 \
    --labels=created-by=ai-on-gke,guide=gpu-nic-cpu-alignment
```

### Create a node pool with A3 Ultra H200 GPUs

We provision a node pool using `a3-ultragpu-8g` machine types containing 8x NVIDIA H200 GPUs and support multi-NIC high-performance networking.

To enable Dynamic Resource Allocation on this node pool, we must configure specific node labels to opt in to GKE managed DRA drivers instead of the default device plugins:
* **`cloud.google.com/gke-networking-dra-driver=true`**: This label tells the GKE control plane that this node pool is opted-in to GKE managed DRANet. GKE will automatically deploy and manage the lifecycle of the `networking-dra-driver` DaemonSet Pods on nodes in this pool to make network interfaces discoverable and allocatable.
* **`cloud.google.com/gke-nvidia-gpu-dra-driver=true`** and **`gke-no-default-nvidia-gpu-device-plugin=true`**: These labels instruct GKE to disable the standard GPU device plugin on these nodes and instead enable the NVIDIA GPU DRA driver to handle hardware resource slicing and allocations.

We enable GKE Managed DRANet (which relies on GKE Dataplane V2 for low-latency container networking, detailed in the [GKE DRA Networking guide](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/allocate-network-resources-dra)).

```bash
gcloud beta container node-pools create a3u-pool \
    --project="${PROJECT_ID}" \
    --cluster="${CLUSTER_NAME}" \
    --location="${ZONE}" \
    --node-locations="${ZONE}" \
    --machine-type=a3-ultragpu-8g \
    --accelerator=type=nvidia-h200-141gb,count=8,gpu-driver-version=disabled \
    --reservation-affinity=specific \
    --reservation="${RESERVATION_NAME}" \
    --accelerator-network-profile=auto \
    --node-labels=cloud.google.com/gke-networking-dra-driver=true,nvidia.com/gpu.present=true,gke-no-default-nvidia-gpu-device-plugin=true,cloud.google.com/gke-nvidia-gpu-dra-driver=true \
    --num-nodes=1
```

## Configure Kubectl to communicate with your cluster

To configure kubectl to communicate with your cluster, run the following
command:

```bash
gcloud container clusters get-credentials "${CLUSTER_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}"
```

## Verify GKE Managed Networking Driver

Since we enabled the DRANet driver via node labels during node pool creation,
GKE automatically manages its lifecycle.

1. **Verify Driver Pods:**
   The driver runs in the `gke-managed-networking-dra-driver` namespace.

   ```bash
   kubectl get pods -n gke-managed-networking-dra-driver
   ```

   Ensure the pods are in `Running` state on your GPU nodes.

2. **Verify ResourceSlice:**
   Verify that the network driver has published a `ResourceSlice` object
   that lists the network interfaces on the node:

    ```bash
    kubectl get resourceslices --field-selector=spec.driver=dra.net -o yaml
    ```

    You should see the description of the physical networking devices,
    demonstrating how different interfaces are partitioned across separate
    NUMA nodes and PCIe switches:

    ```yaml
    apiVersion: v1
    items:
    - apiVersion: resource.k8s.io/v1
      kind: ResourceSlice
      metadata:
        name: <node-name>-dra.net-<suffix>
      spec:
        driver: dra.net
        nodeName: <node-name>
        pool:
          name: <node-name>
        devices:
        - name: pci-0000-91-00-0
          attributes:
            dra.net/ifName:
              string: gpu0rdma0
            dra.net/numaNode:
              int: 0
            dra.net/rdma:
              bool: true
            resource.kubernetes.io/pcieRoot:
              string: pci0000:8c
        # ... (other network interfaces)
        - name: pci-0000-cd-00-0
          attributes:
            dra.net/ifName:
              string: gpu6rdma0
            dra.net/numaNode:
              int: 1
            dra.net/rdma:
              bool: true
            resource.kubernetes.io/pcieRoot:
              string: pci0000:c8
    kind: List
    metadata:
      resourceVersion: ""
    ```

## Install the NVIDIA GPU driver

Since we disabled the installation of the GPU Device Plugin at node pool creation time, we need to install the NVIDIA GPU driver manually.

```bash
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml
```

## Install the NVIDIA GPU DRA driver

We install the NVIDIA GPU DRA driver using a Helm chart from the official Kubernetes OCI registry. Make sure that you have Helm installed. If not, you can follow the [Helm documentation](https://helm.sh/docs/intro/install/) to install it.

```bash
helm install dra-driver-nvidia-gpu oci://registry.k8s.io/dra-driver-nvidia/charts/dra-driver-nvidia-gpu \
    --version="0.4.0" --create-namespace --namespace=dra-driver-nvidia-gpu \
    --set nvidiaDriverRoot="/home/kubernetes/bin/nvidia/" \
    --set gpuResourcesEnabledOverride=true \
    --set resources.computeDomains.enabled=false \
    --set kubeletPlugin.priorityClassName="" \
    --set 'kubeletPlugin.tolerations[0].operator=Exists' # Needed to ensure the driver can run on tainted GPU nodes
```

### Verify that the NVIDIA GPU DRA driver is working

Check that the NVIDIA GPU DRA driver is installed and working by inspecting the driver pod:

```bash
kubectl -n dra-driver-nvidia-gpu get pods
```

The pod should be in a `Running` state. If not, you can inspect the logs with:

```bash
kubectl -n dra-driver-nvidia-gpu logs -l app.kubernetes.io/name=dra-driver-nvidia-gpu -c gpus
```

Verify that the driver has published a `ResourceSlice` object that lists the GPU on the node:

**Note:** It might take a minute or two for the driver to fully initialize and publish the `ResourceSlice` after installation.

```bash
kubectl get resourceslices --field-selector=spec.driver=gpu.nvidia.com -o yaml
```

You should see the description of the GPU:

```yaml
apiVersion: v1
items:
- apiVersion: resource.k8s.io/v1
  kind: ResourceSlice
  metadata:
    name: 00000-gpu.nvidia.com-<suffix>
  spec:
    driver: gpu.nvidia.com
    nodeName: <node-name>
    pool:
      generation: 1
      name: <node-name>
      resourceSliceCount: 1
    devices:
    - name: gpu-0
      attributes:
        addressingMode:
          string: None
        architecture:
          string: Hopper
        brand:
          string: Nvidia
        cudaComputeCapability:
          version: 9.0.0
        cudaDriverVersion:
          version: 13.0.0
        driverVersion:
          version: 580.126.20
        productName:
          string: NVIDIA H200
        resource.kubernetes.io/pciBusID:
          string: "0000:8f:00.0"
        resource.kubernetes.io/pcieRoot:
          string: pci0000:8c
        type:
          string: gpu
        uuid:
          string: GPU-759779bb-6a40-ec83-0c00-8a7ae577a6a8
      capacity:
        memory:
          value: 143771Mi
    # ... (other physical GPUs on the node)
kind: List
metadata:
  resourceVersion: ""
```

## Install the CPU DRA Driver

Install the CPU DRA Driver:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/dra-driver-cpu/releases/download/v0.1.0/install.yaml
```

**Verification:**

1. **Verify Driver Pods:**
   Check that the CPU driver pods are running in the `kube-system` namespace:
   ```bash
   kubectl get pods -n kube-system -l app.kubernetes.io/name=dra-driver-cpu
   ```
   Ensure they are in `Running` state.

2. **Verify ResourceSlice:**
   Check that the CPU driver has successfully published `ResourceSlice` objects to represent your CPU topology:
    ```bash
    kubectl get resourceslices --field-selector=spec.driver=dra.cpu
    ```

    Inspect the published CPU ResourceSlice details to confirm NUMA node groupings:
    ```bash
    kubectl get resourceslice <your-cpu-slice-name> -o yaml
    ```

   You should see the CPU devices partitioned by NUMA domain with attributes representing their NUMA groupings:
   ```yaml
   spec:
     driver: dra.cpu
     devices:
     - name: cpudevnuma000
       attributes:
         dra.cpu/numCPUs: { int: 112 }
         dra.cpu/numaNodeID: { int: 0 }
         dra.net/numaNode: { int: 0 }
       capacity:
         dra.cpu/cpu: { value: "112" }
     - name: cpudevnuma001
       attributes:
         dra.cpu/numCPUs: { int: 112 }
         dra.cpu/numaNodeID: { int: 1 }
         dra.net/numaNode: { int: 1 }
       capacity:
         dra.cpu/cpu: { value: "112" }
   ```

## Create the DRA Claim with Scoped Alignment Constraints

This is the core part of the tutorial. We create a combined
`ResourceClaim` that requests all three resources and defines the alignment constraints.

Save the following as `aligned-claim.yaml` and apply it:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: aligned-resource-claim
  namespace: default
spec:
  devices:
    requests:
    - name: gpu-req
      exactly:
        deviceClassName: gpu.nvidia.com
    - name: nic-req
      exactly:
        deviceClassName: mrdma.google.com
    - name: cpu-req
      exactly:
        deviceClassName: dra.cpu
        capacity:
          requests:
            dra.cpu/cpu: "4" # Request 4 exclusive CPU cores from the aligned NUMA domain
    constraints:
    - matchAttribute: "resource.kubernetes.io/pcieRoot" # Align GPU and NIC to the same PCIe root complex
      requests:
      - gpu-req
      - nic-req
    - matchAttribute: "dra.net/numaNode" # Align CPU and NIC to the same NUMA node
      requests:
      - cpu-req
      - nic-req
```

```bash
kubectl apply -f aligned-claim.yaml
```

## Deploy the Workload

Next, we deploy a Pod that consumes the allocated resources via the resource claim.

For the purpose of this tutorial, the Pod is configured to use the official CUDA development container image. At startup,
the container dynamically installs development dependencies, clones, and compiles NVIDIA's `nvbandwidth` utility,
and then sleeps. This sets up a ready-to-use benchmarking environment so we can subsequently run diagnostic latency
and network tests to physically measure the alignment benefit.

Save the following as `pod.yaml` and apply it:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: aligned-gpu-nic-cpu-pod
  namespace: default
spec:
  tolerations:
  - key: "nvidia.com/gpu"
    operator: "Equal"
    value: "present"
    effect: "NoSchedule"
  containers:
  - name: workload-container
    image: nvidia/cuda:12.6.0-devel-ubuntu22.04
    command: ["/bin/sh", "-c"]
    args:
    - |
      set -e
      apt-get update && apt-get install -y git cmake libboost-program-options-dev numactl
      git clone https://github.com/NVIDIA/nvbandwidth.git
      cd nvbandwidth
      cmake .
      make
      echo "NVBANDWIDTH BUILD COMPLETE. SLEEPING..."
      sleep infinity
    resources:
      claims:
      - name: aligned-devices
      requests:
        cpu: "4" # Standard request matching claim request
        memory: "8Gi"
      limits:
        cpu: "4"
        memory: "8Gi"
  resourceClaims:
  - name: aligned-devices
    resourceClaimName: aligned-resource-claim
```

```bash
kubectl apply -f pod.yaml
```

**Note on Duplicate CPU Requests:**

In the Pod specification above, you will notice we duplicate our CPU requests: we declare `cpu: "4"` inside standard `resources.requests` and also request `4` aligned CPUs via the DRA claim (`aligned-devices` pointing to `cpu-req`).

1. **Standard CPU requests** are needed so that the Kubernetes control plane registers host capacity reservation and Kubelet places the Pod in the `Guaranteed` QoS class (enabling exclusive cgroup boundaries).
2. **DRA CPU requests** are needed so that the [dra-driver-cpu](https://github.com/kubernetes-sigs/dra-driver-cpu) driver and its NRI container runtime plugin can dynamically discover, NUMA-align, and pin the specific physical cores on the host.

**The Long-term Resolution:**
In future Kubernetes versions, this workaround will be obsolete. Kubernetes [KEP-5517: DRA: Node Allocatable Resources](https://github.com/kubernetes/enhancements/issues/5517) proposes a unified resource model. Under this KEP, `kube-scheduler` and `kubelet` will natively synchronize core system resources allocated via DRA claims. This automatically integrates DRA allocations directly into scheduler capacity accounting and node-level enforcement, completely removing the need to duplicate resource declarations in Pod specifications.

## Verify Physical Alignment

Ensure that your pod reaches the `Running` state:

```bash
kubectl get pods aligned-gpu-nic-cpu-pod
```

### Inspect Resolved Claim Allocations

Inspect the allocated `ResourceClaim` status to verify the matched topology pools and physical devices assigned by the
scheduler:
```bash
kubectl get resourceclaim aligned-resource-claim -o yaml
```

Under the `status.allocation.devices.results` section, you will see exactly which physical devices were bound to each
request:
```yaml
status:
  allocation:
    devices:
      results:
      - device: gpu-7
        driver: gpu.nvidia.com
        pool: <node-name>
        request: gpu-req
      - device: pci-0000-cd-00-0
        driver: dra.net
        pool: <node-name>
        request: nic-req
      - consumedCapacity:
          dra.cpu/cpu: "4"
        device: cpudevnuma001
        driver: dra.cpu
        pool: <node-name>
        request: cpu-req
```

### Map Topology Alignment

To trace your allocated devices back to their physical topology attributes, query the published node `ResourceSlices`
using the driver-specific field selectors:

#### Query the Allocated GPU Device's PCIe Root

```bash
kubectl get resourceslices --field-selector=spec.driver=gpu.nvidia.com -o yaml
```
Find the device matching your allocated name (e.g., `gpu-7`) and identify its PCIe root identifier:
```yaml
      resource.kubernetes.io/pcieRoot:
        string: pci0000:c8
```

#### Query the Allocated NIC's PCIe Root & NUMA Node

```bash
kubectl get resourceslices --field-selector=spec.driver=dra.net -o yaml
```
Find the device matching your allocated name (e.g., `pci-0000-cd-00-0`) and identify its PCIe root switch and NUMA
socket mappings:
```yaml
      dra.net/numaNode:
        int: 1
      resource.kubernetes.io/pcieRoot:
        string: pci0000:c8
```

#### Query the Allocated CPU NUMA Node
```bash
kubectl get resourceslices --field-selector=spec.driver=dra.cpu -o yaml
```
Find the device matching your allocated name (e.g., `cpudevnuma001`) and identify its NUMA socket mapping:

```yaml
      dra.net/numaNode:
        int: 1
```

Because the GPU and NIC share the same PCIe root complex, and the CPU and NIC share the same physical NUMA socket,
**all three resources are co-located**.

## Benchmarking with and without alignment

To prove the physical co-location benefits of dynamic resource allocation, we run micro-benchmarks in both
**aligned** and **misaligned** container placement layouts.

We utilize two benchmarking tools:
1. **[`nvbandwidth`](https://github.com/NVIDIA/nvbandwidth) (GPU-to-CPU Latency)**:
   An open-source NVIDIA utility designed to measure memory throughput and latency. We perform pointer chase
   latency micro-benchmarks (`host_device_latency_sm`).
2. **[`perftest`](https://enterprise-support.nvidia.com/s/article/perftest-package) (CPU-to-NIC Latency)**:
   The official suite of Mellanox/NVIDIA RDMA micro-benchmarks. We utilize **`ib_write_lat`** (RDMA Write Latency) in
   loopback mode to measure the packet transaction latency between the CPU host memory and the local network adapter.

### Deploy the Misaligned Workload

The default aligned pod (`aligned-gpu-nic-cpu-pod`) is already running. To measure the latency cost
of cross-socket traffic, we declare a negative constraint to force host CPU exclusive cores to be allocated on
NUMA Socket 1, while the GPU and NIC remain co-located on Socket 0.

The Kubernetes DRA API provides a **[`distinctAttribute`](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-resource-sharing/#dynamic-resource-allocation)**
field, which instructs the scheduler to ensure that selected requests are allocated resources with *distinct* values
for that attribute.

#### Create and Apply the Misaligned Claim

Save the following manifest as `misaligned-claim.yaml` and apply it:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: misaligned-resource-claim
  namespace: default
spec:
  devices:
    requests:
    - name: gpu-req
      exactly:
        deviceClassName: gpu.nvidia.com
    - name: nic-req
      exactly:
        deviceClassName: mrdma.google.com
    - name: cpu-req
      exactly:
        deviceClassName: dra.cpu
        capacity:
          requests:
            dra.cpu/cpu: "4" # Request 4 exclusive CPU cores
    constraints:
    - matchAttribute: "resource.kubernetes.io/pcieRoot" # GPU and NIC share the same PCIe root switch
      requests:
      - gpu-req
      - nic-req
    - distinctAttribute: "dra.net/numaNode" # FORCE host CPU to land on a DIFFERENT NUMA socket than the NIC
      requests:
      - cpu-req
      - nic-req
```
Apply the claim:
```bash
kubectl apply -f misaligned-claim.yaml
```

#### Deploy the Misaligned Workload Pod

Save the following manifest as `misaligned-pod.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: misaligned-gpu-nic-cpu-pod
  namespace: default
spec:
  tolerations:
  - key: "nvidia.com/gpu"
    operator: "Equal"
    value: "present"
    effect: "NoSchedule"
  containers:
  - name: workload-container
    image: nvidia/cuda:12.6.0-devel-ubuntu22.04
    command: ["/bin/sh", "-c"]
    args:
    - |
      set -e
      apt-get update && apt-get install -y git cmake libboost-program-options-dev numactl
      git clone https://github.com/NVIDIA/nvbandwidth.git
      cd nvbandwidth
      cmake .
      make
      echo "NVBANDWIDTH BUILD COMPLETE. SLEEPING..."
      sleep infinity
    resources:
      claims:
      - name: misaligned-devices
      requests:
        cpu: "4"
        memory: "8Gi"
      limits:
        cpu: "4"
        memory: "8Gi"
  resourceClaims:
  - name: misaligned-devices
    resourceClaimName: misaligned-resource-claim
```

Apply the manifest:

```bash
kubectl apply -f misaligned-pod.yaml
```

Inspect the resolved `misaligned-resource-claim` claim status to verify that the scheduler successfully solved the negative socket co-location equation:

```bash
kubectl get resourceclaim misaligned-resource-claim -o yaml
```

Under `status.allocation.devices.results`, you can verify that the GPU request (`gpu-req`) and NIC request (`nic-req`) resolve to co-located devices on Socket 0, while the CPU request (`cpu-req`) is forced onto a different NUMA Socket.

To verify physical misalignment, follow the identical `kubectl get resourceslices` query approach detailed in the Aligned section, confirming that your host CPU cores reside on a different NUMA socket than the network device.

### GPU-to-CPU Latency Test (`nvbandwidth`)

We run `nvbandwidth` pointer chase test (`host_device_latency_sm`) inside both pods to measure the latency
penalty of crossing the inter-socket UPI bus.

#### Aligned Run
```bash
kubectl exec aligned-gpu-nic-cpu-pod -c workload-container -- ./nvbandwidth/nvbandwidth -t host_device_latency_sm -d -i 100 -m
```

*Observed Output:*
```text
SUM host_device_latency_sm 1191.19
```

#### Misaligned Run
```bash
kubectl exec misaligned-gpu-nic-cpu-pod -c workload-container -- ./nvbandwidth/nvbandwidth -t host_device_latency_sm -d -i 100 -m
```

*Observed Output:*
```text
SUM host_device_latency_sm 1321.51
```

**Note:**

We pass the `-d` / `--disableAffinity` flag to `nvbandwidth` in both tests. In the misaligned test, running without `-d` will cause `nvbandwidth` to crash immediately on startup with `NVML_ERROR: [Unknown Error] in setOptimalCpuAffinity()`. This happens because NVML attempts to override process thread bindings to target the remote GPU's native socket, which is strictly blocked because of CPU DRA driver core pinning. Passing `-d` bypasses these NVML affinity overrides and allows the test to successfully measure the physical cross-socket UPI latency.

### CPU-to-NIC Latency Test (`ib_write_lat`)

We run Mellanox/NVIDIA RDMA loopback write latency tests inside both containers.

#### Aligned Run

Before executing the benchmarks, discover the custom GPUDirect interface name, its assigned IP address, and the corresponding physical Mellanox device name using GKE DRA claims and physical layout mappings:

**Retrieve the Interface Name and IP Address from the ResourceClaim**

Query the `status.devices` section of your allocated `aligned-resource-claim` to find the network metadata:

```bash
kubectl get resourceclaim aligned-resource-claim -o yaml
```

Look under `status.devices` for the `dra.net` network adapter allocation:

```yaml
  devices:
  - device: pci-0000-91-00-0
    driver: dra.net
    networkData:
      interfaceName: gpu0rdma0 # Logical network interface name inside the container
      ips:
      - 10.33.0.2/32           # Assigned GPUDirect IP address
```

This confirms that the allocated interface name is **`gpu0rdma0`** and the IP address is **`10.33.0.2`**.

**Map the Logical Interface to the Physical Mellanox Device**

Query the physical InfiniBand/Mellanox adapter mapped to `gpu0rdma0`:

```bash
kubectl exec aligned-gpu-nic-cpu-pod -c workload-container -- ls /sys/class/net/gpu0rdma0/device/infiniband
```
*Observed Output:*
```text
mlx5_0
```

**Execute the Aligned Latency Test**

Inside the aligned pod, the exclusive CPU cores allocated via DRA are located on NUMA Socket 0, which matches the
physical NUMA socket of the ConnectX-7 adapter `mlx5_0` (having logical interface IP `10.33.0.2`).

```bash
# Install perftest utilities
kubectl exec aligned-gpu-nic-cpu-pod -c workload-container -- apt-get update && \
kubectl exec aligned-gpu-nic-cpu-pod -c workload-container -- apt-get install -y perftest

# Launch RDMA server (background)
# -d mlx5_0: Target physical ConnectX-7 interface mlx5_0 on Socket 0 (co-located with container's pinned CPU cores)
# -x 0: Select GID index 0 (link-local IPv6 GID) for local loopback communication
kubectl exec aligned-gpu-nic-cpu-pod -c workload-container -- ib_write_lat -d mlx5_0 -x 0 &

# Launch RDMA client (foreground) targeting the local server's interface IP
# 10.33.0.2: The logical GPUDirect network interface IP mapped directly to physical mlx5_0 inside this pod
kubectl exec aligned-gpu-nic-cpu-pod -c workload-container -- ib_write_lat -d mlx5_0 -x 0 10.33.0.2
```

*Observed Output:*
```text
 #bytes #iterations    t_min[usec]    t_max[usec]  t_typical[usec]    t_avg[usec]
 2       1000          1.45           3.88         1.47                1.51
```

#### Misaligned Run

**Retrieve the Interface Name and IP Address from the ResourceClaim**

Query the `status.devices` section of your allocated `misaligned-resource-claim` to find the network metadata:

```bash
kubectl get resourceclaim misaligned-resource-claim -o yaml
```

Look under `status.devices` for the `dra.net` network adapter allocation:

```yaml
  devices:
  - device: pci-0000-98-00-0
    driver: dra.net
    networkData:
      interfaceName: gpu2rdma0 # Logical network interface name inside the container
      ips:
      - 10.148.0.2/32          # Assigned GPUDirect IP address
```

**Map the Logical Interface to the Physical Mellanox Device**

Query the physical InfiniBand/Mellanox adapter mapped to `gpu2rdma0`:

```bash
kubectl exec misaligned-gpu-nic-cpu-pod -c workload-container -- ls /sys/class/net/gpu2rdma0/device/infiniband
```
*Observed Output:*
```text
mlx5_2
```

**Execute the Misaligned Latency Test**

Inside the misaligned container, the CPU exclusive cores reside on NUMA Socket 1, while the physical NIC `mlx5_2`
resides on NUMA Socket 0. This forces transactions to traverse the inter-socket UPI link.

```bash
# Install perftest utilities
kubectl exec misaligned-gpu-nic-cpu-pod -c workload-container -- apt-get update && \
kubectl exec misaligned-gpu-nic-cpu-pod -c workload-container -- apt-get install -y perftest

# Launch RDMA server (background)
# -d mlx5_2: Target physical ConnectX-7 interface mlx5_2 on Socket 0 (forcing cross-socket UPI bus transit to Socket 1)
# -x 0: Select GID index 0 (link-local IPv6 GID) for local loopback communication
kubectl exec misaligned-gpu-nic-cpu-pod -c workload-container -- ib_write_lat -d mlx5_2 -x 0 &

# Launch RDMA client (foreground) targeting the local server's interface IP
# 10.148.0.2: The logical GPUDirect network interface IP mapped directly to physical mlx5_2 inside this pod
kubectl exec misaligned-gpu-nic-cpu-pod -c workload-container -- ib_write_lat -d mlx5_2 -x 0 10.148.0.2
```

*Observed Output:*
```text
 #bytes #iterations    t_min[usec]    t_max[usec]  t_typical[usec]    t_avg[usec]
 2       1000          1.71           5.75         1.74                1.81
```

### Results Comparison

Below are the verified comparative benchmarks obtained from live runs on a GKE A3 Ultra H200 node pool:

| Hardware Path | Test Metric | Aligned Pod | Misaligned Pod | Latency Penalty (Delta %) | Reason |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **GPU $\leftrightarrow$ CPU** | `host_device_latency_sm` (Median) | **1191.19 ns** | **1321.51 ns** | **+10.94%** | Crossing the inter-socket UPI link adds constant physical delay to direct host memory accesses. |
| **CPU $\leftrightarrow$ NIC** | `ib_write_lat` (Median) | **1.47 µs** | **1.74 µs** | **+18.37%** | Crossing NUMA boundaries to write to NIC host ring buffers imposes a latency penalty. |

**Key Conclusion:**
Misaligning the hardware components incurs a **10.9% memory latency penalty** and an **18.4% RDMA network latency penalty**
due to cross-socket UPI transit delays. By linking resources via GKE DRA scheduler constraint parameters, you enforce
complete **CPU-NIC-GPU co-location** on the identical socket and PCIe root complex, unlocking the full hardware capabilities
of GKE A3 Ultra.

## Understanding the DRA Benefit

This tutorial demonstrated how Dynamic Resource Allocation (DRA) enables optimal hardware alignment for co-located resources.
With traditional Device Plugins, workloads are topology-blind at the scheduling phase, leading to suboptimal scheduling choices that incur heavy penalties due to cross-socket communications and host-interconnect hops.

With DRA:
- We achieved perfect co-location by linking GPU to NIC via PCIe switches (`pcieRoot`) and CPU to NIC via NUMA sockets (`numaNode`).
- Workloads execute on localized NUMA and PCIe zones, unlocking the maximum performance.

## Clean up

To avoid incurring charges to your Google Cloud account for the resources that you created in this guide,
run the following command to delete the cluster:

```bash
gcloud container clusters delete "${CLUSTER_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}"
```

