"""Infrastructure layer (adapters): concrete implementations of domain interfaces,
selected at runtime via environment variables. This is the ONLY layer allowed to
import third-party provider SDKs (openai, google-generativeai, ollama, qdrant-client, etc.)."""
