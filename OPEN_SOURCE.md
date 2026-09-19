# Open-source reuse register

This is the implementation shortlist for the local-first CivitasX direction.
Each dependency must be pinned and checked before adoption. Confirm the exact
license at the selected commit, inspect transitive dependencies, preserve
copyright and license notices, and add the package to a generated SBOM.

| Area | Candidate | How to use it | License / boundary |
|---|---|---|---|
| Chat workspace | [LibreChat](https://github.com/danny-avila/LibreChat) | Borrow or fork chat composer, conversation layout, attachments, message search, MCP wiring, and multi-user patterns. Keep CivitasX ticket and feed views separate. | Repository identifies itself as MIT. Recheck the selected commit and dependencies. |
| Agent UI/API patterns | [OpenHands](https://github.com/OpenHands/OpenHands) | Reuse Agent Server and TypeScript client patterns for streaming conversations, tool events, resumable work, and user takeover. | Core repository is MIT; the repository documents exceptions for enterprise material. Exclude enterprise-only code. |
| Agent state | [LangGraph](https://github.com/langchain-ai/langgraph) | Use a local state graph for intent detection, clarification, evidence retrieval, complaint preparation, review gates, and recovery. | MIT. Keep deployment-specific services behind our own interface. |
| Browser execution | [browser-use](https://github.com/browser-use/browser-use) | Evaluate for local Playwright navigation, observation, typed browser actions, and form preparation. | MIT repository, but audit transitive dependencies and pin a reviewed release. |
| Browser evaluation | [BrowserGym](https://github.com/ServiceNow/BrowserGym) | Use environments and evaluation ideas to test government-site navigation; it is not the production browser runtime. | Apache-2.0. |
| Local inference | [llama.cpp](https://github.com/ggml-org/llama.cpp) | Run quantized models locally and expose a small provider interface that can later map to Bedrock. | MIT. Model weights have separate licenses and must be checked individually. |
| Local inference server | [Ollama](https://github.com/ollama/ollama) | Use its local HTTP API during development when it reduces setup friction; keep the app provider-neutral. | Check the repository license and each downloaded model's license before distribution. |
| Retrieval | [LlamaIndex](https://github.com/run-llama/llama_index) | Reuse document loaders, chunking, metadata, and retrieval evaluation if they fit the existing provenance contracts. | Repository is MIT; retain CivitasX's source/page/hash validation layer. |
| Feed ranking reference | [X recommendation algorithm](https://github.com/twitter/the-algorithm) | Use the public architecture as inspiration for candidate sources, explicit user signals, ranking, mixing, and visibility filtering. CivitasX uses a small transparent civic scorer instead of copying the heavyweight services. | AGPL-3.0. No X source code is embedded; recheck the license before any future reuse. |
| Reddit-like reference | [Lemmy](https://github.com/LemmyNet/lemmy) | Study post, vote, comment, moderation, notification, and feed interaction patterns. Consider an isolated service only if its license obligations are acceptable. | AGPL-3.0. Do not copy code into the CivitasX application without deliberately accepting copyleft obligations. |
| Community platform reference | [Discourse](https://github.com/discourse/discourse) | Study moderation, trust, reporting, and discussion workflows. | GPL-2.0. Use as product research or an isolated deployment unless the project intentionally adopts the license. |

## Reuse rules

1. Prefer MIT, BSD, or Apache-2.0 components for code linked into CivitasX.
2. Treat GPL, AGPL, SSPL, BSL, source-available, and hosted-only components as
   separate legal and architectural decisions.
3. Do not copy a repository's branding, product name, screenshots, or private
   code. Copy only code whose license permits the intended use.
4. Preserve license files and copyright headers. Record the source URL, commit,
   license, modifications, and notices in `THIRD_PARTY_NOTICES.md`.
5. Scan the complete dependency tree before release. A permissive top-level
   license does not make every transitive dependency permissive.
6. Check model-weight licenses separately from runtime-library licenses.

## Suggested build order

- Start with the existing FastAPI and React shell.
- Add a small chat message/attachment contract inspired by LibreChat or
  OpenHands rather than replacing the product shell wholesale.
- Add a LangGraph-compatible local state machine behind the Agent API.
- Evaluate browser-use locally only after the complaint review gate exists.
- Keep the feed implementation thin and product-specific; importing a complete
  Reddit clone creates unnecessary license, migration, and moderation risk.
