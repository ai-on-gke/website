---
linkTitle: "Agentic LlamaIndex with RAG"
title: "Building a Movie Recommendation RAG with Agentic LlamaIndex on GKE"
description: "This tutorial guides you through creating a movie recommendation Retrieval-Augmented Generation (RAG) system using Agentic LlamaIndex and deploying it on Google Kubernetes Engine (GKE)."
weight: 30
type: docs
owner: 
  - name: "Vlado Djerek"
    link: https://github.com/volatilemolotov
tags:
 - Serving
 - Llamaindex
 - Agentic
 - RAG
cloudShell:
    enabled: true
    folder: site/content/docs/agentic/agentic-llamaindex
    editorFile: index.md
---
[LlamaIndex](https://docs.llamaindex.ai/en/stable/) bridges the gap between LLMs and domain-specific datasets, enabling efficient indexing and querying of unstructured data for intelligent, data-driven responses. When deployed on a [GKE](https://cloud.google.com/kubernetes-engine/docs/concepts/kubernetes-engine-overview) cluster, it ensures scalability and security by leveraging containerized workloads, GPU-based nodes, and seamless cloud-native integrations, making it ideal for ML-powered applications like RAG systems.

# Overview

This tutorial will guide you through creating a Intelligent Agent & robust Retrieval-Augmented Generation (RAG) system using LlamaIndex and deploying it on Google Kubernetes Engine (GKE). The system indexes a dataset of 1000 top-rated movies and uses an LLM to generate recommendations based on user queries (e.g., "best drama movies" or "movies like Inception").

## What you will learn?

1. **Data Preparation and Ingestion:** Use LlamaIndex to structure and index your data for efficient querying.
2. **Model Integration:** Connect LlamaIndex with an LLM to build a RAG pipeline that can generate precise responses based on your indexed data.
3. **Containerization:** Package the application as a container for deployment.
4. **GKE Deployment:** Set up a GKE cluster using [Terraform](https://developer.hashicorp.com/terraform?product_intent=terraform) and deploy your RAG system, leveraging Kubernetes features.

# Before you begin

Ensure you have a GCP project with a billing account.

Ensure you have the following tools installed on your workstation:

* [gcloud CLI](https://cloud.google.com/sdk/docs/install)
* [kubectl](https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl#install_kubectl)
* [terraform](https://developer.hashicorp.com/terraform/tutorials/aws-get-started/install-cli)  (for an automated deployment)
* [git](https://git-scm.com/downloads)

If you previously installed the gcloud CLI, get the latest version by running:

```bash
gcloud components update
```

 Ensure that you are signed in using the gcloud CLI tool. Run the following commands:

```bash
gcloud auth application-default login
```

## Download the guide files

Clone the repository with our guides and cd to the llamaindex/rag directory by running these commands:
```bash
git clone https://github.com/ai-on-gke/tutorials-and-examples.git
cd tutorials-and-examples/agentic-llamaindex/rag
```

## Filesystem structure

* `app` \- folder with demo Python application that uses llamaindex to ingest data to RAG and infer it through web API.
* `templates` \- folder with Kubernetes manifests that require additional processing to specify additional values that are not known from the start.
* `terraform` \- folder with Terraform config that executes automated provisioning of required infrastructure resources.

## Demo application

The demo application consists of following components:

### Data ingestion

The data ingestion process indexes a movie dataset (`imdb_top_1000.csv`) into a Redis vector store for use by the RAG system. It is defined in `app/cmd/ingest_data.py`.


File: `app/cmd/ingest_data.py`
<details><summary>Expand to see key parts</summary>

```python
pipeline = IngestionPipeline(
    transformations=[embed_model],
    vector_store=vector_store,
)

# Process documents individually
nodes = []
for doc in docs:
    logger.info(f"Ingesting document: {doc.id_}")
    try:
        embedding = embed_model.get_text_embedding(doc.text)
        logger.info(f"Embedding generated for {doc.id_}: {len(embedding)} dimensions")
        node_list = pipeline.run(documents=[doc], show_progress=True)
        nodes.extend(node_list)
        logger.info(f"Nodes created for {doc.id_}: {len(node_list)}")
    except Exception as e:
        logger.error(f"Error ingesting document {doc.id_}: {str(e)}")

```

The pipeline is configured with:
* `transformations`: Only the embedding model (`BAAI/bge-small-en-v1.5`) to generate vector representations for each movie document.
* `vector_store`: A Redis vector store to store the indexed nodes.
* Individual document processing to ensure each movie (e.g., *The Shawshank Redemption*) is ingested as a separate node, avoiding batching issues.
</details>

File `app/rag_demo/__init__.py`:

<details><summary>Expand to see key parts</summary>

The application uses [Redis Stack](https://redis.io/about/about-stack/) as the vector store, with a defined schema for indexing movie data.

```python
custom_schema = IndexSchema.from_dict(
    {
        "index": {"name": "movies", "prefix": "movie"},
        "fields": [
            {"type": "tag", "name": "id"},
            {"type": "tag", "name": "doc_id"},
            {"type": "text", "name": "text"},
            {
                "type": "vector",
                "name": "vector",
                "attrs": {
                    "dims": 384,
                    "algorithm": "hnsw",
                    "distance_metric": "cosine",
                },
            },
        ],
    }
)
```

The schema defines:
* An index named `movies` with a `movie` prefix.
* Fields for document ID, text, and a 384-dimensional vector for embeddings generated by `BAAI/bge-small-en-v1.5`.
</details>

### RAG server

The RAG server is a FastAPI application that exposes a `/recommend` endpoint to run a multi-step, self-refining movie recommender. It is defined in `app/rag_demo/main.py`.

File: `app/rag_demo/main.py`
<details><summary>Expand to see key parts</summary>

```python
# Agentic pipeline: expansion, validation, correction, coordination
expander  = QueryExpander(llm=llm)       # Context‑aware sub‑query generation
validator = ValidationAgent(llm=llm)     # YES/NO relevance check
corrector = CorrectionAgent(llm=llm)     # JSON‑based correction step
coordinator = MultiStepCoordinator(
    expander=expander,
    query_engine=query_engine,
    validator=validator,
    corrector=corrector,
    max_steps=3
)

# FastAPI endpoints
app = FastAPI()
class QueryRequest(BaseModel):
    query: str

@app.post("/recommend")
async def recommend_movies(request: QueryRequest):
    """
    1) Expand query via LLM  
    2) Retrieve top‑K from Redis  
    3) Validate relevance (YES/NO)  
    4) If invalid, correct and retry 
    5) Return final JSON with `message` + `recommendations`
    """
    result = await coordinator.run(request.query)
    return JSONResponse(content=jsonable_encoder(result))

@app.get("/recommend")
async def recommend_movies_get(query: str):
    if not query:
        raise HTTPException(400, "Query parameter is required")
    result = await coordinator.run(query)
    return JSONResponse(content=jsonable_encoder(result))
```

</details>

Key components:

* **Query Expander**: 
  
  File: `app/rag_demo/agents/query_expansion.py`

  The system uses an LLM to generate 3-5 expanded queries or keywords for the original query. For example, for "movies like inception," the LLM doesn't just look for synonyms of "movie" or "Inception." It understands the context (e.g., Inception's themes of mind-bending sci-fi, dream sequences, and Nolan's style) and generates expansions like 'mind bending movies', 'science fiction thrillers', 'films with complex narratives', 'psychological action movies', 'time manipulation movies'. This makes the expansion context-aware and improves retrieval relevance.

  <details><summary>Expand to see action log</summary>

  ```log
    13:58:04 rag_demo.agents.coordinator INFO   [Step 1] Expanding query: movies like inception
    13:58:09 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:58:09 rag_demo.agents.query_expansion INFO   Expanded query 'movies like inception' to: ['mind bending sci-fi', 'films with dream sequences', 'neo noir thrillers', 'Christopher Nolan movies', 'thought provoking action']
    13:58:09 rag_demo.agents.coordinator INFO   [Step 1] Expanded queries: ['mind bending sci-fi', 'films with dream sequences', 'neo noir thrillers', 'Christopher Nolan movies', 'thought provoking action']
    13:58:09 rag_demo.agents.coordinator INFO   [Step 1] Querying with expanded: mind bending sci-fi
  ```

  </details>

* **Multi-Step Coordinator**:

  File: `app/rag_demo/agents/coordinator.py`

  The multi-step coordinator orchestrates the entire workflow. If validation fails, the system doesn't just give up — it reasons that the current approach was flawed and enters a correction phase, using the CorrectionAgent (`app/rag/demo/agents/correction.py`) to fix the results. This demonstrates the "expand -> retrieve -> validate -> correct" loop in action.

  <details><summary>Expand to see action log</summary>

  ```log
    13:58:04 rag_demo.agents.coordinator INFO   [Step 1] Expanding query: movies like inception
    13:58:09 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:58:09 rag_demo.agents.query_expansion INFO   Expanded query 'movies like inception' to: ['mind bending sci-fi', 'films with dream sequences', 'neo noir thrillers', 'Christopher Nolan movies', 'thought     provoking action']
    13:58:09 rag_demo.agents.coordinator INFO   [Step 1] Expanded queries: ['mind bending sci-fi', 'films with dream sequences', 'neo noir thrillers', 'Christopher Nolan movies', 'thought provoking action']
    13:58:09 rag_demo.agents.coordinator INFO   [Step 1] Querying with expanded: mind bending sci-fi
    Batches:   0%|          | 0/1 [00:00<?, ?it/s]/usr/local/lib/python3.12/site-packages/torch/nn/modules/module.py:1747: FutureWarning: `encoder_attention_mask` is deprecated and will be removed in version 4.55.0    for `BertSdpaSelfAttention.forward`.
      return forward_call(*args, **kwargs)
    Batches: 100%|██████████| 1/1 [00:00<00:00,  1.51it/s]
    13:58:10 llama_index.vector_stores.redis.base INFO   Querying index movies with query *=>[KNN 3 @vector $vector AS vector_distance] RETURN 5 id doc_id text _node_content vector_distance SORTBY vector_distance    ASC DIALECT 2 LIMIT 0 3
    13:58:10 llama_index.vector_stores.redis.base INFO   Found 3 results for query with id ['movie:movie_Avatar', 'movie:movie_Inception', 'movie:movie_Alien']
    13:58:12 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:58:12 rag_demo.agents.coordinator INFO   [Step 1] Querying with expanded: films with dream sequences
    Batches: 100%|██████████| 1/1 [00:00<00:00, 15.60it/s]
    13:58:12 llama_index.vector_stores.redis.base INFO   Querying index movies with query *=>[KNN 3 @vector $vector AS vector_distance] RETURN 5 id doc_id text _node_content vector_distance SORTBY vector_distance    ASC DIALECT 2 LIMIT 0 3
    13:58:12 llama_index.vector_stores.redis.base INFO   Found 3 results for query with id ['movie:movie_Le_charme_discret_de_la_bourgeoisie', 'movie:movie_8½', 'movie:movie_Requiem_for_a_Dream']
    13:58:23 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:58:23 rag_demo.agents.coordinator INFO   [Step 1] Querying with expanded: neo noir thrillers
    Batches: 100%|██████████| 1/1 [00:00<00:00, 25.27it/s]
    13:58:23 llama_index.vector_stores.redis.base INFO   Querying index movies with query *=>[KNN 3 @vector $vector AS vector_distance] RETURN 5 id doc_id text _node_content vector_distance SORTBY vector_distance    ASC DIALECT 2 LIMIT 0 3
    13:58:23 llama_index.vector_stores.redis.base INFO   Found 3 results for query with id ['movie:movie_Shadow_of_a_Doubt', 'movie:movie_Prisoners', 'movie:movie_Sicario']
    13:58:35 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:58:35 rag_demo.agents.coordinator INFO   [Step 1] Querying with expanded: Christopher Nolan movies
    Batches: 100%|██████████| 1/1 [00:00<00:00, 24.42it/s]
    13:58:35 llama_index.vector_stores.redis.base INFO   Querying index movies with query *=>[KNN 3 @vector $vector AS vector_distance] RETURN 5 id doc_id text _node_content vector_distance SORTBY vector_distance    ASC DIALECT 2 LIMIT 0 3
    13:58:35 llama_index.vector_stores.redis.base INFO   Found 3 results for query with id ['movie:movie_Dunkirk', 'movie:movie_The_Dark_Knight', 'movie:movie_The_Prestige']
    13:58:48 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:58:48 rag_demo.agents.coordinator INFO   [Step 1] Querying with expanded: thought provoking action
    Batches: 100%|██████████| 1/1 [00:00<00:00, 24.75it/s]
    13:58:48 llama_index.vector_stores.redis.base INFO   Querying index movies with query *=>[KNN 3 @vector $vector AS vector_distance] RETURN 5 id doc_id text _node_content vector_distance SORTBY vector_distance    ASC DIALECT 2 LIMIT 0 3
    13:58:48 llama_index.vector_stores.redis.base INFO   Found 3 results for query with id ['movie:movie_The_Conversation', 'movie:movie_Terminator_2:_Judgment_Day', 'movie:movie_Saw']
    13:58:50 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:58:50 rag_demo.agents.coordinator INFO   [Step 1] Got 15 unique recs; validating…
    13:58:54 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:58:54 rag_demo.agents.coordinator INFO   [Step 1] Validation failed; correcting…
    13:59:06 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:59:06 rag_demo.agents.correction INFO   Successfully parsed corrected recommendations: [{'title': 'The Matrix', 'imdb_rating': 8.7, 'overview': 'A computer hacker learns from mysterious rebels about the true    nature of his reality and his role in the war against machines.', 'genre': 'Action, Sci-Fi', 'released_year': 1999, 'director': 'Lana Wachowski, Lilly Wachowski', 'stars': ['Keanu Reeves', 'Laurence Fishburne',    'Carrie-Anne Moss']}, {'title': 'Interstellar', 'imdb_rating': 8.6, 'overview': 'A team of explorers travel through a wormhole in space in an attempt to find a new home for humanity.', 'genre': 'Adventure,    Sci-Fi', 'released_year': 2014, 'director': 'Christopher Nolan', 'stars': ['Matthew McConaughey', 'Anne Hathaway', 'Jessica Chastain']}, {'title': 'Paprika', 'imdb_rating': 8.0, 'overview': 'A device allows   therapists to enter the dreams of their patients, but when the technology falls into the wrong hands, reality and dreams begin to blur.', 'genre': 'Animation, Sci-Fi', 'released_year': 2006, 'director':     'Satoshi Kon', 'stars': ['Shinobu Otake', 'Yuko  Miyamura', 'Megumi Hayashibara']}]
    13:59:06 rag_demo.agents.coordinator INFO   [Step 1] Correction succeeded; stopping loop
  ```

  </details>

* **Validation & Correction Agents**: 

  The MultiStepCoordinator runs a loop where it expands the query, retrieves recommendations from all expansions, aggregates unique results, validates relevance, and corrects if needed. If the validation fails (e.g., recommendations don't match the intent), it enters correction phase using the CorrectionAgent to generate new results based on the original query and previous recs. This shows multi-step reasoning and agent coordination.

  <details><summary>Expand to see action log</summary>

  ```log
    13:58:50 rag_demo.agents.coordinator INFO   [Step 1] Got 15 unique recs; validating…
    13:58:54 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:58:54 rag_demo.agents.coordinator INFO   [Step 1] Validation failed; correcting…
    13:59:06 httpx INFO   HTTP Request: POST http://ollama-service:11434/api/chat "HTTP/1.1 200 OK"
    13:59:06 rag_demo.agents.correction INFO   Successfully parsed corrected recommendations: [{'title': 'The Matrix', 'imdb_rating': 8.7, 'overview': 'A computer hacker learns from mysterious rebels about the true    nature of his reality and his role in the war against machines.', 'genre': 'Action, Sci-Fi', 'released_year': 1999, 'director': 'Lana Wachowski, Lilly Wachowski', 'stars': ['Keanu Reeves', 'Laurence Fishburne',    'Carrie-Anne Moss']}, {'title': 'Interstellar', 'imdb_rating': 8.6, 'overview': 'A team of explorers travel through a wormhole in space in an attempt to find a new home for humanity.', 'genre': 'Adventure,    Sci-Fi', 'released_year': 2014, 'director': 'Christopher Nolan', 'stars': ['Matthew McConaughey', 'Anne Hathaway', 'Jessica Chastain']}, {'title': 'Paprika', 'imdb_rating': 8.0, 'overview': 'A device allows   therapists to enter the dreams of their patients, but when the technology falls into the wrong hands, reality and dreams begin to blur.', 'genre': 'Animation, Sci-Fi', 'released_year': 2006, 'director':     'Satoshi Kon', 'stars': ['Shinobu Otake', 'Yuko  Miyamura', 'Megumi Hayashibara']}]
    13:59:06 rag_demo.agents.coordinator INFO   [Step 1] Correction succeeded; stopping loop
  ```

  </details>

* **FastAPI Endpoint**: The `/recommend` endpoint accepts queries (e.g., `{"query": "movies like inception"}`) and returns JSON responses with up to 3 movie recommendations.

###

# Infrastructure Setup

In this section we will use `Terraform` to automate the creation of infrastructure resources. For more details how it is done please refer to the terraform config in the `terraform` folder.
By default it creates an Autopilot GKE cluster but it can be changed to standard by setting `autopilot_cluster=false`

It creates:

* IAM service accounts:
	* for a cluster
 	* for Kubernetes permissions for app deployments (using [Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation))
  * GCS bucket to store data to be ingested to the RAG.
	* [Artifact registry](https://cloud.google.com/artifact-registry/docs/overview) as storage for an app-demo image

1. Go the terraform directory:

    ```bash
    cd terraform
    ```

2. Specify the following values inside the `default_env.tfvars` file (or make a separate copy):
	- `<PROJECT_ID>` – replace with your project id (you can find it in the project settings).

    Other values can be changed, if needed, but can be left with default values.

3. Optional. You can use a GCS bucket as a storage for a Terraform state.  [Create a bucket](https://cloud.google.com/storage/docs/creating-buckets#command-line) manually and then uncomment the content of the file `terraform/backend.tf` and specify your bucket:

    ```hcl
    terraform {
      backend "gcs" {
        bucket = "<BUCKET_NAME>"
        prefix = "terraform/state/agentic-llamaindex"
      }
    }
    ```

4. Init terraform modules:

    ```bash
    terraform init
    ```

5. Optionally run the `plan` command to view an execution plan:

    ```bash
    terraform plan -var-file=default_env.tfvars
    ```

6. Execute the plan:

    ```bash
    terraform apply -var-file=default_env.tfvars
    ```

    And you should see your resources created:

    ```bash
    Apply complete! Resources: 16 added, 0 changed, 0 destroyed.

    Outputs:

    bucket_name = "llamaindex-rag-demo-tf"
    demo_app_image_repo_name = "llamaindex-rag-demo-tf"
    gke_cluster_location = "us-central1"
    gke_cluster_name = "llamaindex-rag-demo-tf"
    project_id = "akvelon-gke-aieco"

    ```

7. Connect the cluster:

    ```bash
    gcloud container clusters get-credentials $(terraform output -raw gke_cluster_name) --region $(terraform output -raw gke_cluster_location) --project $(terraform output -raw project_id)
    ```

# Deploy the application to the cluster

## 1. Deploy Redis-stack.

   For this guide it was decided to use [redis-stack](https://hub.docker.com/r/redis/redis-stack) as a vector store, but there are many other [options](https://docs.llamaindex.ai/en/stable/module_guides/storing/vector_stores/).

   IMPORTANT: For the simplicity of this guide, Redis  is deployed without persistent volumes, so the database is not persistent as well. Please consider proper persistence configuration for production.

   1. Apply Redis-stack manifest:

      ```bash
      kubectl apply -f ../redis-stack.yaml
      ```

   2. Wait for Redis-stack is successfully deployed

      ```bash
      kubectl rollout status deployment/redis-stack
      ```

## 2. Deploy Ollama server

   [Ollama](https://ollama.com/) is a tool that will run LLMs. It interacts with Llama-index through its [ollama integration](https://docs.llamaindex.ai/en/stable/api_reference/llms/ollama/) and will serve the desired model, the `gemma2-9b` in our case.

   1. Deploy resulting Ollama manifest:

        ```bash
        kubectl apply -f ../gen/ollama-deployment.yaml
        ```

        <details>
        <summary>Key notes on the Ollama deployment 'ollama-deployment.yaml' file: </summary>

        ```yaml
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: ollama
        spec:
          ...
          template:
            metadata:
              ...
              annotations:
                gke-gcsfuse/volumes: 'true' # <- use FUSE to mount our bucket
            spec:
              serviceAccount: ... # <- our service account
              nodeSelector:
                cloud.google.com/gke-accelerator: nvidia-l4 # <- specify GPU type for LLM
              containers:
                ...
                  volumeMounts: # <- mount bucket volume to be used by Ollama
                    - name: ollama-data
                      mountPath: /root/.ollama/ # <- Ollama's path where it stores models
                  resources:
                    limits:
                      nvidia.com/gpu: 1 # <- Enable GPU
              volumes:
                - name: ollama-data # <- Volume with a bucket mounted with FUSE
                  csi:
        ```
        </details>

2. Wait for Ollama to be successfully deployed:

    ```bash
    kubectl rollout status deployment/ollama
    ```

3. Pull the  `gemma2:9b` model within the Ollama server pod:

    ```bash
    kubectl exec $(kubectl get pod -l app=ollama -o name) -c ollama -- ollama pull gemma2:9b
    ```

## 3. Build the demo app image

   1. Build the  `llamaindex-rag-demo` container image using [Cloud Build](https://cloud.google.com/build/docs/overview) and push it to the repository that is created by terraform. It uses `cloudbuild.yaml` file which uses the `app/Dockerfile` for a build. This may take some time:

        ```bash
        gcloud builds submit ../app \
        --substitutions _IMAGE_REPO_NAME="$(terraform output -raw demo_app_image_repo_name)" \
        --config ../cloudbuild.yaml
        ```

      More information about the container image and demo application can be found in the `app` folder.

## 4. Ingest data to the vector database by running a Kubernetes job.
  
  1. Upload sample data into our bucket, which is created by the Terraform. This data will then be ingested to our RAG’s vector store.

        ```bash
        curl -o imdb_top_1000.zip -L https://www.kaggle.com/api/v1/datasets/download/harshitshankhdhar/imdb-dataset-of-top-1000-movies-and-tv-shows | \
        unzip -p - imdb_top_1000 | \         
        gcloud storage cp - gs://$(terraform output -raw bucket_name)/datalake/imdb_top_1000.csv
        ```

  2. Create ingestion job:

      The manifests are generated from templates in the `templates` directory and put in the `gen` directory.

        ```bash
        kubectl apply -f ../gen/ingest-data-job.yaml
        ```

        <details><summary>Key notes on `ingest-data-job.yaml`</summary>

        ```yaml
        apiVersion: batch/v1
        kind: Job
        spec:
          template:
            metadata:
              annotations:
                gke-gcsfuse/volumes: 'true' # Enable GCS Fuse
            spec:
              serviceAccount: ... # Service account for GCP access
              containers:
                - name: ingest-data
                  image: ${IMAGE_NAME} # Built application image
                  command: ["python3", "cmd/ingest_data.py"] # Run ingestion script
                  env:
                    - name: INPUT_DIR
                      value: /datalake # Path to mounted dataset
                  volumeMounts:
                    - name: datalake
                      mountPath: /datalake
              volumes:
                - name: datalake
                  csi:
                    driver: gcsfuse.csi.storage.gke.io
                    volumeAttributes:
                      bucketName: ... # GCS bucket
                      mountOptions: implicit-dirs,only-dir=datalake
        ```
        - Mounts the GCS bucket containing `imdb_top_1000.csv` via GCS Fuse.
        - Runs `ingest_data.py` to index the dataset into Redis.
        </details>

3. Wait for data to be ingested. It may take few minutes:

    ```bash
    kubectl wait --for=condition=complete --timeout=600s job/llamaindex-ingest-data
    ```

4. Verify that data has been ingested:

    ```bash
    kubectl logs -f -l name=ingest-data
    ```

    Expected output:
    ```logs
    INFO:__main__:Loaded 1000 documents
    ...
    INFO:__main__:Embedding generated for movie_The_Shawshank_Redemption: 384 dimensions
    INFO:__main__:Nodes created for movie_The_Shawshank_Redemption: 1
    ...
    INFO:__main__:Ingested 1000 nodes
    ```

    **Note**: Completed job pods may be deleted by Kubernetes after some time, so check logs promptly.

## 5.  Deploy RAG server

1. Apply created manifest:

    ```bash
    kubectl apply -f ../gen/rag-deployment.yaml
    ```

      <details><summary>Key notes on `rag-deployment.yaml`</summary>

      ```yaml
      apiVersion: apps/v1
      kind: Deployment
      spec:
        template:
          spec:
            containers:
              - name: llamaindex-rag
                image: ${IMAGE_NAME} # Built application image
                env:
                  - name: MODEL_NAME
                    value: gemma2:9b # LLM model
                  - name: OLLAMA_SERVER_URL
                    value: http://ollama-service:11434 # Ollama service URL
      ```

      - Deploys the FastAPI application with access to the indexed Redis vector store and Ollama LLM.
      </details>

2. Wait for deployment is completed:

    ```bash
    kubectl rollout status deployment/llamaindex-rag
    ```

# Test the RAG

1. Forward the port to get access from a local machine:

    ```bash
    kubectl port-forward svc/llamaindex-rag-service 8000:8000
    ```

2. Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) in your browser to access the FastAPI Swagger UI, which provides an interactive interface for the `/recommend` endpoint.

3. Test movie recommendation queries:
   - **Query**: `{"query": "best drama movies"}`
     - Expected response:
       ```json
       {
         "message": "Here are the top movie recommendations:",
         "recommendations": [
           {
             "title": "The Shawshank Redemption",
             "imdb_rating": 9.3,
             "overview": "Framed for the murder of his wife and her lover, Andy Dufresne begins a new life sentence at the notorious Shawshank prison. Over time, he befriends fellow inmate Red and uses his intelligence to navigate the brutal realities of prison life while secretly plotting a daring escape.",
             "genre": "Drama",
             "released_year": "1994",
             "director": "Frank Darabont",
             "stars": "Tim Robbins, Morgan Freeman"
           },
           {
             "title": "Schindler's List",
             "imdb_rating": 8.9,
             "overview": "During the Holocaust in World War II, Oskar Schindler, a German businessman, saves the lives of over a thousand Jewish refugees by employing them in his factory. The film is a powerful and moving testament to the human capacity for both good and evil.",
             "genre": "Drama, History",
             "released_year": "1993",
             "director": "Steven Spielberg",
             "stars": "Liam Neeson, Ralph Fiennes"
           },
           {
             "title": "The Godfather",
             "imdb_rating": 9.2,
             "overview": "Don Vito Corleone, the aging patriarch of a powerful Mafia family, faces threats from rival gangsters and tries to protect his empire. When his youngest son Michael enters the family business, he is thrust into a world of violence and betrayal.",
             "genre": "Crime, Drama",
             "released_year": "1972",
             "director": "Francis Ford Coppola",
             "stars": "Marlon Brando, Al Pacino"
           }
         ]
       }
       ```
   - **Query**: `{"query": "movies like inception"}`
     - Expected response:
       ```json
       {
         "message": "Here are the top movie recommendations:",
         "recommendations": [
           {
             "title": "Interstellar",
             "imdb_rating": 8.6,
             "overview": "A team of explorers travel through a wormhole in space in an attempt to find a new home for humanity.",
             "genre": "Science Fiction, Adventure",
             "released_year": "2014",
             "director": "Christopher Nolan",
             "stars": "Matthew McConaughey, Anne Hathaway, Jessica Chastain"
           },
           {
             "title": "Predestination",
             "imdb_rating": 7.9,
             "overview": "A temporal agent travels through time to prevent a devastating attack, but his mission leads him down a mind-bending path of paradox and self-discovery.",
             "genre": "Science Fiction, Thriller",
             "released_year": "2014",
             "director": "Michael & Peter Spierig",
             "stars": "Ethan Hawke, Sarah Snook"
           },
           {
             "title": "The Matrix",
             "imdb_rating": 8.7,
             "overview": "A computer hacker learns that reality is a simulation controlled by machines and joins a rebellion to free humanity.",
             "genre": "Science Fiction, Action",
             "released_year": "1999",
             "director": "The Wachowskis",
             "stars": "Keanu Reeves, Laurence Fishburne, Carrie-Anne Moss"
           }
         ]
       }
       ```

# Cleanup

```bash
terraform destroy -var-file=default_env.tfvars
```

# Troubleshooting

There may be a temporary error in the pods, where we mount buckets by using FUSE. Normally, they should be resolved without any additional actions.

```logs
MountVolume.SetUp failed for volume "datalake" : kubernetes.io/csi: mounter.SetUpAt failed to get CSI client: driver name gcsfuse.csi.storage.gke.io not found in the list of registered CSI drivers
```
