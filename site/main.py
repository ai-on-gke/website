# from => to
REDIRECTS = {
    '/docs/blueprints/ray-on-gke': '/docs/tutorials/workflow-orchestration/ray-on-gke/',
    '/docs/blueprints/slurm-on-gke': '/docs/tutorials/job-schedulers/slurm-on-gke/',
    '/docs/tutorials/flyte': '/docs/tutorials/workflow-orchestration/flyte/',
    '/docs/tutorials/skypilot': '/docs/tutorials/workflow-orchestration/skypilot/',
    '/docs/tutorials/skypilot/cross-region-capacity-chasing': '/docs/tutorials/workflow-orchestration/skypilot/cross-region-capacity-chasing/',
    '/docs/tutorials/skypilot/resource-management-using-kueue': '/docs/tutorials/workflow-orchestration/skypilot/resource-management-using-kueue/',
    '/docs/tutorials/finetuning-gemma-3-1b-it-on-l4': '/docs/tutorials/finetuning/finetuning-gemma-3-1b-it-on-l4/',
    '/docs/tutorials/models-as-oci': '/docs/tutorials/storage/models-as-oci/',
    '/docs/tutorials/llamaindex': '/docs/tutorials/frameworks-and-pipelines/llamaindex/',
    '/docs/tutorials/langchain-chatbot': '/docs/tutorials/frameworks-and-pipelines/langchain-chatbot/',
    '/docs/tutorials/metaflow': '/docs/tutorials/frameworks-and-pipelines/metaflow/',
    '/docs/tutorials/mlflow': '/docs/tutorials/frameworks-and-pipelines/mlflow/',
    '/docs/tutorials/fungibility-recipes': '/docs/tutorials/gpu-tpu/fungibility-recipes/',
    '/docs/tutorials/ray-gke-tpus': '/docs/tutorials/gpu-tpu/ray-gke-tpus/',
    '/docs/tutorials/hf-gcs-transfer': '/docs/tutorials/storage/hf-gcs-transfer/',
    '/docs/tutorials/hyperdisk-ml': '/docs/tutorials/storage/hyperdisk-ml/',
}


def app(environ, start_response):
  try:
    if environ['PATH_INFO'].endswith('/'):
      environ['PATH_INFO'] = environ['PATH_INFO'][:-1]
    if environ['PATH_INFO'] in REDIRECTS:
      new_url = REDIRECTS[environ['PATH_INFO']]
      HTTP_HOST = environ.get('HTTP_HOST', '')
      start_response('301 Moved Permanently', [('Location', 'https://' + HTTP_HOST + new_url)])
      return []
    # Specify the file path
    file_path = "public/404.html"
    
    # Read the file content
    with open(file_path, 'rb') as file:
        response_body = file.read()
    
    # Send a 200 OK response with the file content
    start_response('200 OK', [('Content-Type', 'text/html'), ('Content-Length', str(len(response_body)))])
    return [response_body]
  except FileNotFoundError:
    # Handle the case where the file is not found
    response_body = b"404 - File Not Found"
    start_response('404 Not Found', [('Content-Type', 'text/html'), ('Content-Length', str(len(response_body)))])
    return [response_body]