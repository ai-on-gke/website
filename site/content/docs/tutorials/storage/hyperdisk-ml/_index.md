---
linkTitle: "Hyperdisk ML Disk"
title: "Populate a Hyperdisk ML Disk from Google Cloud Storage"
description: "This guide uses the Google Cloud API to create a Hyperdisk ML disk from data in Cloud Storage and then use it in a GKE cluster. Refer to [this documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/hyperdisk-ml) for instructions all in the GKE API."
weight: 30
owner:
  - name: "Brian Kaufman"
    link: "https://github.com/bkauf"
type: docs
tags:
  - Storage
  - Tutorials
cloudShell: 
    enabled: true
    folder: site/content/docs/tutorials/storage/hyperdisk-ml
    editorFile: index.md
---

## Overview

This guide uses the Google Cloud API to create a Hyperdisk ML disk from data in Cloud Storage and then use it in a GKE cluster. Refer to [this documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/hyperdisk-ml) for instructions all in the GKE API

## Before you begin

1. Ensure you have a GCP project with billing enabled and have enabled the GKE API.

   * Follow [this link](https://cloud.google.com/billing/v1/getting-started) to learn how to enable billing for your project.

   * GCE and GKE APIs can be enabled by running:

     ```
     gcloud services enable compute.googleapis.com
     gcloud services enable container.googleapis.com
     ```


1. Ensure you have the following tools installed on your workstation:

   * [gcloud](https://cloud.google.com/sdk/docs/install)
   * [kubectl](https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl#install_kubectl)
   * [terraform](https://developer.hashicorp.com/terraform/tutorials/aws-get-started/install-cli)
   * [helm](https://helm.sh/docs/intro/install/)


## Setting up your cluster and Hyperdisk ML Disk

1. Set the default environment variables:

    ```sh
    export VM_NAME=hydrator
    export MACHINE_TYPE=c3-standard-44
    export IMAGE_FAMILY=debian-12
    export IMAGE_PROJECT=debian-cloud
    export ZONE=us-central1-a
    export SNAP_SHOT_NAME=hdmlsnapshot
    export PROJECT_ID=$(gcloud config get project)
    export DISK_NAME=model1
    ```

1. Create a new GCE instance that you will use to hydrate the new Hyperdisk ML disk with data. Note a c3-standard-44 instance is used to provide the max throughput while populating the hyperdisk([Instance to throughput rates](https://cloud.google.com/compute/docs/disks/hyperdisks#performance_limits_for_other_vms)).

    ```sh
    gcloud compute instances create $VM_NAME \
        --image-family=$IMAGE_FAMILY \
        --image-project=$IMAGE_PROJECT \
        --zone=$ZONE \
        --machine-type=$MACHINE_TYPE
    ```
1. Create and attach the disk to the new GCE VM.

    ```sh
    SIZE=140
    THROUGHPUT=2400

    gcloud compute disks create $DISK_NAME --type=hyperdisk-ml \
    --size=$SIZE --provisioned-throughput=$THROUGHPUT  \
    --zone $ZONE

    gcloud compute instances attach-disk $VM_NAME --disk=$DISK_NAME --zone=$ZONE 
    ```

## Create a template snapshot of the disk with the content of the GCS bucket

1. Get your current IP to create a new Firewall rule
    ```sh
    curl ifconfig.me
    ```

1. Replace your network and add a Firewall rule to enable SSH access into the virutal machine
     ```sh
    gcloud compute firewall-rules create allow-ssh-ingress-from-iap \
        --direction=INGRESS \
        --action=allow \
        --rules=tcp:22 \
        --source-ranges=<replace-your-ip>/20
    ```

1. Log into the virtual machine

    ```sh
    gcloud compute ssh $VM_NAME
    ```

1. Update and authenticate the instance

    ```sh
    sudo apt-get update
    sudo apt-get install google-cloud-cli
    gcloud init
    gcloud auth login
    ```

1. Identify the device name (eg: */dev/nvme0n2*) by looking at the output of lsblk. This should correspond to the disk that was attached in the previous step. 

    ```sh 
    lsblk
    ```

1. Save device name given by **lsblk** command (example */dev/nvme0n2*)
    ```sh
    DEVICE=/dev/nvme0n2
    ```

1. Mount the disk and copy the content form the GCS bucket into the disk

    ```sh
    GCS_DIR=gs://vertex-model-garden-public-us/llama3.3/Llama-3.3-70B-Instruct
    sudo /sbin/mkfs -t ext4 -E lazy_itable_init=0,lazy_journal_init=0,discard $DEVICE

    sudo mount $DEVICE /mnt
    sudo gcloud storage cp -r $GCS_DIR /mnt
    sudo umount /mnt
    ```

1. Close the connection to the VM
    ```sh
    exit
    ```

1. Detach disk from the hydrator and switch to READ_ONLY_MANY access mode.
    ```sh
    gcloud compute instances detach-disk $VM_NAME --disk=$DISK_NAME --zone=$ZONE
    gcloud compute disks update $DISK_NAME --access-mode=READ_ONLY_MANY  --zone=$ZONE
    ```

1. Create a snapshot from the disk to use as a template.
    ```sh
    gcloud compute snapshots create $SNAP_SHOT_NAME \
        --source-disk-zone=$ZONE \
        --source-disk=$DISK_NAME \
        --project=$PROJECT_ID
    ```

## Delete the VM and connect the disk to the GKE cluster

1. You now have a hyperdisk ML snapshot populated with your data from Google Cloud Storage. You can delete the hydrator GCE instance and the original disk.

    ```sh
    gcloud compute instances delete $VM_NAME --zone=$ZONE
    gcloud compute disks delete $DISK_NAME --project $PROJECT_ID --zone $ZONE
    ```

1. In your GKE cluster create your Hypedisk ML multi zone and Hyperdisk ML storage classes. Hyperdisk ML disks are zonal and the *Hyperdisk-ml-multi-zone* storage class automatically provisions disks in zones where the pods using them are. 
Replace the zones in this class with the zones you want to allow the Hyperdisk ML snapshot to create disks in. 

    ```yaml
    apiVersion: storage.k8s.io/v1
    kind: StorageClass
    metadata:
    name: hyperdisk-ml-multi-zone
    parameters:
    type: hyperdisk-ml
    provisioned-throughput-on-create: "2400Mi"
    enable-multi-zone-provisioning: "true"
    provisioner: pd.csi.storage.gke.io
    allowVolumeExpansion: false
    reclaimPolicy: Delete
    volumeBindingMode: Immediate
    allowedTopologies:
    - matchLabelExpressions:
    - key: topology.gke.io/zone
        values:
        - us-central1-a
        - us-central1-c
    --- 
    apiVersion: storage.k8s.io/v1
    kind: StorageClass
    metadata:
        name: hyperdisk-ml
    parameters:
        type: hyperdisk-ml
    provisioner: pd.csi.storage.gke.io
    allowVolumeExpansion: false
    reclaimPolicy: Delete
    volumeBindingMode: WaitForFirstConsumer
    ```

1. Create a **volumeSnapshotClass** and **VolumeSnapshotContent** config to use your snapshot. Replace the *VolumeSnapshotContent.spec.source.snapshotHandle* with the path to your snapshot. 

    ```yaml
    apiVersion: snapshot.storage.k8s.io/v1
    kind: VolumeSnapshotClass
    metadata:
    name: my-snapshotclass
    driver: pd.csi.storage.gke.io
    deletionPolicy: Delete
    ---
    apiVersion: snapshot.storage.k8s.io/v1
    kind: VolumeSnapshot
    metadata:
    name: restored-snapshot
    spec:
    volumeSnapshotClassName: my-snapshotclass
    source:
        volumeSnapshotContentName: restored-snapshot-content
    ---
    apiVersion: snapshot.storage.k8s.io/v1
    kind: VolumeSnapshotContent
    metadata:
    name: restored-snapshot-content
    spec:
    deletionPolicy: Retain
    driver: pd.csi.storage.gke.io
    source:
        snapshotHandle: projects/[project_ID]/global/snapshots/[snapshotname]
    volumeSnapshotRef:
        kind: VolumeSnapshot
        name: restored-snapshot
        namespace: default
    ```

1. Reference your snapshot in the persistent volume claim. Be sure to adjust the *spec.dataSource.name* and *spec.resources.requests.storage* to your snapshot name and size.

    ```yaml
    kind: PersistentVolumeClaim
    apiVersion: v1
    metadata:
    name: hdml-consumer-pvc
    spec:
    dataSource:
        name: restored-snapshot
        kind: VolumeSnapshot
        apiGroup: snapshot.storage.k8s.io
    accessModes:
    - ReadOnlyMany
    storageClassName: hyperdisk-ml-multi-zone
    resources:
        requests:
        storage: 140Gi
    ```

1. Add a reference to this PVC in your deployment *spec.template.spec.volume.persistentVolumeClaim.claimName* parameter. 

    ```yaml
    ---
    apiVersion: apps/v1
    kind: Deployment
    metadata:
    name: busybox
    labels:
        app: busybox
    spec:
    replicas: 1
    selector:
        matchLabels:
        app: busybox
    strategy:
        type: Recreate
    template:
        metadata:
        labels:
            app: busybox
        spec:
        containers:
        - image: busybox:latest
            name: busybox
            command:
            - "sleep"
            - "infinity"
            volumeMounts:
            - name: busybox-persistent-storage
            mountPath: /var/www/html
        volumes:
        - name: busybox-persistent-storage
            persistentVolumeClaim:
            claimName: hdml-consumer-pvc
    ```