---
build:
  render: never
  list: never
---
### **Why Choose DRA for TPUs?**

Historically, GKE managed hardware accelerators like TPUs through the traditional **Device Plugin** model. While functional, the Device Plugin model relies on static node-level labels and integer device counts, which limits scheduling flexibility.

**Dynamic Resource Allocation (DRA)** represents the future of accelerator management in Kubernetes, decoupling physical hardware configuration from workload scheduling. DRA provides several key advantages for TPU workloads:

* **Advanced Scheduling Features (Prioritized Lists)**: DRA enables sophisticated allocation strategies such as prioritized lists (`firstAvailable`). Workloads can specify a top hardware preference (e.g., TPU v6e) and automatically fall back to alternative hardware (e.g., TPU v5p) if the primary choice is unavailable, eliminating manual intervention and avoiding unschedulable pods.
* **Rich Hardware Context & Topologies**: Traditional scheduling only understands integer device counts (e.g., 4 TPUs). DRA provides the scheduler with deep visibility into multidimensional accelerator characteristics, such as TPU generations, memory capacity, and interconnect topologies (e.g., 2x2x1 slices). *Note: While the TPU DRA driver is in active development and currently requires allocating all chips on a node without subslicing support, DRA lays the foundation for future topology-aware scheduling and subslicing.*
* **Expressive CEL-Based Filtering**: Workloads request TPUs using Common Expression Language (CEL) selectors to match specific device attributes directly within a `ResourceClaim`, rather than relying on rigid node labels or taints.
