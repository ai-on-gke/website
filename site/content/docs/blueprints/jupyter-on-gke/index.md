---
linkTitle: "Jupyter"
title: "Jupyter on GKE"
description: "This guide details how to deploy JupyterHub on Google Kubernetes Engine (GKE) using a provided Terraform template, including options for persistent storage and Identity-Aware Proxy (IAP) for secure access. It covers the necessary prerequisites, configuration steps, and installation process, emphasizing the use of Terraform for automation and IAP for authentication. The guide also provides instructions for accessing JupyterHub, setting up user access, and running an example notebook."
weight: 30
type: docs
owner:
  - name: "Francisco Cabrera"
    link: "https://github.com/fcabrera23"
tags:
 - Blueprints
cloudShell: 
    enabled: true
    folder: site/content/docs/blueprints/jupyter-on-gke
    editorFile: index.md
---
This repository contains a Terraform template for running [JupyterHub](https://jupyter.org/hub) on Google Kubernetes Engine.

This module deploys the following resources, once per user:
* JupyterHub deployment
* User namespace
* Kubernetes service accounts

## Prerequisites

1. GCP Project with following APIs enabled
    - *container.googleapis.com*
    - *gkehub.googleapis.com* (required when using private clusters with Anthos Connect Gateway)
    - *iap.googleapis.com* (required when using authentication with Identity Aware Proxy)

2. A functional GKE cluster.
    - To create a new standard or autopilot cluster, follow the instructions in [`infrastructure/README.md`](https://github.com/ai-on-gke/common-infra/tree/main/common/infrastructure)
    - Alternatively, you can set the `create_cluster` variable to true in `workloads.tfvars` to provision a new GKE cluster. This will default to creating a GKE Autopilot cluster; if you want to provision a standard cluster you must also set `autopilot_cluster` to false.

3. This module is configured to use Identity Aware Proxy (IAP) as default authentication method for JupyterHub. It expects the brand & the OAuth consent configured in your org. You can check the details here: [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)

	This code can also perform auto brand creation. Please check the details [below](#auto-brand-creation-and-iap-enablement)

4. Preinstall the following on your computer:
    * Terraform
    * Gcloud CLI

> [!NOTE]
> JupyterHub server can use either local storage or GCS to store notebooks and other artifcts. 
To use GCS, create a bucket with your username. For example, when authenticating with IAP as username@domain.com, ensure your bucket name is `gcsfuse-<username>`

## Installation

### Configure Inputs

1. If needed, clone the repo
	```bash
	 git clone https://github.com/ai-on-gke/quick-start-guides
	 cd quick-start-guides/jupyter
	 ```

2. Edit `workloads.tfvars` with your GCP settings. The `namespace` that you specify will become a K8s namespace for your JupyterHub services. For more information about what the variables do, visit [here](https://github.com/ai-on-gke/quick-start-guides/blob/main/jupyter/variable_definitions.md).
   
    | Variable                  | Description                                                                                                     | Required |
    | :------------------------ | :-------------------------------------------------------------------------------------------------------------- | :------: |
    | `project_id`              | GCP Project Id                                                                                                  | Yes      |
    | `cluster_name`            | GKE Cluster Name                                                                                                | Yes      |
    | `cluster_location`        | GCP Region                                                                                                      | Yes      |
    | `cluster_membership_id`   | Fleet membership name for GKE cluster. <br /> Required when using private clusters with Anthos Connect Gateway |          |
    | `namespace`               | The namespace that JupyterHub and rest of the other resources will be installed in.                             | Yes      |
    | `gcs_bucket`              | GCS bucket to be used for Jupyter storage                                                                       |          |
    | `create_service_account`  | Create service accounts used for Workload Identity mapping                                                      | Yes      |
    | `gcp_and_k8s_service_account` | GCP service account used for Workload Identity mapping and k8s sa attached with workload                      | Yes      |


 For variables under `JupyterHub with IAP`, please see the section below.


### Secure endpoint with IAP

> [!NOTE]
> To secure the Jupyter endpoint, this module enables IAP by default. It is _strongly recommended_ to keep this configuration. If you wish to disable it, do the following: set the `add_auth` flag to false in the `workloads.tf` file.

3. If you already have a brand setup for your project, use the existing values to fill in the variable values in workloads.tf

4. If you have not enabled the IAP API before or created a Brand for your project, please follow these steps:

    - Navigate to the `brand` [page](https://console.cloud.google.com/apis/credentials/consent) to create your own brand:

    See [here](#auto-brand-creation-and-iap-enablement) for more information about how to create a brand automatically. Please note, auto brand creation enables the application only for [internal (within the org) users](https://cloud.google.com/iap/docs/programmatic-oauth-clients#branding). This can be switched to external users from the [consent](https://console.cloud.google.com/apis/credentials/consent) screen.

	See the example `.tfvars` files under `/applications/jupyter` for different brand/IAP configurations.

	| Variable                 | Description                | Default Value | Required |
	| ------------------------ |--------------------------- |:-------------:|:--------:|
	| add_auth                 | Enable IAP on JupyterHub   | true          | Yes      |
	| brand                    | Name of the brand used for creating IAP OAuth clients. Only one is allowed per project. View existing brands: `gcloud iap oauth-brands list`. Leave it empty to create a new brand.  Uses support_email |           |       |
	| support_email            | Support email assocated with the brand. Used as a point of contact for consent for the ["OAuth Consent" in Cloud Console](https://console.cloud.google.com/apis/credentials/consent). Optional field if `brand` is empty.   |           |       |
	| default_backend_service  | default_backend_service   |           |       |
	| service_name             | Name of the Backend Service that gets created when enabling IAP.   |           |       |
	| url_domain_addr          | Provided by the user if they want to bring their own URL/Domain. Used by the IAP resources if filled in. Filling this in will disable automatic global IP reservation. Must also fill in url_domain_name.   |           |       |
	| url_domain_name          | This variable will only be used if url_domain_addr is provided. It is the name associated with the domain provided by the user. Since we are using Ingress, it will require the `kubernetes.io/ingress.global-static-ip-name` annotation along with the name associated.   |           |       |
	| client_id                | Client ID of an [OAuth 2.0 Client ID](https://console.cloud.google.com/apis/credentials) created by the user for enabling IAP. You must also input the client_secret. If this variable is unset, the template will create an OAuth client for you - in this case, you must ensure the associated [brand](https://console.cloud.google.com/apis/credentials/consent) is `Internal` i.e. only principals within the organization can access the application.   |           |       |
	| client_secret            | Client Secret associated with the client_id. This variable will only be used when the client id is filled out.     |           |       |
	| members_allowlist        | Comma seperated values for users to be allowed access through IAP. Example values: `user:username@domain.com`  |      |       |


### Install

> [!NOTE]
> Terraform keeps state metadata in a local file called `terraform.tfstate`. Deleting the file may cause some resources to not be cleaned up correctly even if you delete the cluster. We suggest using `terraform destroy` before reapplying/reinstalling.

5. Ensure your gcloud application default credentials are in place. 
	```bash
	gcloud auth application-default login
	```

6. Initialize the Terraform template
  	```bash
	 terraform init
	```

7. Run Terraform creation tempalte
    ```bash
    terraform apply --var-file=./workloads.tfvars
    ```

    It can take upto 5 minutes on standard clusters & upto 10 minutes on AutoPilot clusters. Due to some IAP limitations, this is expected to fail with an error `Error retrieving IAM policy for iap webbackendservice` which will be resolved by the next step.

8. If using authentication with IAP (i.e. `add_auth = true`), rerun terraform apply again. This is needed to configure Jupyter with IAP correctly.

    * Verify the backend service for IAP has been created (takes 5-10 mins) with `gcloud compute backend-services list`
        - Should have `jupyter-proxy-public` in the name eg.: `k8s1-63da503a-jupyter-proxy-public-80-74043627`.
    * Run `terraform apply --var-file=./workloads.tfvars`

## Using JupyterHub

### If Auth with IAP is disabled

1. Extract the randomly generated password for JupyterHub login.

	```bash
	terraform output jupyterhub_password
	```

1. Setup port forwarding for the frontend and and open `localhost:8081` in a browser. Use the username **admin** and the password retrieved in the previous step. If you're not using the default **ai-on-gke** namespace, replace your namespace in the command.
    ```bash
	kubectl port-forward service/proxy-public -n ai-on-gke 8081:80 &
	```

### If Auth with IAP is enabled

1. Note down the value for the **domain** from the terraform output section: 
    ```bash
    terraform output domain 
    ```
    
    You can open this in a browser & login with your credentials. Alternatively, domain value for Jupyter Ingress can be found on [Certificate Manager](https://console.cloud.google.com/security/ccm/list/lbCertificates) page.

2. Ensure the managed cert for the domain has finished provisioning: 
    ```bash
    kubectl get managedcertificate -n <namespace>
    ```
    This can take 10 - 20 minutes. You may see an SSL error if you try to hit the domain when the cert isn't **Active**.

3. Open the external IP in a browser and login. If you get an access error, see the **Setup Access** section below.
Please note there may be some propagation delay after adding IAP principals (5-10 mins).

4. Select profile and open a Jupyter Notebook

>[!NOTE]
>Domain specific managed certificate may take some time to finish provisioning. This can take between 10-15 minutes. The browser may not display the login page correctly until the certificate provisioning is complete.

### Setup Access

In order for users to login to JupyterHub via IAP, their access needs to be configured. To allow access for users/groups: 

1. Navigate to the [GCP IAP Cloud Console](https://console.cloud.google.com/security/iap) and select your backend-service for `<namespace>/proxy-public`.

2. Click on **Add Principal**, insert the username / group name and select under **Cloud IAP** with role **IAP-secured Web App User**. Once presmission is granted, these users / groups can login to JupyterHub with IAP. Please note there may be some propagation delay after adding IAP principals (5-10 mins).

## Persistent Storage

JupyterHub is configured to provide 2 choices for storage:

1. Default JupyterHub Storage - `pd.csi.storage.gke.io` with reclaim policy **Delete**

2. GCSFuse - `gcsfuse.csi.storage.gke.io` uses GCS Buckets and require users to pre-create buckets with name format `gcsfuse-{username}`

For more information about Persistent storage and the available options, visit [here](https://github.com/ai-on-gke/quick-start-guides/blob/main/jupyter/storage.md)

## Running example notebook

1. Open the JupyterHub instance by gogin to `localhost:8081` in a browser. 

1. Go to *File* -> *New* -> *Notebook*

4. Connect the notebook to the **Python 3** kernel.

1. Start writing your Python code.

## Auto Brand creation and IAP enablement

> [!CAUTION]
> If you enable automatic brand creation, only `Internal` brand will be created, allowing only the users under the same org as the project to access the application.
Make sure [Policy for Restrict Load Balancer Creation Based on Load Balancer Types](https://cloud.google.com/load-balancing/docs/org-policy-constraints) allows EXTERNAL_HTTP_HTTPS.

Ensure that the following variables within `workloads.tfvars` are set:

* *enable_iap_service* - Enables the IAP service API. Leave as false if IAP is enabled before.
* *brand* - creates a brand for the project. Only one is currently allowed per project. Leave it empty to create a new brand
* *support_email* - used by brand, required field.
*  *client_id* and *client_secret* - **IMPORTANT**: If your brand is `external`, you must provide your own client_id and client_secret. If your brand is `internal`, you can choose to leave the variable as is and allow terraform to create one for you.
* If you do bring your own OAuth client, you must add to the `Authorized redirect URIs` Field:  `https://iap.googleapis.com/v1/oauth/clientIds/<client ID>:handleRedirect`

> [!NOTE]
> You can use a custom domain & existing ingress ip address in the `workloads.tfvars` file.

## Cleanup

Remove the cluster and deployment by running the following command:
```bash
terraform destroy --var-file="workloads.tfvars"
```

>[!NOTE]
> If you encounter a network deletion issue when applying the `terraform destroy` command,  this is becasue it fails to delete the network due to a known issue in the GCP provider. For now, the workaround is to manually delete it.

## Additional Information

For more information about JupyterHub profiles and the preset profiles visit [here](https://github.com/ai-on-gke/quick-start-guides/blob/main/jupyter/profiles.md)
