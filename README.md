# GKE AI Labs Website

This repository contains the source code for the [GKE AI Labs website](https://gke-ai-labs.dev).


## Contributing

The AI on GKE website website can be run locally and on AppEngine.
Please read the [contributing](CONTRIBUTING.md) guide for directions on submitting pull requests.

### Embed .md file from external public github repository

In case you want to add .md file from external github repository:

1. Provide additional following parameters to the frontmatter:

```yaml
    externalSource:
        repository: "organisation/repository"
        branch: "branchName"
        filePath: "filePath"
```

2. Add ```{{% include-external %}}``` as a content.

You can refer to this [example](site/content/docs/blueprints/external/index.md).

Content of this file will be automatically updated on each rebuild. In case some urls are not valild build process will fail.
Last modified date will set to the date of last commit in origin repository.

## License

* The use of the assets contained in this repository is subject to compliance with [Google's AI Principles](https://ai.google/responsibility/principles/)
* See [LICENSE](/LICENSE)


## Build & Deploy

### Run website locally

```docker run --rm -it -v $(pwd):/src -p 1313:1313 floryn90/hugo:ext-alpine server```

### Build static files locally:
- Use `build -e production` in order to get minified source files

```docker run --mount type=bind,src=./,dst=/src floryn90/hugo:ext-alpine build```

### Deployment to AppEngine
Deployment happens automatically on push to main branch using following github actions workflow [file](/.github/workflows/website.yaml) 

