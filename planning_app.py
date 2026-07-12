import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from openai import OpenAI
from supabase import create_client

import ask_planning_ai_v11_final_backend as engine


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


class SourceItem(BaseModel):
    title: str
    page_number: int | None = None
    section_title: str | None = None
    publication_date: str | None = None
    version: str | None = None
    publisher: str | None = None


class ChatResponse(BaseModel):
    question: str
    answer: str
    language: str
    greek_search_query: str
    sources: list[SourceItem]


state: dict[str, Any] = {}


def unique_sources(rows: list[dict[str, Any]]) -> list[SourceItem]:
    seen: set[tuple[str, int | None]] = set()
    sources: list[SourceItem] = []

    for row in rows:
        title = row.get("title") or "Unknown document"
        page_number = row.get("page_number")
        key = (title, page_number)

        if key in seen:
            continue

        seen.add(key)
        sources.append(
            SourceItem(
                title=title,
                page_number=page_number,
                section_title=row.get("section_title"),
                publication_date=(
                    str(row.get("publication_date"))
                    if row.get("publication_date")
                    else None
                ),
                version=row.get("version"),
                publisher=row.get("publisher"),
            )
        )

    return sources


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()

    supabase_url = engine.require_env("SUPABASE_URL")
    supabase_secret_key = engine.require_env("SUPABASE_SECRET_KEY")
    openai_api_key = engine.require_env("OPENAI_API_KEY")

    state["supabase"] = create_client(supabase_url, supabase_secret_key)
    state["openai"] = OpenAI(api_key=openai_api_key)
    state["all_rows"] = engine.fetch_all_chunks_with_metadata(state["supabase"])

    print(
        f"Cyprus Planning API ready — loaded "
        f"{len(state['all_rows'])} knowledge-base chunks."
    )

    yield

    state.clear()


app = FastAPI(
    title="Cyprus Planning AI API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "chunks_loaded": len(state.get("all_rows", [])),
        "model": engine.ANSWER_MODEL,
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    question = payload.question.strip()

    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    supabase = state.get("supabase")
    openai_client = state.get("openai")
    all_rows = state.get("all_rows")

    if not supabase or not openai_client or all_rows is None:
        raise HTTPException(status_code=503, detail="API is not ready yet.")

    try:
        greek_search_query = engine.generate_greek_search_query(
            question,
            openai_client,
        )

        semantic_rows = engine.merge_unique_rows([
            engine.semantic_candidates(question, openai_client, supabase),
            engine.semantic_candidates(
                greek_search_query,
                openai_client,
                supabase,
            ),
        ])

        lexical_rows = engine.merge_unique_rows([
            engine.lexical_candidates(question, all_rows),
            engine.lexical_candidates(greek_search_query, all_rows),
        ])

        direct_rows = engine.merge_unique_rows([
            engine.direct_rule_candidates(question, all_rows),
            engine.direct_rule_candidates(greek_search_query, all_rows),
        ])

        hybrid_rows = engine.merge_and_rerank(
            semantic_rows,
            lexical_rows,
            direct_rows,
        )

        reranked_rows = engine.llm_rerank_candidates(
            question,
            hybrid_rows,
            openai_client,
        )

        context_rows = engine.expand_with_adjacent_pages(
            reranked_rows,
            supabase,
        )

        answer = engine.answer_question(
            question,
            context_rows,
            openai_client,
        )

        return ChatResponse(
            question=question,
            answer=answer,
            language=engine.output_language_for_question(question),
            greek_search_query=greek_search_query,
            sources=unique_sources(reranked_rows),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Planning AI request failed: {exc}",
        ) from exc
