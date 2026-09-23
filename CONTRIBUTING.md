# Contributing

Thank you for your interest in making Pie better!

## Questions and feature ideas

Join our [Discord community](https://discord.gg/R62Udyzum) to ask questions, share feedback, or discuss ideas.
If you'd like to add a new feature, we're happy to help you get started.

## Sending a change

Work on a branch and open a pull request; nobody pushes to `main`. CI runs
every check in parallel, a reviewer approves, and the merge queue tests the
PR on top of the current `main` before squashing it in. The PR title
becomes the commit title, so write it as `area: what changed`.

Once per clone, check your machine and install the git hook:

```sh
python3 ci/ci.py setup
```

Run what CI runs before you push:

```sh
python3 ci/ci.py run pr
```

[`ci/README.md`](ci/README.md) explains the levels, the GPU label and how
to add a check or a platform.

## LLM usage policy

Pie follows the Rust project's [LLM usage policy](https://forge.rust-lang.org/policies/llm-usage.html). Please review it before using an LLM to prepare a contribution.