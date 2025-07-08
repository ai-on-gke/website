---
linkTitle: "Hugging Face to GCS"
title: "Load Hugging Face Models into Cloud Storage"
description: "This guide provides instructions for how to hydrate GCS buckets with models from Hugging Face with a Kubernetes Job."
weight: 30
type: docs
owner:
  - name: "amacaskill"
    link: "https://github.com/amacaskill"
tags:
  - Storage
  - Tutorials
draft: false
cloudShell:
    enabled: true
    folder: site/content/docs/tutorials/storage/hf-gcs-transfer
    editorFile: index.md
---

## Overview

This guide uses a Kubernetes Job to load [meta-llama/Meta-Llama-3-8B](https://huggingface.co/meta-llama/Meta-Llama-3-8B) model weights hosted on Hugging Face, into a Cloud Storage Bucket, which is used in a vLLM model deployment.

## Before you begin

1. Ensure you have a GCP project with billing enabled and have enabled the GKE and Cloud Storage APIs.

   * Follow [this link](https://cloud.google.com/billing/v1/getting-started) to learn how to enable billing for your project.

   * The GKE and Cloud Storage APIs can be enabled by running:

     ```bash
     gcloud services enable container.googleapis.com
     gcloud services enable storage.googleapis.com
     ```

1. Ensure you have the following tools installed on your workstation:

   * [gcloud](https://cloud.google.com/sdk/docs/install)
   * [kubectl](https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl#install_kubectl)

1. Configure access to Hugging Face models. 

   * Create a [Hugging Face](https://huggingface.co/) account, if you don't already have one.
   * To get access to the Llama models for deployment to GKE, you must first sign the [meta-llama/Meta-Llama-3-8B](https://huggingface.co/meta-llama/Meta-Llama-3-8B) license consent agreement. 
   * You will also need to [generate a Hugging Face access token](https://huggingface.co/settings/tokens). Make sure the token has `Read` permission.


## Set up your GKE Cluster

Let’s start by setting a few environment variables that will be used throughout this post. You should modify these variables to meet your environment and needs.

Run the following commands to set the env variables and make sure to replace `<my-project-id>`, `<your-hf-token>`, and `<your-hf-username>` with your own values:

```bash
gcloud config set project <my-project-id>
export PROJECT_ID=$(gcloud config get project)
export REGION=us-central1
export HF_TOKEN=<your-hf-token>
export HF_USER=<your-hf-username>
export CLUSTER_NAME=hf-gcs-transfer
```

>[!NOTE]
>You might have to rerun the export commands if for some reason you reset your shell and the variables are no longer set. This can happen for example when your Cloud Shell disconnects.

Create a GKE Autopilot cluster by running the following command. If you choose to create a GKE standard cluster, you will need enable [Workload Identity Federation for GKE](https://cloud.google.com/kubernetes-engine/docs/concepts/workload-identity), and the [Cloud Storage FUSE CSI Driver](https://cloud.google.com/kubernetes-engine/docs/how-to/cloud-storage-fuse-csi-driver-setup#enable) on your cluster.
 
```bash
gcloud container clusters create-auto ${CLUSTER_NAME} \
  --project=${PROJECT_ID} \
  --region=${REGION} \
  --labels=created-by=ai-on-gke,guide=hf-gcs-transfer
```

### Create a Kubernetes secret for Hugging Face credentials

In your shell session, do the following:

1. Configure `kubectl` to communicate with your cluster:

    ```bash
    gcloud container clusters get-credentials ${CLUSTER_NAME} --location=${REGION}
    ```

1. Create a Kubernetes Secret that contains your Hugging Face token. This is only required for [gated models](https://huggingface.co/docs/hub/models-gated):

    ```bash
    kubectl create secret generic hf-secret \
      --from-literal=hf_api_token=${HF_TOKEN} \
      --dry-run=client -o yaml | kubectl apply -f -
    ```


## Create your Cloud Storage bucket

Now, [create the Cloud Storage bucket with hierarchical namespace enabled](https://cloud.google.com/storage/docs/create-hns-bucket) for the model weights, by running the following command. We recommend that you enable hierarchical namespace in your Cloud Storage bucket to improve read performance of your LLM.

>[!NOTE]
>Cloud Storage bucket names must conform to the [naming requirements](https://cloud.google.com/storage/docs/buckets#naming), and in order to get the required permissions for creating a Cloud Storage bucket, you must have the Storage Admin (`roles/storage.admin`) IAM role for the project where the bucket is created. See [Required roles](https://cloud.google.com/storage/docs/creating-buckets#required-roles) for details.

```bash
export BUCKET_NAME=${PROJECT_ID}-hf-gcs-transfer
export BUCKET_URI=gs://${BUCKET_NAME}
gcloud storage buckets create ${BUCKET_URI} --project=${PROJECT_ID} --uniform-bucket-level-access --enable-hierarchical-namespace
```

## Deploy the Kubernetes Job to populate the Cloud Storage Bucket

1. Configure access for the `producer-job` Job, to the Cloud Storage bucket. 

    To make your Cloud Storage bucket accessible by your GKE cluster, authenticate using Workload Identity Federation for GKE with the Cloud Storage bucket. 
    >[!NOTE] 
    > If you don't have Workload Identity Federation for GKE enabled, follow [these steps](https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity#enable_on_clusters_and_node_pools) to enable it.

    Grant the Storage Admin (`roles/storage.admin`) IAM role for Cloud Storage to the Kubernetes ServiceAccount by running the following commands. If you are using a custom workload identity pool, you will need to update the workload identity pool name in the command below. Custom Workload Identitiy pools are not supported in Autopilot clusters.

    ```bash
    export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
    export WORKLOAD_IDENTITY_POOL=${PROJECT_ID}.svc.id.goog
    export NAMESPACE=default
    export SERVICE_ACCOUNT=hf-sa
    gcloud storage buckets add-iam-policy-binding ${BUCKET_URI} \
    --member "principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WORKLOAD_IDENTITY_POOL}/subject/ns/${NAMESPACE}/sa/${SERVICE_ACCOUNT}" \
    --role "roles/storage.admin"
    ```

1. Save the following file to a file named producer-job.yaml

    ```bash
    apiVersion: batch/v1
    kind: Job
    metadata:
      name: producer-job
      namespace: "${NAMESPACE}"
      annotations:
        hf-gcs-transfer-ai-on-gke: "true"
    spec:
      template:
        spec:
          serviceAccountName: "${SERVICE_ACCOUNT}"
          # Without this, the job will run on an E2 machine, which results in a slower transfer time.
          affinity:
            nodeAffinity:
              requiredDuringSchedulingIgnoredDuringExecution:
                nodeSelectorTerms:
                - matchExpressions:
                  - key: cloud.google.com/machine-family
                    operator: In
                    values:
                    - "c3"
          initContainers:
          - name: download
            image: ubuntu:22.04
            resources:
              # Need a big enough machine that can fit the full model in RAM, with some buffer room. If you are deploying a larger model, you MUST increase this value to prevent the Pod from being evicted for using too much memory.
              requests:
                memory: "${MEMORY_REQUIREMENTS}"
              limits:
                memory: "${MEMORY_REQUIREMENTS}"
            command: ["bash", "-c"]
            args:
            - |
              start=$(date +%s)
              apt-get update && apt-get install -y aria2 git

              # Get directory name from MODEL_ID (e.g., "meta-llama/Meta-Llama-3-8B" -> "Meta-Llama-3-8B")
              git_dir="${MODEL_ID##*/}"

              hf_endpoint="https://huggingface.co"
              download_url="https://$HF_USER:$HF_TOKEN@${hf_endpoint#https://}/$MODEL_ID"
              echo "INFO: Cloning model repository metadata into '$git_dir'..."
              GIT_LFS_SKIP_SMUDGE=1 git clone $download_url && cd $git_dir

              # remove files we don't want to upload
              rm -r -f .git
              rm -r -f .gitattributes
              rm -r -f original
              cd ..

              # Get the list of files.
              file_list=($(find "$git_dir/" -type f -name "[!.]*" -print))

              # Strip git dir path.
              files=()
              for file in "${file_list[@]}"; do
                trimmed_file="${file#$git_dir/}"
                files+=("$trimmed_file")
              done

              # Create a file that maps each URL to its desired relative filename.
              # This is needed because aria2c uses the file's content hash as the identifier.
              > download_list.txt # Create or clear the file

              for file in "${files[@]}"; do
                  url="$hf_endpoint/$MODEL_ID/resolve/main/$file"
                  # Write the URL and the desired filename, separated by a space, on the same line
                  echo "$url $file" >> download_list.txt
              done

              echo "--- Download List (URL and Filename) ---"
              cat download_list.txt
              echo "----------------------------------------"

              mkdir -p "${MODEL_DIR}"
              # Use xargs to read 2 arguments per line (-n 2): the URL ($1) and the filename ($2).
              # Then, use the -o option in aria2c to specify the output filename.
              cat download_list.txt | xargs -P 4 -n 2 sh -c '
                aria2c --header="Authorization: Bearer ${HF_TOKEN}" \
                      --console-log-level=error \
                      --file-allocation=none \
                      --max-connection-per-server=16 \
                      --split=16 \
                      --min-split-size=3M \
                      --max-concurrent-downloads=16 \
                      -c \
                      -d "${MODEL_DIR}" \
                      -o "$2" \
                      "$1"
              ' _

              end=$(date +%s)
              du -sh ${MODEL_DIR}
              echo "download took $((end-start)) seconds"
            env:
            - name: MODEL_ID
              value: "${MODEL_ID}"
            - name: HF_TOKEN
              valueFrom:
                secretKeyRef:
                  name: hf-secret
                  key: hf_api_token
            - name: MODEL_DIR
              value: "/data${BUCKET_PATH}"
            volumeMounts:
              - mountPath: "/data"
                name: model-tmpfs
          containers:
          - name: gcloud-upload
            image: gcr.io/google.com/cloudsdktool/cloud-sdk:stable
            resources:
              # Need a big enough machine that can fit the full model in RAM, with some buffer room. If you are deploying a larger model, you MUST increase this value to prevent the Pod from being evicted for using too much memory.
              requests:
                memory: "${MEMORY_REQUIREMENTS}"
              limits:
                memory: "${MEMORY_REQUIREMENTS}"
            command: ["bash", "-c"]
            args:
            - |
              start=$(date +%s)
              model_dir="${MODEL_DIR}"
              # If BUCKET_PATH is empty, copy the contents of MODEL_DIR to the root of the bucket.
              if [ -z "$BUCKET_PATH" ]; then
                model_dir="${MODEL_DIR}/*"
              fi
              gcloud storage cp -r $model_dir "${BUCKET_URI}${BUCKET_PATH}"
              end=$(date +%s)
              echo "gcloud storage cp took $((end-start)) seconds"
            env:
            - name: MODEL_DIR
              value: "/data${BUCKET_PATH}"
            volumeMounts:
            - name: model-tmpfs  # Mount the same volume as the download container
              mountPath: /data
          restartPolicy: Never
          volumes:
            - name: model-tmpfs
              emptyDir:
                medium: Memory
      parallelism: 1         # Run 1 Pods concurrently
      completions: 1         # Once 1 Pods complete successfully, the Job is done
      backoffLimit: 0        # Max retries on failure
    ---
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: "${SERVICE_ACCOUNT}"
      namespace: "${NAMESPACE}"
    ```


1. Deploy the Job in `producer-job.yaml` by running the following command. It uses `envsubst` to substitute the required environment variables.

    >[!CAUTION]
    >If you changed `MODEL_ID` to a model other than `meta-llama/Meta-Llama-3-8B`, you must do the following. Failure to do so, will prevent this Job from transfering the model successfully.
    >  1. Signed the model's license consent agreement (if required for the model).
    >  1. Set `MEMORY_REQUIREMENTS` to an appropriate size for chosen model: To be safe, `MEMORY_REQUIREMENTS` should be at least 10Gi greater than the size of the model (the sum of the model's file sizes).
    >  1. If you are using a GKE Standard cluster with Node Autoprovisioning disabled, you will need to manually provision a C3 nodepool with 1 node, that has RAM memory >=  `MEMORY_REQUIREMENTS`.

    ```bash
    export MODEL_ID=meta-llama/Meta-Llama-3-8B
    export MEMORY_REQUIREMENTS=30Gi
    export BUCKET_PATH=/model
    envsubst '$NAMESPACE $SERVICE_ACCOUNT $HF_USER $BUCKET_URI $MODEL_ID $MEMORY_REQUIREMENTS $BUCKET_PATH' < producer-job.yaml | kubectl apply -f -
    ```

    It might take a few minutes for the c3 node to be auto-provisioned, and for pods to be scheduled, and finish copying data to the GCS bucket. When the Job completes, its status is marked "Complete". After the Job completes, your Cloud Storage bucket should contain the `MODEL_ID`'s model's files (except for the `.gitattributes` and the `original/` folder) within the specified `BUCKET_PATH`. If you didn't change the `MODEL_ID`, you should see the [meta-llama/Meta-Llama-3-8B files](https://huggingface.co/meta-llama/Meta-Llama-3-8B/tree/main) in your Cloud Storage bucket, within the `/model` folder. You can use `BUCKET_PATH=""` to upload the model's files into the GCS bucket's root directory.

1. Monitor the status of the transfer. 

    To check the status of your Job, run the following command:

    ```bash
    kubectl get job producer-job --namespace ${NAMESPACE}
    ```
    
    To see logs for the download / gcloud-upload containers while they are running, run the following commands. Note that you will not be able to run these commands once the Job has the "Complete" status: 
    ```bash
    kubectl logs jobs/producer-job -c download --namespace=$NAMESPACE
    ```
    ```bash
    kubectl logs jobs/producer-job -c gcloud-upload
    ```

    Once you see that the Job has the "Complete" Status, the transfer is complete.

    Once the transfer is complete, you can confirm all of the model's files exist in your Cloud Storage Bucket by running the following command:
    ```bash
    gcloud storage ls --recursive gs://$BUCKET_NAME$BUCKET_PATH
    ```

1. Once the Job completes, you can clean up the Job by running this command:

    ```bash
    kubectl delete job producer-job --namespace ${NAMESPACE}
    kubectl delete serviceaccount hf-sa --namespace ${NAMESPACE}
    kubectl delete secret hf-secret --namespace ${NAMESPACE}
    ```

## Deploy the vLLM Model Server on GKE

Now that the model's weights exist in your Cloud Storage bucket, you can deploy the vLLM Model server on GKE, and load model weights from your Cloud Storage bucket to optimize model load time. 


1. Configure access for the model deployment, to the Cloud Storage bucket. 

    Grant the Storage Object Viewer (`roles/storage.objectViewer`) IAM role for the `model-vllm-deployment-service-account` ServiceAccount to allow the `model-vllm-deployment` to load the model weights from the Cloud Storage bucket. This is a different IAM binding than the one we used to grant the `producer-job` the access needed to populate the Cloud Storage bucket with the model weights.

    ```bash
    export SERVICE_ACCOUNT=model-vllm-deployment-service-account
    gcloud storage buckets add-iam-policy-binding ${BUCKET_URI} \
    --member "principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WORKLOAD_IDENTITY_POOL}/subject/ns/${NAMESPACE}/sa/${SERVICE_ACCOUNT}" \
    --role "roles/storage.objectViewer"
    ```

1. Deploy the following manifest, to create a model deployment, which which loads the model weights from your Cloud Storage bucket into the GPU using GCSFuse. If you did not change the `MODEL_ID`, this will deploy the [meta-llama/Meta-Llama-3-8B](https://huggingface.co/meta-llama/Meta-Llama-3-8B) model deployment. If you change


    ```bash
    kubectl apply -f - <<EOF
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      labels:
        app: model-vllm-inference-server
      name: model-vllm-deployment
      namespace: "${NAMESPACE}"
      annotations:
        hf-gcs-transfer-ai-on-gke: "true"
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: model-vllm-inference-server
      template:
        metadata:
          annotations:
            gke-gcsfuse/cpu-limit: "0"
            gke-gcsfuse/ephemeral-storage-limit: "0"
            gke-gcsfuse/memory-limit: "0"
            gke-gcsfuse/volumes: "true"
          labels:
            app: model-vllm-inference-server
        spec:
          containers:
          - args:
            - --model=/data${BUCKET_PATH}
            command:
            - python3
            - -m
            - vllm.entrypoints.openai.api_server
            image: vllm/vllm-openai:v0.7.2
            name: inference-server
            ports:
            - containerPort: 8000
              name: metrics
            readinessProbe:
              failureThreshold: 60
              httpGet:
                path: /health
                port: 8000
              periodSeconds: 10
            resources:
              limits:
                nvidia.com/gpu: "1"
              requests:
                nvidia.com/gpu: "1"
            volumeMounts:
            - mountPath: /dev/shm
              name: dshm
            - mountPath: /data
              name: model-src
          nodeSelector:
            cloud.google.com/gke-accelerator: nvidia-l4
          serviceAccountName: "${SERVICE_ACCOUNT}"
          volumes:
          - emptyDir:
              medium: Memory
            name: dshm
          - csi:
              driver: gcsfuse.csi.storage.gke.io
              volumeAttributes:
                bucketName: ${BUCKET_NAME}
                mountOptions: implicit-dirs,file-cache:enable-parallel-downloads:true,file-cache:parallel-downloads-per-file:100,file-cache:max-parallel-downloads:-1,file-cache:download-chunk-size-mb:10,file-cache:max-size-mb:-1
            name: model-src
    ---
    apiVersion: v1
    kind: Service
    metadata:
      labels:
        app: model-vllm-inference-server
      name: model-vllm-service
      namespace: "${NAMESPACE}"
    spec:
      ports:
      - port: 8000
        protocol: TCP
        targetPort: 8000
      selector:
        app: model-vllm-inference-server
      type: ClusterIP
    ---
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: "${SERVICE_ACCOUNT}"
      namespace: "${NAMESPACE}"
    EOF
    ```

1. Check the status of the model deployment by running: 
    ```bash
    kubectl get deployment model-vllm-deployment --namespace ${NAMESPACE}
    ```

    Once your model deployment is running (1/1 Replicas are Ready), follow the [vLLM documentation](https://docs.vllm.ai/en/latest/getting_started/quickstart.html#openai-completions-api-with-vllm) to build and send a request to your endpoint.

## Cleanup

1. Delete the model deployment, service, and service account by running: 

    ```bash
    kubectl delete deployment model-vllm-deployment  --namespace ${NAMESPACE}
    kubectl delete service model-vllm-service --namespace ${NAMESPACE}
    kubectl delete serviceaccount ${SERVICE_ACCOUNT} --namespace ${NAMESPACE}   
    ```

1. Delete the Cloud Storage bucket, and all of its contents by running:

    ```bash
    gcloud storage rm --recursive ${BUCKET_URI}
    ```