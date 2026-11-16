# Upstream provenance

Reviewed 2026-11-16. Adapted from two Skills in [openai/skills](https://github.com/openai/skills) at revision `49f948faa9258a0c61caceaf225e179651397431`. Latest path change for both was `5c8f1e26803bcfaffeceef1e7accbcf7e388417a` on 2026-02-02.

| Source path | Material reviewed | Adaptation |
| --- | --- | --- |
| [skills/.curated/security-best-practices](https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/security-best-practices) | Entrypoint, UI metadata, reference inventory, FastAPI and framework-agnostic browser guidance | Retains stack-aware evidence and verification. Omits unrelated frameworks, ID migration suggestions, production defaults and automatic report-file creation. |
| [skills/.curated/security-threat-model](https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/security-threat-model) | Entrypoint, UI metadata, prompt template, controls and asset references | Retains boundaries, assets, actor capabilities and prioritized abuse scenarios. Replaces the generic template and unconditional clarification pause with this project's contracts and conditional assumptions. |

The two source licenses are byte-identical Apache License 2.0. The upstream [license text](LICENSE.txt) is preserved. This modified adaptation combines both methods and includes no upstream scripts, templates or tool dependencies.

The [source repository is deprecated](https://github.com/openai/skills/blob/49f948faa9258a0c61caceaf225e179651397431/README.md). OpenAI points to [openai/plugins](https://github.com/openai/plugins), where current examples include the [Codex Security plugin](https://github.com/openai/plugins/tree/d420d13051bffe4543ed39e30fa46b87148e47dd/plugins/codex-security). The pinned historical Skills are the provenance here. No plugin is required.
