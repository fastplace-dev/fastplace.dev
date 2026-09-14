# AI-native apps

AI in Fastplace is infrastructure, not an integration: tools, agents,
streaming, and vector search are framework primitives with the same
ergonomics as routing or the ORM.

## Tools: typed functions → JSON schemas

`@Tool` derives an LLM function-calling schema from type hints and the
docstring — no schema authorship, no drift:

```python
from fastplace.ai import Tool


@Tool(description="Search the internal knowledge base")
async def search_docs(query: str, limit: int = 5) -> list[dict]:
    """Full-text search over knowledge items.

    Args:
        query: free-text search phrase.
        limit: maximum number of excerpts to return.
    """
    return await KnowledgeRepository().search(query, limit=limit)
```

Register tools in `app/ai/tools/`; the registry is swept at boot.

## Agents

```python
from fastplace.ai import Agent

assistant = Agent(
    model="gpt-4o-mini",  # any LiteLLM-supported model
    system_prompt="You help with the knowledge base. Cite sources.",
    tools=[search_docs],
)
```

Register agents in `app/ai/agents/` as factories and expose them on
`routes/ai.py` — the endpoint returns an SSE stream:

```python
from app.ai.agents.assistant import assistant_agent


@router.post("/assistant")
async def assistant_endpoint(request):
    data = await request.json()
    return assistant_agent().stream_response(data["message"], history=data.get("history"))
```

The stream is returned unawaited — `stream_response(message)` is a plain
call that hands back the streaming response.

## Streaming on the frontend

```jsx
import { useAIStream } from "@fastplace/ai-react";

export default function Assistant() {
    const { messages, input, setInput, handleSubmit, isStreaming } = useAIStream({
        endpoint: "/ai/assistant",
    });
    /* render messages, wire a form to handleSubmit */
}
```

The hook handles SSE parsing, abort, and batched state updates — tool
calls surface on the message objects as they resolve.

## Embeddings & vector search

```python
from fastplace.orm import VectorField


class KnowledgeItem(Model):
    __tablename__ = "knowledge_items"

    body: str
    embedding: list[float] = VectorField(1536)  # type: ignore[assignment]
```

```python
from fastplace.ai import embed

vector = await embed(item.body)  # config-driven provider
hits = await KnowledgeItem.vector_search(vector, limit=5)
```

- `VectorField` bakes per backend — `VECTOR(dim)` on PostgreSQL (pgvector),
  JSON on SQLite. Generate migrations with the target `DATABASE_URL` set.
- Vector stores register in `app/ai/vectors/`; capability-gated searches
  ride the same global-scope engine as everything else — tenant filters
  and soft delete apply to similarity results too.

## Structured output

With the `ai` extra (LiteLLM + Instructor), agents can return validated
Pydantic models instead of prose — schema enforcement happens at the
provider layer, failures retry rather than crash.

## Guardrails worth knowing

- Bound tool results (labeled, excerpt-limited) before they reach a
  model — context is a budget.
- Treat model output as untrusted input wherever it is rendered or
  executed; the bridge escapes it like any other data.
- Keep per-tenant and per-user data scoping **inside** tool
  implementations — the model cannot be trusted to ask for less.
