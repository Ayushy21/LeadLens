# Tokenizer data

The files in `src/lead_enricher/tokenizer_cache/` are the official `cl100k_base` and `o200k_base`
BPE encoding data fetched and hash-verified by tiktoken 0.14.0. They are included so that ordinary
tests and the demo never need network access to initialize the tokenizer. The installed tiktoken
library and its associated license remain a project dependency.

| Cache file | Encoding | Original data URL |
|---|---|---|
| `9b5ad71b2ce5302211f9c61530b329a4922fc6a4` | cl100k_base | https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken |
| `fb374d419588a4632f3f557e76b4b70aebbca790` | o200k_base | https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken |

Upstream: https://github.com/openai/tiktoken. See `tiktoken-LICENSE.txt` in this directory.
