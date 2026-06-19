"""RAG pipeline over a Knowledge Graph JSON file.

Builds an in-memory FAISS/Chroma-less vector store using LangChain's
InMemoryVectorStore so it works without external infrastructure.

Typical usage
-------------
    from src.rag.kg_rag import build_kg_retriever, search_kg

    retriever = build_kg_retriever(kg_dict)
    results   = search_kg(retriever, "what controls UV shutdown behaviour", k=5)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedding helper (reuses the same env vars as BaseAgent)
# ---------------------------------------------------------------------------


def _build_embeddings() -> OpenAIEmbeddings:
    """Return an OpenAIEmbeddings instance pointed at the local endpoint."""
    import os
    from pathlib import Path
    import httpx
    from dotenv import load_dotenv

    load_dotenv()

    base_url = os.environ.get("OPENAI_API_BASE", "")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")

    _project_root = Path(__file__).resolve().parents[2]
    if os.getenv("VERIFY_SSL", "").lower() == "false":
        ssl_verify: bool | str = False
    elif ca_bundle := os.getenv("SSL_CA_BUNDLE"):
        ssl_verify = str(_project_root / ca_bundle)
    else:
        ssl_verify = True

    return OpenAIEmbeddings(
        model=model,
        base_url=base_url,
        api_key=api_key,
        http_client=httpx.Client(verify=ssl_verify),
    )


# ---------------------------------------------------------------------------
# Document builders — one Document per KG node
# ---------------------------------------------------------------------------


def _variable_to_doc(v: dict[str, Any]) -> Document:
    parts = [
        f"VARIABLE: {v['name']}",
        f"Type: {v.get('type', '')}",
        f"Comment: {v.get('comment', '')}",
    ]
    if v.get("register_trace"):
        parts.append(f"Register: {v['register_trace']}")
    if v.get("semantic_summary"):
        parts.append(f"Summary: {v['semantic_summary']}")
    if v.get("assigned_by_tasks"):
        parts.append(f"Updated by: {', '.join(v['assigned_by_tasks'])}")
    if td := v.get("type_definition"):
        members = td.get("members", [])[:8]  # cap to keep token budget
        parts.append(f"Enum values: {', '.join(members)}")
    if re := v.get("register_enum"):
        vals = list(re.get("values", {}).keys())[:8]
        parts.append(f"Register enum settings: {', '.join(vals)}")
    return Document(
        page_content="\n".join(parts),
        metadata={"id": v["id"], "kind": "variable", "name": v["name"]},
    )


def _task_to_doc(t: dict[str, Any]) -> Document:
    parts = [
        f"TASK: {t['name']}",
        f"Comment: {t.get('comment', '')}",
    ]
    if t.get("semantic_summary"):
        parts.append(f"Summary: {t['semantic_summary']}")
    if t.get("updates_variables"):
        parts.append(f"Updates variables: {', '.join(t['updates_variables'])}")
    return Document(
        page_content="\n".join(parts),
        metadata={"id": t["id"], "kind": "task", "name": t["name"]},
    )


def _event_to_doc(e: dict[str, Any]) -> Document:
    parts = [
        f"EVENT: {e['name']}",
        f"Comment: {e.get('comment', '')}",
    ]
    if e.get("semantic_summary"):
        parts.append(f"Summary: {e['semantic_summary']}")
    return Document(
        page_content="\n".join(parts),
        metadata={"id": e["id"], "kind": "event", "name": e["name"]},
    )


def _covergroup_to_doc(cg: dict[str, Any]) -> Document:
    """Lightweight covergroup doc — just description + sampled variables.

    The full SV source is NOT embedded here; the direct-lookup tool handles
    returning the raw source when the agent requests a specific covergroup.
    """
    parts = [
        f"COVERGROUP: {cg['name']}",
        f"Sampled variables: {', '.join(cg.get('sampled_variables', []))}",
        f"Trigger: {cg.get('sampling_trigger_expression', '')}",
    ]
    if cg.get("semantic_summary"):
        parts.append(f"Summary: {cg['semantic_summary']}")
    # Include first 300 chars of description for semantic search
    doc_text = cg.get("documentation", "")
    if doc_text:
        parts.append(f"Description: {doc_text[:300]}")
    return Document(
        page_content="\n".join(parts),
        metadata={"id": cg["id"], "kind": "covergroup", "name": cg["name"]},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def kg_to_documents(kg: dict[str, Any]) -> list[Document]:
    """Convert all nodes in a KG dict into a flat list of Documents."""
    docs: list[Document] = []
    nodes = kg.get("nodes", {})
    for v in nodes.get("variables", []):
        docs.append(_variable_to_doc(v))
    for t in nodes.get("tasks", []):
        docs.append(_task_to_doc(t))
    for e in nodes.get("events", []):
        docs.append(_event_to_doc(e))
    for cg in nodes.get("covergroups", []):
        docs.append(_covergroup_to_doc(cg))
    logger.debug("KG → %d documents for embedding", len(docs))
    return docs


def build_kg_retriever(
    kg: dict[str, Any],
    k: int = 6,
) -> InMemoryVectorStore:
    """Embed all KG nodes and return an InMemoryVectorStore.

    The returned store exposes ``.similarity_search(query, k=k)`` for retrieval.
    """
    docs = kg_to_documents(kg)
    embeddings = _build_embeddings()
    store = InMemoryVectorStore.from_documents(docs, embeddings)
    logger.info("KG RAG store built: %d nodes embedded", len(docs))
    return store


def search_kg(
    store: InMemoryVectorStore,
    query: str,
    k: int = 6,
) -> list[dict[str, Any]]:
    """Run a similarity search and return a list of node summaries."""
    results = store.similarity_search(query, k=k)
    return [
        {
            "id": doc.metadata.get("id"),
            "kind": doc.metadata.get("kind"),
            "name": doc.metadata.get("name"),
            "content": doc.page_content,
        }
        for doc in results
    ]


def load_kg(kg_path: str | Path) -> dict[str, Any]:
    """Load a KG JSON file from disk."""
    with open(kg_path, encoding="utf-8") as f:
        return json.load(f)
