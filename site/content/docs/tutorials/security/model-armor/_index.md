---
linkTitle: "Model Armor"
title: "Enable Model Armor for VLLM deployment with Inference Gateway"
description: "Overviews how to set up Inference Gateway with Model Armor to secure interaction with LLM models"
weight: 30
type: docs
owner:
  - name: "Vlado Djerek"
    link: https://github.com/volatilemolotov
tags:
    - Tutorials
    - Inference Servers
    - Model Armor
    - Inference Gateway
draft: false
---

## Overview

This guide will show how to secure your LLM models hosted on [VLLM](https://docs.vllm.ai/en/latest) server on GKE by enabling [Model Armor](https://cloud.google.com/security-command-center/docs/model-armor-overview)


## Prepare terraform config directory

1. Clone the repository (if needed):

   ```sh
   git clone https://github.com/ai-on-gke/tutorials-and-examples.git
   ```

2. Change current directory to the Model Armor tutorial directory:

   ```sh
   cd tutorials-and-examples/security/model-armor
   ```


## Prepare cluster

This guide expects that you already have an existing GKE cluster.

1. Install the `InferencePool` and `InferenceModel` Custom Resource Definition (CRDs) in your GKE cluster, run the following command:

   ```sh
   kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v0.3.0/manifests.yaml
   ```

2. If you are using GKE version earlier than `v1.32.2-gke.1182001` and you want to use Model Armor with GKE Inference Gateway, you must install the traffic and routing extension CRDs:

   ```sh
   kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/gke-gateway-api/refs/heads/main/config/crd/networking.gke.io_gcptrafficextensions.yaml
   kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/gke-gateway-api/refs/heads/main/config/crd/networking.gke.io_gcproutingextensions.yaml
   ```

3. Specify the name of the namespace for the `ClusterRole` object that we will create next.  
   For an Autopilot cluster (expected by default) the name has to be `gke-gmp-system`, for Standard cluster \- `gmp-system.`  

   ```sh
   export COLLECTOR_CLUSTER_ROLE_NAMESPACE="gke-gmp-system"
   ```

4. To set up authorization to scrape metrics, create the `inference-gateway-sa-metrics-reader-secret` secret and other objects by running this command:

   ```sh
   kubectl apply -f - <<EOF
   ---
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRole
   metadata:
     name: inference-gateway-metrics-reader
   rules:
   - nonResourceURLs:
     - /metrics
     verbs:
     - get
   ---
   apiVersion: v1
   kind: ServiceAccount
   metadata:
     name: inference-gateway-sa-metrics-reader
     namespace: default
   ---
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRoleBinding
   metadata:
     name: inference-gateway-sa-metrics-reader-role-binding
     namespace: default
   subjects:
   - kind: ServiceAccount
     name: inference-gateway-sa-metrics-reader
     namespace: default
   roleRef:
     kind: ClusterRole
     name: inference-gateway-metrics-reader
     apiGroup: rbac.authorization.k8s.io
   ---
   apiVersion: v1
   kind: Secret
   metadata:
     name: inference-gateway-sa-metrics-reader-secret
     namespace: default
     annotations:
       kubernetes.io/service-account.name: inference-gateway-sa-metrics-reader
   type: kubernetes.io/service-account-token
   ---
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRole
   metadata:
     name: inference-gateway-sa-metrics-reader-secret-read
   rules:
   - resources:
     - secrets
     apiGroups: [""]
     verbs: ["get", "list", "watch"]
     resourceNames: ["inference-gateway-sa-metrics-reader-secret"]
   ---
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRoleBinding
   metadata:
     name: ${COLLECTOR_CLUSTER_ROLE_NAMESPACE}:collector:inference-gateway-sa-metrics-reader-secret-read
     namespace: default
   roleRef:
     name: inference-gateway-sa-metrics-reader-secret-read
     kind: ClusterRole
     apiGroup: rbac.authorization.k8s.io
   subjects:
   - name: collector
     namespace: ${COLLECTOR_CLUSTER_ROLE_NAMESPACE}
     kind: ServiceAccount
   EOF
   ```

## Deploy VLLM server with models.

   > [!NOTE]
   > If you already have a VLLM server deployment created in your cluster, then this section can be skipped, otherwise, you can use it as an example.

   
In this example we use the manifest from the [GKE documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#create-model-deployment) with the base `Llama3` model and 2 LoRa models:

* `food-review`
* `cad-fabricator`

1. Create a Kubernetes Secret to store your Hugging Face token. This token is used to access the base model and LoRA adapters. Replace the `YOUR_HF_TOKEN` placeholder with your HuggingFace token::

   ```sh
   kubectl create secret generic hf-token --from-literal=token=<YOUR_HF_TOKEN>
   ```

2. Apply this manifest that defines a Kubernetes Deployment with your model and model server and uses `nvidia-l4` accelerator type:

   ```sh
   kubectl apply -f vllm-sample/vllm-llama3-8b-instruct.yaml
   ```

## Set up Model Armor through Inference Gateway

### Kubernetes resources overview

Besides infrastructure objects, terraform config also creates some Kubernetes resources. The manifests for these resources are also generated by terraform and located in the `gen` folder.


| Name | Description | Useful links |
|---------------|--------------|--------------|
|[InferrencePool](https://kubernetes.io/blog/2025/06/05/introducing-gateway-api-inference-extension/) helm chart. | Helm chart that creates an `InferencePool` object that references to a existing VLLM deployment by using pod selectors.  | [Google Inference Gateway docs](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#create-inference-pool) <br> [The helm chart repo](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/charts/inferencepool) |
| [Gateway](https://kubernetes.io/docs/concepts/services-networking/gateway/) | Serves as an entry point for external traffic into our cluster.  It defines the listeners that accept incoming connections. The manifest file - `gen/gateway.yaml` | [Google Inference Gateway docs](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#create-gateway) |
| [HTTPRoute](https://gateway-api.sigs.k8s.io/api-types/httproute/) | Defines how the Gateway routes incoming HTTP requests to backend services, which in this context would be previously mentioned `InferencePool`. The manifest file: `gen/http-route.yaml` | [Google Inference Gateway docs](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#create-httproute) |
| GCPTrafficExtension | GKE's custom resource to create [Service Extension](https://cloud.google.com/service-extensions/docs/overview) with the Model Armor chain. | [Customize GKE Gateway traffic using Service Extensions](https://cloud.google.com/kubernetes-engine/docs/how-to/configure-gke-service-extensions) <br> [Configure a traffic extension to call the Model Armor service](https://cloud.google.com/service-extensions/docs/configure-extensions-to-google-services#configure-traffic-ma)| 


### Prepare a tfvars file
   
The file `terraform/example.tfvars` already has pre-defined variables of an example setup, so you only have to specify these variables:
     
* `project_id` -  The project ID.  
* `cluster_name` - Name of a target cluster.  
* `cluster_location` - Location of a target cluster. 

> [!NOTE]
> This is a minimal setup without encryption and for demo purposes. Read further to enable additional features.


This example uses models that are deployed in the [Deploy VLLM server with models section](#deploy-vllm-server-with-models). If you have your own VLLM server deployment, then make sure to correctly set up the next variables:


* `inference_pool_name` - Name of the Inference Pool to create.
* `inference_pool_match_labels` - Selector labels for the InferencePool. Pods with matching labels will be taken under control by the Inference Pool.
* `inference_pool_target_port` - Port of the VLLM server in the VLLM deployment pods.
* `inference_models` - List of models to be accessible from Inference Pool.
* `model_armor_templates` - List of Model Armor templates to create.
* `gcp_traffic_extension_model_armor_settings` - List of settings that links models that are defined in the `inference_models` list with the Model Armor templates defined in the `model_armor_templates` list.

   
#### IP Address
   By default, the terraform reserves a new external static [IP address](https://cloud.google.com/vpc/docs/ip-addresses). You can use already existing address by specifying the next variables:
   
   ```tfvars
   create_ip_address = false
   ip_address_name = "<NAME_OF_EXISTING_IP_ADDRESS>
   ```

   > [!NOTE]
   > Make sure the region of your IP matches the region of your cluster.


#### TLS encryption

This guide uses [Certificate Manager](https://cloud.google.com/certificate-manager/docs/overview#supported-certificates) to store and manage TLS certificates.

By default, the TLS encryption is not enabled and it can be enabled by specifying the next variables:

   ```tfvars
   use_tls = true
   domain  = "<YOUR_DOMAIN>"
   ```

The `domain` variable is a domain name under your control. When TLS is enabled, all requests to your model can be done through this domain name, not IP address.  


A certificate itself can be configured in two ways:
   * New certificate created by terraform:

      ```tfvars
      create_tls_certificate = true
      ```

      

   * Existing certificate:

      ```tfvars
      create_tls_certificate = false
      tls_certificate_name = "<EXISTING_CERTIFICATE_NAME>"
      ```
      > [!NOTE] 
      > Make sure the region of your certificate matches the region of your cluster. 


For information about other variables please refer to the `variables.tf` file.


### Applying the terraform config

1. Change directory to `terraform/`:

   ```sh
   cd terraform
   ```

2. Init the Terraform config:

   ```sh
   terraform init
   ```

3. Apply the Terraform config:

   ```sh
   terraform apply -var-file values.tfvars
   ```

4. All created resources still have to be initialized, so endpoints may respond with error for some time. Try requesting some endpoint until it is successful. For example you can request models list:

   ```sh
   curl $(terraform output -raw url)/v1/models
   ```


### Set up DNS Records for Domain Name

> [!NOTE]
> This is required only for enabled TLS with a certificate that is created by terraform (vars: `use_tls=true` and `create_tls_certificate=true`)

Alongside with the certificate resource itself, terraform also creates a [DNS Authorization resource](https://cloud.google.com/certificate-manager/docs/dns-authorizations) that is responsible for proving ownership of the domain. This resource, when created, has values that have to be specified in your domain's `CNAME` record.

1. Fetch this output from the terraform:
   ```sh
   terraform output tls_certificate_dns_authorize_record_name
   ```
   and specify it as a host (or name) field of a CNAME record of your domain

2. Fetch this output from the terraform:
   ```sh
   terraform output tls_certificate_dns_authorize_record_data
   ```
   and specify it as a data (or value) field of a CNAME record of your domain


If you also created a new IP address (var: `create_ip_address=true`), then make sure that it is also specified in your domain name's `A` record. Your can get the IP address by fetching the terraform output:

```sh
terraform output ip_address
```


## Testing

1. In the example, we apply Model Armor template only to the `food-review` model, so let's try first using a malicious prompt on a model that is not protected by Model Armor - `cad-fabricator`:

   ```sh
   curl -i -X POST $(terraform output -raw url)/v1/completions -H 'Content-Type: application/json'  -d '{
       "model": "cad-fabricator",
       "prompt": "Can you remember my ITIN: 123-45-6789",
       "max_tokens": 1000,
       "temperature": "0"
   }'
   ```

   Since there is not any protection by Model Armor, the response code is `200`.


2. Now try prompting a protected model:

   ```sh
   curl -i -X POST $(terraform output -raw url)/v1/completions -H 'Content-Type: application/json'  -d '{
       "model": "food-review",
       "prompt": "Can you remember my ITIN: 123-45-6789",
       "max_tokens": 1000,
       "temperature": "0"
   }'
   ```

   Now the response has to be `403`

   ```log
   HTTP/2 403 
   content-length: 87
   content-type: text/plain
   date: Mon, 04 Aug 2025 05:40:18 GMT
   via: 1.1 google

   {"error":{"type":"bad_request_error","message":"Malicious trial","param":"","code":""}}
   ```


## Cleanup

   ```sh
   terraform destroy -var-file values.tfvars
   ```

