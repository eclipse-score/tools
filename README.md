# score-tools

Eclipse S-CORE tool version definitions - Yet another approach

## Example

```bash
# Use centrally managed tool version (via uv, pip, poetry etc)
$ uvx --from "git+https://github.com/eclipse-score/tools.git@v0[dev]" ruff --version
ruff 0.15.0

# While local setup remains unmodified
$ ruff --version
ruff 0.14.3
```
