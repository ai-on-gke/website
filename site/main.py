def app(_, start_response):
  try:
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