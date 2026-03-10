# babeldoc-jobpack

`babeldoc-jobpack` adds a two-step, file-based workflow on top of BabelDOC:

1. Export translation jobs from a PDF into a portable package.
2. Apply translated text back into the package and render a translated PDF.

## CLI

```bash
babeldoc-export-jobs input.pdf --job-dir ./jobpack
babeldoc-apply-jobs ./jobpack --translations ./translations.json --output-dir ./out
```

