# Brain Engine

## Cognitive Platform for Autonomous Property Management

Brain Engine is the AI decision layer behind [Cendra AI](https://app.cendra.ai).
It is not a chatbot — it is a **cognitive engine** that reasons over every
guest and operational interaction, captures the full decision context as
`DecisionCase` rows, mines behavioural `PatternRule`s from that history,
enforces safety `Blocker`s before sensitive actions, and keeps improving
through nightly consolidation and on-demand pattern extraction.

- **Live:** https://brain-engine-dev.botel.ai
- **Interactive API:** https://brain-engine-dev.botel.ai/docs (Swagger UI)
- **ReDoc:** https://brain-engine-dev.botel.ai/redoc
- **Deployment:** Azure Kubernetes Service (cluster `botelai-prod`, namespace `dev`)
