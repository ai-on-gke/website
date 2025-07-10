---
linkTitle: "Identity Aware Proxy"
title: "Securing Application with Identity Aware Proxy"
description: "Overviews how to secure application endpoints with Identity Aware Proxy (IAP)"
weight: 30
type: docs
owner:
  - name: "Vlado Djerek"
    link: https://github.com/volatilemolotov
tags:
    - Tutorials
    - Inference Servers
    - Security
draft: false
---
This guide will show how to enable [Identity Aware Proxy (IAP)](https://cloud.google.com/iap/docs/concepts-overview) to securely expose your application to the internet.

## Overview

This guide will use our [Terraform module](https://github.com/ai-on-gke/common-infra/tree/main/common/modules/iap) in order to enable IAP for the application from one of our tutorials.

## Enable IAP and create OAuth client

1. Before securing the application with IAP, ensure that the OAuth consent screen is configured. Go to the [IAP page](https://console.cloud.google.com/security/iap) and click "Configure consent screen" if prompted.  
2. Create an OAuth 2.0 client ID by visiting the [Credentials page](https://console.cloud.google.com/apis/credentials) and selecting "Create OAuth client ID". Use the `Web application` type and proceed with the creation. Save the Client ID and secret for later use.   
3. Go back to the [Credentials page](https://console.cloud.google.com/apis/credentials), click on the OAuth 2.0 client ID you created, and add the redirect URI as follows: `https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect` (replace `<CLIENT_ID>` with the actual client ID). This is crucial because the redirect URI is used by the OAuth 2.0 authorization server to return the authorization code to your application. If it is not configured correctly, the authorization process will fail, and users will not be able to log in to the application.

### Apply the IAP Terraform module 

1. Create a directory for IAP terraform config. If you came here from another guide, then you should already have this directory created, otherwise, create it now:

   ```shell
   mkdir iap
   cd iap
   ```

2. Create terraform files with the following contents:


   File `iap.tf`:

   ```
   module "iap_auth" {
     source = "github.com/ai-on-gke/common-infra//common/modules/iap-with-cluster?ref=main"
   
     project_id =  var.project_id
     cluster_name = var.cluster_name
     cluster_location = var.cluster_location
     namespace                = var.k8s_namespace
     k8s_backend_service_name = var.k8s_backend_service_name
     k8s_backend_service_port = var.k8s_backend_service_port
     support_email            = var.support_email
     app_name                 = var.app_name
     client_id                = var.client_id
     client_secret            = var.client_secret
     domain                   = var.domain
     members_allowlist        = var.members_allowlist
     create_brand             = var.create_brand
     k8s_ingress_name         = var.k8s_ingress_name
     k8s_managed_cert_name    = var.k8s_managed_cert_name
     k8s_iap_secret_name      = var.k8s_iap_secret_name
     k8s_backend_config_name  = var.k8s_backend_config_name
   }
   ```

   File `variables.tf`:

   ```
   variable "project_id" {
     type        = string
     description = "GCP project ID"
   }
   
   variable "cluster_name" {
     type = string
     description = "Name of a target GKE cluster where the target application is deployed"
   }
   
   variable "cluster_location" {
     type = string
     description = "Location of a target GKE cluster where the target application is deployed"
   }
   
   
   variable "app_name" {
     type        = string
     description = "App Name"
   }
   
   # IAP settings
   variable "create_brand" {
     type        = bool
     description = "Create Brand OAuth Screen"
     default = false
   }
   
   variable "k8s_namespace" {
     type        = string
     description = "Kubernetes namespace where resources are deployed"
   }
   variable "k8s_ingress_name" {
     type        = string
     description = "Name for k8s Ingress"
     default = ""
   }
   
   variable "k8s_managed_cert_name" {
     type        = string
     description = "Name for k8s managed certificate"
     default = ""
   }
   
   variable "k8s_iap_secret_name" {
     type        = string
     description = "Name for k8s iap secret"
     default = ""
   }
   
   variable "k8s_backend_config_name" {
     type        = string
     description = "Name of the Kubernetes Backend Config"
     default = ""
   }
   
   variable "k8s_backend_service_name" {
     type        = string
     description = "Name of the Backend Service"
   }
   
   variable "k8s_backend_service_port" {
     type        = number
     description = "Name of the Backend Service Port"
   }
   
   variable "domain" {
     type        = string
     description = "Provide domain for ingress resource and ssl certificate. "
     default     = "{IP_ADDRESS}.sslip.io"
   }
   
   variable "support_email" {
     type        = string
     description = "Email for users to contact with questions about their consent"
     default     = ""
   }
   
   variable "client_id" {
     type        = string
     description = "Client ID used for enabling IAP"
     default     = ""
   }
   
   variable "client_secret" {
     type        = string
     description = "Client secret used for enabling IAP"
     default     = ""
   }
   
   variable "members_allowlist" {
     type    = list(string)
     default = []
   }
   ```

   File `outputs.tf`:

   ```
   output "app_public_ip" {
     value = module.iap_auth.ip_address
   }
   
   output "app_url" {
     value = "https://${module.iap_auth.domain}"
   }
   
   output "k8s_managed_cert_name" {
     value = module.iap_auth.k8s_managed_cert_name
   }
   ```

3. Create a tfvars file with the following values. 

   > [!NOTE]
   > If you came from another guide, then you may already have this file.

   The file must have the following variables:

   ```
   project_id               = "<PROJECT_ID>"
   cluster_name             = "<CLUSTER_NAME>"
   cluster_location         = "<CLUSTER_LOCATION>"
   app_name                 = "<APP_NAME>"
   k8s_namespace            = "<KUBERNETES_NAMESPACE>"
   k8s_backend_service_name = "<SERVICE_NAME>"
   k8s_backend_service_port = "<SERVICE_PORT>"
   support_email            = "<SUPPORT_EMAIL>"
   client_id                = "<CLIENT_ID>"
   client_secret            = "<CLIENT_SECRET>"
   members_allowlist        = [<MEMBERS_ALLOWLIST>]
   ```

   Where:
   
      * `project_id` \-  The project ID.  
      * `cluster_name` \- Name of a target cluster where app resources are deployed.  
      * `cluster_location` \- Location of a target cluster.  
      * `app_name` \- Name of the application. This will be used as a prefix for created resources names.  
      * `k8s_namespace` \- The namespace where the app is deployed.  
      * `k8s_backend_service_name` \- Name of a service to expose.  
      * `k8s_backend_service_port` \-  Port of a service to expose.  
      * `support_email` \- A support email to be shown in the authorization screen.  
      * `client_id` \- ID of a created OAuth client.  
      * `client_secret` \- Secret if a created OAuth client
      * `members_allowlist` - List of members that have access through IAP. As a simplest example, it can be just a user from your personal GCP account - `user:<YOUR_ACCOUNT_EMAIL>`. For more info, you can read [this](https://cloud.google.com/iam/docs/principal-identifiers).
   
   For information about other variables please refer to the `variables.tf` file. 

4. Init the Terraform config:

   ```shell
   terraform init
   ```

5. Apply the IAP Terraform config:

   ```shell
   terraform apply -var-file values.tfvars
   ```

6. Wait for a managed certificate to be provisioned. Since the provisioning time for the certificate is longer than for other resources, it should be safe to assume that other resources are also ready:

   ```shell
   kubectl wait --for='jsonpath={.status.domainStatus[0].status}=Active' managedcertificate/$(terraform output -raw k8s_managed_cert_name) --timeout=20m
   ```

7. Get the url of the app:

   ```shell
   terraform output app_url
   ```

## Cleanup

   ```shell
   terraform destroy -var-file values.tfvars
   ```

## Troubleshooting

### Can not create valid Healthcheck

By default the Health check that is created for a GCP LoadBalancer backend is derived from the Pod readiness probe, so make sure that its values are compatible with GCP healthcheck.

For example the service object has to use a port only from range 80 to 443.

Read more [here](https://cloud.google.com/kubernetes-engine/docs/concepts/ingress#interpreted_hc).
