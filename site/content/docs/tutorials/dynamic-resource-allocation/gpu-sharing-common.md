---
build:
  render: never
  list: never
---
### GPU Device Sharing Modes

When sharing a single physical GPU among multiple containers or pods, you typically choose between three primary mechanisms. Here is a quick summary of how they work and their tradeoffs:

1.  **Time Slicing (Time Sharing)**:
    *   **How it works**: The GPU's compute resources are shared in time. The GPU context switches between different workloads.
    *   **Pros**: Simple to configure, works on almost all modern GPUs, and has no memory overhead.
    *   **Cons**: No memory isolation (one workload can consume all memory and OOM the other) and potential latency impact if one workload hogs the GPU.
    *   **Best for**: Development, testing, or workloads with low and bursty utilization where hard isolation is not required.

2.  **Multi-Process Service (MPS)**:
    *   **How it works**: Multiple processes share the GPU compute and memory simultaneously (spatial sharing).
    *   **Pros**: Higher utilization and lower overhead than time slicing. It allows concurrent execution of kernels from different processes.
    *   **Cons**: Limited isolation compared to MIG. Memory limits can be set but are not as strictly enforced at the hardware level as MIG.
    *   **Best for**: Cooperative workloads, like running multiple inference servers that trust each other and benefit from high throughput.

3.  **Multi-Instance GPU (MIG)**:
    *   **How it works**: The GPU is physically partitioned into separate "instances," each with its own dedicated compute and memory resources.
    *   **Pros**: Hard isolation, guaranteed QoS (Quality of Service), and complete memory separation.
    *   **Cons**: Rigid partition sizes and requires specific high-end GPUs (Ampere architecture or newer, e.g., A100, H100).
    *   **Best for**: Production workloads requiring strict isolation, predictable performance, and multi-tenancy security.

### Why Choose DRA for GPU Sharing?

While GPU sharing (Time-Slicing, MPS, and MIG) is available through both the standard GKE GPU Device Plugin and Dynamic Resource Allocation (DRA), DRA offers significant advantages in flexibility and management.

#### The Limitations of the Device Plugin

In the traditional Device Plugin model, GPU sharing is "baked into" the node pool. You must specify the sharing mode and the specific partitions or clients (e.g., specific MIG profiles like `1g.5gb`) when you create the node pool. This creates several challenges:

*   **Infrastructure Rigidity**: If a new workload requires a different partition size or sharing mode, you must create a new node pool.
*   **Resource Waste**: A node pool configured for a specific sharing strategy cannot easily adapt to workloads that need a full, non-shared GPU without wasting the other configured slots.
*   **Manual Labeling**: Developers must know which node labels correspond to which sharing modes and manually add `nodeSelector` entries to their manifests.

#### The DRA Advantage: Dynamic, Per-GPU Sharing

DRA shifts the sharing configuration to the **workload manifest**. Instead of targeting a pre-configured node pool, the pod requests a GPU with specific sharing parameters in its `ResourceClaim`.

*   **Dynamic Granularity**: You can request Time-Slicing for a dev workload and MIG or MPS for a production workload, and GKE will dynamically allocate and configure the GPUs to match these requests.
*   **No Fixed Node Pools**: You don't need to pre-build node pools for every possible sharing ratio or partition size. The same underlying hardware can be partitioned differently for each claim.
*   **Developer-Centric**: Developers define their resource needs directly in their YAML, and GKE handles the infrastructure orchestration to meet those needs.

This transition from infrastructure-level configuration to workload-level requests makes DRA a more flexible, scalable, and efficient solution for multi-tenant GPU environments.
