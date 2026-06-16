---
title: "Dynamic Resource Allocation"
linkTitle: "Dynamic Resource Allocation"
description: "Learn how to use Dynamic Resource Allocation (DRA) in Kubernetes to optimize the utilization of GPUs and TPUs."
weight: 20
type: docs-root
notoc: true
---

Dynamic Resource Allocation (DRA) is a Kubernetes feature designed to modernize how workloads request and share specialized hardware, such as GPUs and other attached accelerators. By providing an experience similar to how Kubernetes handles storage, DRA allows developers to claim the exact hardware they need without getting bogged down in the manual complexities of per-node device management.

### Why DRA Matters

Historically, Kubernetes managed accelerators through the static Device Plugin model, which treated hardware as simple integer counts (e.g., "1 GPU") and required platform teams to pre-configure rigid, dedicated node pools for every hardware variant or sharing configuration.

DRA shifts this paradigm by enabling:
* **Storage-Like Claims:** Workloads use `ResourceClaims` to dynamically request hardware, decoupling the application requirements from the underlying node configuration.
* **Infrastructure Flexibility:** The same physical hardware pool can be dynamically partitioned or shared (using Time-Slicing, MPS, or MIG) on the fly, depending on active workload requests.
* **Declarative Scheduling Constraints:** Developers can use CEL (Common Expression Language) selectors to request specific hardware attributes (like memory sizes or interconnect topologies), ensuring the scheduler automatically matches the application with the most suitable equipment.

Ultimately, DRA empowers developers to build high-performance applications more efficiently by providing a consistent, self-service, and scalable way to leverage specialized infrastructure across the entire cluster.

### Resources

To learn more about the concepts, specifications, and architecture of DRA, refer to the official documentation:
* [Kubernetes Dynamic Resource Allocation Documentation](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)
* [GKE Dynamic Resource Allocation Documentation](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/about-dynamic-resource-allocation)