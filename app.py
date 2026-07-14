import json
import asyncio
import time
import math
import os
import re
import sys
import unicodedata
import httpx
from collections import Counter
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client

from planning_rules import (
    RULE_CATALOG,
    RULE_ENGINE_VERSION,
    calculate_zoned_capacity,
    compact_rule_context,
    evaluate_parcel_rules,
)


EMBEDDING_MODEL = "text-embedding-3-small"
ANSWER_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-5.6-terra")
QUERY_EXPANSION_MODEL = os.getenv("OPENAI_QUERY_EXPANSION_MODEL", ANSWER_MODEL)
VERIFY_MODEL = os.getenv("OPENAI_VERIFY_MODEL", ANSWER_MODEL)

SEMANTIC_THRESHOLD = 0.18
SEMANTIC_MATCH_COUNT_PER_QUERY = 16
FINAL_HITS = 40
LEXICAL_CANDIDATES = 40
ADJACENT_EXPANSION_TOP_N = 5
MAX_CONTEXT_CHARS = 26000
RERANK_CANDIDATES = 40
DIRECT_RULE_CANDIDATES = 12
RERANK_TOP_N = 10
RERANK_SNIPPET_CHARS = 1400
RERANK_MODEL = os.getenv("OPENAI_RERANK_MODEL", ANSWER_MODEL)
PLANNING_ANALYSIS_MODEL = os.getenv("OPENAI_PLANNING_ANALYSIS_MODEL", ANSWER_MODEL)

SEMANTIC_WEIGHT = 0.64
LEXICAL_WEIGHT = 0.26
RECENCY_WEIGHT = 0.06
PRIORITY_WEIGHT = 0.04


GREEK_STOPWORDS = {
    "και", "ή", "η", "ο", "οι", "το", "τα", "του", "της", "των", "τον", "την",
    "σε", "στο", "στη", "στην", "στον", "στα", "στις", "στους", "με", "από",
    "για", "ως", "που", "ποιο", "ποια", "ποιος", "ποιες", "ποιοι", "τι", "πως",
    "πώς", "είναι", "ισχύει", "ισχυει", "ένα", "μια", "ένας", "αν", "να", "θα",
    "δεν", "τουλάχιστον", "μέχρι", "πάνω", "κάτω", "μεταξύ", "πρέπει",
}

ENGLISH_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "does", "do", "what", "how", "can", "must", "should", "from",
}


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing {name} in .env")
    return value


def normalize_text(text: str) -> str:
    text = text or ""
    text = unicodedata.normalize("NFD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tokenize(text: str) -> List[str]:
    normalized = normalize_text(text)
    return re.findall(r"[0-9a-zα-ω]+", normalized)


def meaningful_terms(question: str) -> List[str]:
    terms = []
    seen = set()

    for token in tokenize(question):
        if token in GREEK_STOPWORDS or token in ENGLISH_STOPWORDS:
            continue
        if len(token) < 3:
            continue
        if token not in seen:
            terms.append(token)
            seen.add(token)

    return terms


def token_root(token: str) -> str:
    """
    Light inflection-tolerant prefix matching.
    This is intentionally conservative: never shorter than 5 characters.
    """
    if len(token) >= 10:
        return token[:-3]
    if len(token) >= 7:
        return token[:-2]
    if len(token) >= 6:
        return token[:-1]
    return token


def parse_year(value: Any) -> int:
    if not value:
        return 0
    try:
        return int(str(value)[:4])
    except Exception:
        return 0


def recency_score(row: Dict[str, Any]) -> float:
    year = parse_year(row.get("publication_date"))
    if year >= 2025:
        return 1.0
    if year >= 2024:
        return 0.8
    if year >= 2020:
        return 0.5
    if year > 0:
        return 0.2
    return 0.0


def priority_score(row: Dict[str, Any]) -> float:
    try:
        return min(float(row.get("authority_priority") or 0.0), 100.0) / 100.0
    except Exception:
        return 0.0


def embed_text(text: str, openai_client: OpenAI) -> List[float]:
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def run_semantic_search(
    query_text: str,
    openai_client: OpenAI,
    supabase: Any,
) -> List[Dict[str, Any]]:
    query_embedding = embed_text(query_text, openai_client)

    response = supabase.rpc(
        "match_kb_chunks",
        {
            "query_embedding": query_embedding,
            "match_threshold": SEMANTIC_THRESHOLD,
            "match_count": SEMANTIC_MATCH_COUNT_PER_QUERY,
        },
    ).execute()

    return response.data or []


def batch_semantic_candidates(
    query_texts: List[str],
    openai_client: OpenAI,
    supabase: Any,
) -> List[Dict[str, Any]]:
    """Run one batched embedding request, then semantic search for each query."""
    queries = [q.strip() for q in query_texts if q and q.strip()]
    if not queries:
        return []

    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=queries,
    )
    embeddings = [item.embedding for item in response.data]

    groups: List[List[Dict[str, Any]]] = []
    for embedding in embeddings:
        result = supabase.rpc(
            "match_kb_chunks",
            {
                "query_embedding": embedding,
                "match_threshold": SEMANTIC_THRESHOLD,
                "match_count": SEMANTIC_MATCH_COUNT_PER_QUERY,
            },
        ).execute()
        groups.append(result.data or [])

    return merge_unique_rows(groups)



def contains_greek(text: str) -> bool:
    return bool(re.search(r"[Α-ΩΆΈΉΊΌΎΏα-ωάέήίϊΐόύϋΰώ]", text or ""))


def generate_greek_search_query(
    question: str,
    openai_client: OpenAI,
) -> str:
    """
    Convert a non-Greek user question into a concise Greek planning-regulation
    search query. This is for retrieval only, not for answering the user.
    """
    if contains_greek(question):
        return question

    instructions = """
You translate user questions into concise Greek search queries for a Cyprus
planning-regulations knowledge base.

Rules:
1. Do NOT answer the question.
2. Preserve the exact technical meaning.
3. Use Cyprus planning terminology where appropriate.
4. Prefer terms likely to appear in Greek planning documents, for example:
   - building coefficient -> συντελεστής δόμησης
   - coverage -> ποσοστό κάλυψης
   - basement -> υπόγειο
   - auxiliary building -> βοηθητική οικοδομή
   - setback / boundary distance -> απόσταση από τα σύνορα
   - parking space -> χώρος στάθμευσης
5. Return ONLY the Greek search query, with no quotation marks or explanation.
"""

    response = openai_client.responses.create(
        model=QUERY_EXPANSION_MODEL,
        instructions=instructions.strip(),
        input=question.strip(),
    )

    greek_query = (response.output_text or "").strip()

    if not greek_query:
        return question

    return greek_query


def merge_unique_rows(
    row_groups: List[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[Any, Any, str], Dict[str, Any]] = {}

    for rows in row_groups:
        for row in rows:
            key = (
                row.get("document_id"),
                row.get("page_number"),
                row.get("content") or "",
            )

            existing = merged.get(key)
            if not existing:
                merged[key] = row
                continue

            # Keep the strongest values seen across original-language and
            # Greek-expanded retrieval runs.
            for field in (
                "similarity",
                "lexical_score",
                "raw_lexical_score",
                "direct_rule_score",
                "direct_score",
            ):
                new_value = float(row.get(field) or 0.0)
                old_value = float(existing.get(field) or 0.0)
                if new_value > old_value:
                    existing[field] = row.get(field)

    return list(merged.values())


def semantic_candidates(
    question: str,
    openai_client: OpenAI,
    supabase: Any,
) -> List[Dict[str, Any]]:
    queries = [
        question,
        (
            f"{question}\n"
            "Εξαιρέσεις, προϋποθέσεις, ειδικές περιπτώσεις, "
            "δεν προσμετράται, εξαιρείται, μερική προσμέτρηση, ανάλογα με τη χρήση."
        ),
        (
            f"{question}\n"
            "Ισχύουσες νεότερες πρόνοιες 2026, Εντολή 4/2026, "
            "τρέχοντες κανόνες, μεταβατικές ή καταργημένες πρόνοιες και ειδικές εξαιρέσεις."
        ),
    ]

    merged: Dict[Tuple[Any, Any, str], Dict[str, Any]] = {}

    for query_text in queries:
        for row in run_semantic_search(query_text, openai_client, supabase):
            key = (
                row.get("document_id"),
                row.get("page_number"),
                row.get("content") or "",
            )
            existing = merged.get(key)
            if not existing or float(row.get("similarity") or 0.0) > float(existing.get("similarity") or 0.0):
                merged[key] = row

    return list(merged.values())


def fetch_all_chunks_with_metadata(supabase: Any) -> List[Dict[str, Any]]:
    docs_response = (
        supabase.table("kb_documents")
        .select("id,title,publisher,publication_date,version,authority_priority")
        .execute()
    )
    docs = {row["id"]: row for row in (docs_response.data or [])}

    chunks_response = (
        supabase.table("kb_chunks")
        .select("id,document_id,page_number,section_title,content")
        .execute()
    )

    rows = []
    for chunk in chunks_response.data or []:
        doc = docs.get(chunk.get("document_id"), {})
        rows.append({**chunk, **doc, "document_id": chunk.get("document_id")})

    return rows


def lexical_candidates(
    question: str,
    all_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    terms = meaningful_terms(question)
    if not terms:
        return []

    roots = {term: token_root(term) for term in terms}

    tokenized_rows = []
    document_frequency = Counter()

    for row in all_rows:
        section_text = row.get("section_title") or ""
        combined_text = f"{section_text}\n{row.get('content') or ''}"
        content_tokens = tokenize(combined_text)
        token_set = set(content_tokens)

        matched_terms = set()
        for term in terms:
            root = roots[term]
            if term in token_set or any(tok.startswith(root) for tok in token_set):
                matched_terms.add(term)

        for term in matched_terms:
            document_frequency[term] += 1

        tokenized_rows.append((row, content_tokens, token_set))

    total_docs = max(len(all_rows), 1)
    normalized_question = normalize_text(question)

    scored = []

    for row, content_tokens, token_set in tokenized_rows:
        score = 0.0
        exact_matches = 0
        root_matches = 0

        for term in terms:
            root = roots[term]
            df = document_frequency.get(term, 0)
            idf = math.log((total_docs + 1) / (df + 1)) + 1.0

            if term in token_set:
                score += 1.0 * idf
                exact_matches += 1
            elif any(tok.startswith(root) for tok in token_set):
                score += 0.72 * idf
                root_matches += 1

        normalized_content = normalize_text(row.get("content") or "")
        normalized_section = normalize_text(row.get("section_title") or "")

        # Strong bonus when the query term appears in the detected section title.
        section_tokens = set(tokenize(row.get("section_title") or ""))
        section_match_count = 0
        for term in terms:
            root = roots[term]
            if term in section_tokens or any(tok.startswith(root) for tok in section_tokens):
                section_match_count += 1
                score += 3.5

        # Phrase bonus when several important question words occur near each other.
        matched_count = exact_matches + root_matches
        coverage = matched_count / max(len(terms), 1)
        score += coverage * 2.0

        if section_match_count:
            score += min(section_match_count, 3) * 1.5

        # Small exact-phrase bonus.
        if len(normalized_question) >= 8 and normalized_question in normalized_content:
            score += 4.0

        if score > 0:
            scored.append({**row, "raw_lexical_score": score})

    scored.sort(key=lambda r: float(r.get("raw_lexical_score") or 0.0), reverse=True)

    if not scored:
        return []

    max_score = float(scored[0]["raw_lexical_score"]) or 1.0
    for row in scored:
        row["lexical_score"] = float(row["raw_lexical_score"]) / max_score

    return scored[:LEXICAL_CANDIDATES]



DOMAIN_RELATION_EXPANSIONS = {
    # Questions like "Μετρά ... στον συντελεστή δόμησης;"
    "μετρ": [
        "υπολογισ", "λογιζ", "προσμετρ", "συνυπολογ", "εξαιρ",
    ],
    "λογιζ": [
        "υπολογισ", "μετρ", "προσμετρ", "συνυπολογ", "εξαιρ",
    ],
    "προσμετρ": [
        "υπολογισ", "λογιζ", "μετρ", "συνυπολογ", "εξαιρ",
    ],
    "εξαιρ": [
        "υπολογισ", "λογιζ", "μετρ", "προσμετρ", "συνυπολογ",
    ],
}


def rootify(token: str) -> str:
    token = normalize_text(token)
    if len(token) >= 10:
        return token[:-3]
    if len(token) >= 7:
        return token[:-2]
    if len(token) >= 6:
        return token[:-1]
    return token


def direct_rule_candidates(
    question: str,
    all_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    High-precision rule matching over the entire KB.

    This is designed to catch passages that literally encode the asked legal
    relationship, even when vector similarity or recency boosts rank them lower.

    Example:
    "Μετρά το υπόγειο στον συντελεστή δόμησης;"
    should strongly favor a chunk containing:
    "ΥΠΟΓΕΙΟ ... Εξαιρείται από τον υπολογισμό του συντελεστή δόμησης ..."
    """
    q_tokens = meaningful_terms(question)
    q_roots = [rootify(t) for t in q_tokens]

    # Expand relational verbs into legal-document wording.
    relation_roots = set()
    for root in q_roots:
        for trigger, expansions in DOMAIN_RELATION_EXPANSIONS.items():
            if root.startswith(trigger) or trigger.startswith(root):
                relation_roots.update(expansions)

    # Important concept roots are the non-stopword roots from the question.
    concept_roots = [r for r in q_roots if len(r) >= 4]

    scored = []

    for row in all_rows:
        combined = normalize_text(
            f"{row.get('section_title') or ''}\n{row.get('content') or ''}"
        )

        # Root-level concept matches.
        concept_hits = sum(1 for root in concept_roots if root in combined)
        relation_hits = sum(1 for root in relation_roots if root in combined)

        # Strong phrase/concept bonuses for planning-coefficient questions.
        building_coeff_bonus = 0.0
        if "συντελεστ" in combined and "δομησ" in combined:
            building_coeff_bonus = 4.0

        section_bonus = 0.0
        section_norm = normalize_text(row.get("section_title") or "")
        if any(root in section_norm for root in concept_roots):
            section_bonus = 3.0

        # Require at least meaningful concept overlap.
        if concept_hits == 0:
            continue

        score = (
            concept_hits * 2.2
            + relation_hits * 2.5
            + building_coeff_bonus
            + section_bonus
        )

        # Big bonus when multiple question concepts co-occur with a legal relation.
        if concept_hits >= 2 and relation_hits >= 1:
            score += 7.0
        if concept_hits >= 3 and relation_hits >= 1:
            score += 4.0

        # Direct exclusion/counting language is especially valuable.
        if "εξαιρ" in combined and "υπολογισ" in combined:
            score += 4.0
        if "λογιζ" in combined or "προσμετρ" in combined or "συνυπολογ" in combined:
            score += 2.0

        if score > 0:
            scored.append({**row, "direct_rule_score": score})

    scored.sort(
        key=lambda r: float(r.get("direct_rule_score") or 0.0),
        reverse=True,
    )

    return scored[:DIRECT_RULE_CANDIDATES]


def merge_and_rerank(
    semantic_rows: List[Dict[str, Any]],
    lexical_rows: List[Dict[str, Any]],
    direct_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[Any, Any, str], Dict[str, Any]] = {}

    def key_for(row: Dict[str, Any]) -> Tuple[Any, Any, str]:
        return (
            row.get("document_id"),
            row.get("page_number"),
            row.get("content") or "",
        )

    for row in semantic_rows:
        key = key_for(row)
        merged[key] = {
            **row,
            "semantic_score": float(row.get("similarity") or 0.0),
            "lexical_score": 0.0,
        }

    for row in lexical_rows:
        key = key_for(row)
        if key in merged:
            merged[key]["lexical_score"] = float(row.get("lexical_score") or 0.0)
        else:
            merged[key] = {
                **row,
                "similarity": 0.0,
                "semantic_score": 0.0,
                "lexical_score": float(row.get("lexical_score") or 0.0),
            }

    # Force direct rule matches into the candidate pool.
    max_direct = max(
        [float(r.get("direct_rule_score") or 0.0) for r in direct_rows] or [1.0]
    )
    for row in direct_rows:
        key = key_for(row)
        normalized_direct = float(row.get("direct_rule_score") or 0.0) / max_direct
        if key in merged:
            merged[key]["direct_rule_score"] = float(row.get("direct_rule_score") or 0.0)
            merged[key]["direct_score"] = normalized_direct
        else:
            merged[key] = {
                **row,
                "similarity": 0.0,
                "semantic_score": 0.0,
                "lexical_score": 0.0,
                "direct_score": normalized_direct,
            }

    rows = list(merged.values())

    for row in rows:
        semantic = float(row.get("semantic_score") or 0.0)
        lexical = float(row.get("lexical_score") or 0.0)
        direct = float(row.get("direct_score") or 0.0)

        row["hybrid_score"] = (
            0.50 * semantic
            + 0.20 * lexical
            + 0.22 * direct
            + RECENCY_WEIGHT * recency_score(row)
            + PRIORITY_WEIGHT * priority_score(row)
        )

    rows.sort(
        key=lambda r: (
            float(r.get("hybrid_score") or 0.0),
            float(r.get("semantic_score") or 0.0),
            float(r.get("lexical_score") or 0.0),
        ),
        reverse=True,
    )

    return rows[:FINAL_HITS]



def llm_rerank_candidates(
    question: str,
    candidates: List[Dict[str, Any]],
    openai_client: OpenAI,
) -> List[Dict[str, Any]]:
    """
    Second-stage semantic/legal reranker.

    Hybrid retrieval is good at recall, but can still rank a merely related newer
    passage above an older passage that directly states the rule. This reranker
    sees the actual candidate text and prioritizes direct answerability first.
    """
    pool = candidates[:RERANK_CANDIDATES]
    if not pool:
        return []

    blocks = []
    for i, row in enumerate(pool, start=1):
        content = (row.get("content") or "").strip()
        if len(content) > RERANK_SNIPPET_CHARS:
            content = content[:RERANK_SNIPPET_CHARS] + "…"

        blocks.append(
            f"CANDIDATE {i}\n"
            f"Document: {row.get('title')}\n"
            f"Publication date: {row.get('publication_date')}\n"
            f"Page: {row.get('page_number')}\n"
            f"Section: {row.get('section_title') or 'Unknown'}\n"
            f"Hybrid score: {float(row.get('hybrid_score') or 0.0):.4f}\n"
            f"Text:\n{content}\n"
        )

    instructions = """
You rerank source excerpts for a Cyprus planning-regulations question.

Rank by DIRECT ANSWERABILITY first:
1. A passage that explicitly states the rule asked about ranks above a passage that is merely related.
2. A passage containing the exact legal relationship in the question ranks highly even if it is older.
3. Newer sources matter for current applicability, but do not bury an older passage that directly states the rule; include both when the newer source may qualify it.
4. Prefer passages containing conditions, exceptions, exclusions, and definitions that materially affect the answer.
5. Do not answer the user's question. Only rank candidate indices.

Return ONLY valid JSON in this exact shape:
{"ranked_indices":[1,2,3,4,5,6,7,8,9,10]}

Use at most 10 indices. Do not include indices that are not useful.
"""

    prompt = (
        f"QUESTION:\n{question}\n\n"
        "CANDIDATES:\n\n"
        + "\n\n".join(blocks)
    )

    try:
        response = openai_client.responses.create(
            model=RERANK_MODEL,
            instructions=instructions.strip(),
            input=prompt.strip(),
        )
        text = response.output_text.strip()

        # Be tolerant if the model accidentally wraps JSON in prose/code fences.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return pool[:RERANK_TOP_N]

        data = json.loads(text[start:end + 1])
        indices = data.get("ranked_indices", [])

        reranked = []
        seen = set()
        for idx in indices:
            try:
                pos = int(idx) - 1
            except Exception:
                continue
            if 0 <= pos < len(pool) and pos not in seen:
                reranked.append(pool[pos])
                seen.add(pos)
            if len(reranked) >= RERANK_TOP_N:
                break

        # Fill any remaining slots from the original hybrid order.
        for pos, row in enumerate(pool):
            if pos not in seen:
                reranked.append(row)
                seen.add(pos)
            if len(reranked) >= RERANK_TOP_N:
                break

        return reranked

    except Exception as exc:
        print(f"Reranker warning: {exc}")
        print("Falling back to hybrid ranking.")
        return pool[:RERANK_TOP_N]


def expand_with_adjacent_pages(
    rows: List[Dict[str, Any]],
    supabase: Any,
) -> List[Dict[str, Any]]:
    expanded: List[Dict[str, Any]] = []
    seen: set[Tuple[Any, Any, str]] = set()

    for row in rows:
        key = (
            row.get("document_id"),
            row.get("page_number"),
            row.get("content") or "",
        )
        if key not in seen:
            expanded.append({**row, "context_type": "hybrid_hit"})
            seen.add(key)

    for hit in rows[:ADJACENT_EXPANSION_TOP_N]:
        document_id = hit.get("document_id")
        page = hit.get("page_number")
        if not document_id or not page:
            continue

        start_page = max(1, int(page) - 1)
        end_page = int(page) + 1

        response = (
            supabase.table("kb_chunks")
            .select("document_id,page_number,section_title,content")
            .eq("document_id", document_id)
            .gte("page_number", start_page)
            .lte("page_number", end_page)
            .order("page_number")
            .execute()
        )

        for neighbor in response.data or []:
            key = (
                neighbor.get("document_id"),
                neighbor.get("page_number"),
                neighbor.get("content") or "",
            )
            if key in seen:
                continue

            expanded.append(
                {
                    **neighbor,
                    "title": hit.get("title"),
                    "publisher": hit.get("publisher"),
                    "publication_date": hit.get("publication_date"),
                    "version": hit.get("version"),
                    "authority_priority": hit.get("authority_priority"),
                    "similarity": None,
                    "semantic_score": None,
                    "lexical_score": None,
                    "hybrid_score": None,
                    "context_type": "adjacent_page_context",
                }
            )
            seen.add(key)

    return expanded


def expand_with_adjacent_pages_local(
    rows: List[Dict[str, Any]],
    all_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Add neighboring pages from the in-memory KB instead of extra Supabase calls."""
    expanded: List[Dict[str, Any]] = []
    seen: set[Tuple[Any, Any, str]] = set()

    def add_row(row: Dict[str, Any], context_type: str) -> None:
        key = (
            row.get("document_id"),
            row.get("page_number"),
            row.get("content") or "",
        )
        if key in seen:
            return
        expanded.append({**row, "context_type": context_type})
        seen.add(key)

    for row in rows:
        add_row(row, "hybrid_hit")

    by_document: Dict[Any, List[Dict[str, Any]]] = {}
    for row in all_rows:
        by_document.setdefault(row.get("document_id"), []).append(row)

    for hit in rows[:ADJACENT_EXPANSION_TOP_N]:
        document_id = hit.get("document_id")
        page = hit.get("page_number")
        if not document_id or not page:
            continue
        try:
            page_number = int(page)
        except Exception:
            continue

        for neighbor in by_document.get(document_id, []):
            try:
                neighbor_page = int(neighbor.get("page_number") or 0)
            except Exception:
                continue
            if page_number - 1 <= neighbor_page <= page_number + 1:
                add_row(neighbor, "adjacent_page_context")

    return expanded


def greek_zone_variant(zone_code: str) -> str:
    """Add a Greek-script search variant for DLS zone codes such as Ka4 -> Κα4."""
    char_map = {
        "a": "α", "b": "β", "g": "γ", "d": "δ", "e": "ε",
        "z": "ζ", "h": "η", "i": "ι", "k": "κ", "l": "λ",
        "m": "μ", "n": "ν", "x": "ξ", "o": "ο", "p": "π",
        "r": "ρ", "s": "σ", "t": "τ", "y": "υ", "u": "υ",
        "f": "φ", "c": "χ", "w": "ω",
    }
    result = []
    for char in zone_code:
        lower = char.casefold()
        greek = char_map.get(lower)
        if greek is None:
            result.append(char)
        elif char.isupper():
            result.append(greek.upper())
        else:
            result.append(greek)
    return "".join(result)


def build_parcel_planning_queries(parcel_details: Dict[str, Any]) -> List[str]:
    parcel = parcel_details.get("parcel") or {}
    zones = parcel_details.get("planning_zones") or []
    zone_codes = [str(z.get("zone")).strip() for z in zones if z.get("zone")]
    zone_terms = []
    for code in zone_codes:
        greek_variant = greek_zone_variant(code)
        zone_terms.append(
            f"{code} / {greek_variant}" if greek_variant != code else code
        )
    zone_text = ", ".join(zone_terms) or "μη καθορισμένη πολεοδομική ζώνη"
    municipality = parcel.get("municipality") or parcel.get("district") or "Κύπρο"

    base = (
        f"Γήπεδο στην {municipality}, πολεοδομική ζώνη {zone_text}. "
        "Ποιες ισχύουσες πολεοδομικές πρόνοιες, ειδικές ρυθμίσεις, εξαιρέσεις "
        "και προϋποθέσεις επηρεάζουν ουσιωδώς την αναπτυξιακή δυνατότητα του τεμαχίου;"
    )
    capacity = (
        f"Πολεοδομική ζώνη {zone_text}: Εντολή 4/2026, συντελεστής δόμησης, ποσοστό κάλυψης, "
        "μέγιστο ύψος και όροφοι, χώροι που δεν προσμετρώνται ή προσμετρώνται μερικώς, "
        "υπόγεια, βοηθητικές οικοδομές, κίνητρα, εξαιρέσεις και ειδικές πρόνοιες."
    )
    practical = (
        f"Πολεοδομική ζώνη {zone_text}: ισχύουσες πρόνοιες μετά την Εντολή 4/2026, "
        "επιτρεπόμενες χρήσεις, οικιστική ανάπτυξη, απαιτήσεις χώρων στάθμευσης, "
        "βασικές αποστάσεις από σύνορα από το εφαρμοστέο Σχέδιο Ανάπτυξης, πρόσβαση και άλλοι κανόνες "
        "που μπορούν να μειώσουν την πρακτικά αξιοποιήσιμη ανάπτυξη ενός τεμαχίου."
    )
    return [base, capacity, practical]


def build_numbered_planning_context(
    rows: List[Dict[str, Any]],
) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    primary = [r for r in rows if r.get("context_type") == "hybrid_hit"]
    adjacent = [r for r in rows if r.get("context_type") == "adjacent_page_context"]
    adjacent.sort(
        key=lambda r: (
            str(r.get("title") or ""),
            int(r.get("page_number") or 0),
        )
    )
    ordered = primary + adjacent

    blocks: List[str] = []
    source_map: Dict[str, Dict[str, Any]] = {}
    total_chars = 0
    for index, row in enumerate(ordered, start=1):
        source_id = f"S{index}"
        title = row.get("title") or "Unknown document"
        page = row.get("page_number") or "?"
        content = (row.get("content") or "").strip()
        block = (
            f"[{source_id}]\n"
            f"Document: {title}\n"
            f"Page: {page}\n"
            f"Publication date: {row.get('publication_date') or 'unknown'}\n"
            f"Section: {row.get('section_title') or 'Μη καθορισμένη'}\n"
            f"Context type: {row.get('context_type') or 'source'}\n"
            f"Text:\n{content}\n"
        )
        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            break
        blocks.append(block)
        total_chars += len(block)
        source_map[source_id] = {
            "source_id": source_id,
            "title": title,
            "page_number": row.get("page_number"),
            "section_title": row.get("section_title"),
            "publication_date": (
                str(row.get("publication_date"))
                if row.get("publication_date")
                else None
            ),
            "version": row.get("version"),
            "publisher": row.get("publisher"),
        }

    return "\n".join(blocks), source_map


def parse_json_object(text: str) -> Dict[str, Any]:
    value = (text or "").strip()
    start = value.find("{")
    end = value.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model did not return a JSON object.")
    parsed = json.loads(value[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Model JSON response must be an object.")
    return parsed


def normalise_planning_analysis(
    raw: Dict[str, Any],
    source_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    valid_confidence = {"high", "medium", "low"}
    provisions = []
    used_source_ids: set[str] = set()

    for item in raw.get("material_provisions") or []:
        if not isinstance(item, dict):
            continue
        source_ids = []
        for source_id in item.get("source_ids") or []:
            source_id = str(source_id).upper().strip()
            if source_id in source_map and source_id not in source_ids:
                source_ids.append(source_id)
                used_source_ids.add(source_id)
        finding = str(item.get("finding") or "").strip()
        if not finding or not source_ids:
            continue
        confidence = str(item.get("confidence") or "medium").lower()
        if confidence not in valid_confidence:
            confidence = "medium"
        provisions.append({
            "category": str(item.get("category") or "other").strip(),
            "title": str(item.get("title") or "Material planning provision").strip(),
            "finding": finding,
            "development_impact": str(item.get("development_impact") or "").strip(),
            "confidence": confidence,
            "source_ids": source_ids,
            "source_refs": [source_map[sid] for sid in source_ids],
        })
        if len(provisions) >= 6:
            break

    model_confidence = str(raw.get("confidence") or "medium").lower()
    if model_confidence not in valid_confidence:
        model_confidence = "medium"

    if not provisions:
        confidence = "low"
    elif model_confidence == "high" and len(used_source_ids) < 3:
        confidence = "medium"
    else:
        confidence = model_confidence

    def clean_strings(values: Any, limit: int) -> List[str]:
        result = []
        for value in values or []:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    return {
        "summary": str(raw.get("summary") or "").strip(),
        "confidence": confidence,
        "material_provisions": provisions,
        "capacity_caveats": clean_strings(raw.get("capacity_caveats"), 5),
        "checks_before_reliance": clean_strings(raw.get("checks_before_reliance"), 5),
        "sources": [
            source_map[sid]
            for sid in sorted(used_source_ids, key=lambda x: int(x[1:]))
        ],
    }


def retrieve_parcel_planning_context(
    parcel_details: Dict[str, Any],
    openai_client: OpenAI,
    supabase: Any,
    all_rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int], str]:
    queries = build_parcel_planning_queries(parcel_details)
    primary_question = queries[0]

    semantic_rows = batch_semantic_candidates(queries, openai_client, supabase)
    lexical_rows = merge_unique_rows([
        lexical_candidates(query, all_rows) for query in queries
    ])
    direct_rows = merge_unique_rows([
        direct_rule_candidates(query, all_rows) for query in queries
    ])
    hybrid_rows = merge_and_rerank(semantic_rows, lexical_rows, direct_rows)
    reranked_rows = llm_rerank_candidates(
        primary_question,
        hybrid_rows,
        openai_client,
    )
    context_rows = expand_with_adjacent_pages_local(reranked_rows, all_rows)

    metrics = {
        "semantic_candidates": len(semantic_rows),
        "lexical_candidates": len(lexical_rows),
        "direct_rule_candidates": len(direct_rows),
        "hybrid_candidates": len(hybrid_rows),
        "reranked_hits": len(reranked_rows),
        "context_chunks": len(context_rows),
    }
    return context_rows, metrics, primary_question


def generate_parcel_planning_analysis(
    parcel_details: Dict[str, Any],
    openai_client: OpenAI,
    supabase: Any,
    all_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    started = time.perf_counter()
    if not parcel_details.get("planning_zones"):
        return {
            "status": "insufficient_parcel_context",
            "summary": "No planning-zone data was returned for this parcel, so PLANA.CY did not infer parcel-specific planning provisions.",
            "confidence": "low",
            "material_provisions": [],
            "capacity_caveats": [],
            "checks_before_reliance": ["Confirm the applicable planning zone before relying on a parcel-specific planning analysis."],
            "sources": [],
            "retrieval": {},
            "analysis_engine_version": "planning-auto-v2-rules-4-2026",
            "model_passes": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    context_rows, retrieval_metrics, primary_question = retrieve_parcel_planning_context(
        parcel_details,
        openai_client,
        supabase,
        all_rows,
    )

    if not context_rows:
        return {
            "status": "insufficient_sources",
            "summary": (
                "The planning knowledge base did not return enough directly relevant "
                "material for an automatic parcel analysis."
            ),
            "confidence": "low",
            "material_provisions": [],
            "capacity_caveats": [],
            "checks_before_reliance": [
                "Review the applicable planning documents or ask PLANA.CY a narrower planning question."
            ],
            "sources": [],
            "retrieval": retrieval_metrics,
            "analysis_engine_version": "planning-auto-v2-rules-4-2026",
            "model_passes": 1 if retrieval_metrics.get("hybrid_candidates") else 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    context, source_map = build_numbered_planning_context(context_rows)
    structured_rules = parcel_details.get("structured_rule_analysis") or evaluate_parcel_rules(parcel_details)
    parcel_context = {
        "parcel": parcel_details.get("parcel"),
        "planning_zones": parcel_details.get("planning_zones"),
        "development_potential": parcel_details.get("development_potential"),
        "structured_rule_analysis": compact_rule_context(structured_rules),
        "warnings": parcel_details.get("warnings"),
    }

    instructions = """
You are PLANA.CY's automatic Cyprus parcel-planning intelligence analyst.

Return ONLY valid JSON. Do not use markdown or prose outside the JSON object.

Your job is not to restate the DLS zone coefficients or re-derive rules already encoded in STRUCTURED_RULE_ANALYSIS.
Identify only additional parcel-specific planning provisions, exceptions, use restrictions, special policies, or applicability issues that could materially change, qualify, reduce, condition, or require confirmation before relying on the theoretical development capacity.

Source precedence and evidence rules:
1. The structured rule layer is the deterministic baseline for topics it covers. It uses Ministerial Order 4/2026 from 11 May 2026.
2. Order 4/2026 supersedes Order 4/2024 for its covered calculation/setback topics. Do not revive a 4/2024 rule when the structured layer or a 4/2026 excerpt states the current rule.
3. The March 2026 ETEK guide is interpretive context and predates the effective date of Order 4/2026; it must not override Order 4/2026.
4. Use only the supplied source excerpts for any additional planning/legal claims.
5. Never assume a general rule applies to the parcel merely because it is common in Cyprus.
6. A provision is a material_provision only when the excerpts directly support the rule and its relevance to the stated zone/use context.
7. If a source is relevant but parcel applicability depends on missing facts, put the issue in checks_before_reliance instead of claiming it applies.
8. Keep general rules, exceptions, discretionary powers, and special cases separate.
9. Do not combine conditions from separate provisions.
10. Prefer newer directly applicable material, but retain an older directly stated rule when newer material does not replace it.
11. Each material_provision must cite one or more exact source IDs such as S1 or S4.
12. Do not cite a source ID that does not support the finding.
13. Maximum 6 material provisions. Include fewer when the evidence is weak.
14. Write the user-facing text in English. Keep Greek document titles only in source metadata; do not insert citation text inside finding fields.
15. confidence means confidence in the evidence coverage of this automated analysis, not legal certainty.

Return this exact shape:
{
  "summary": "2-4 sentence evidence-grounded summary",
  "confidence": "high|medium|low",
  "material_provisions": [
    {
      "category": "parking|density calculation|coverage|height/floors|use|setbacks|special provision|other",
      "title": "short title",
      "finding": "what the retrieved rules establish",
      "development_impact": "why it matters to practical development capacity",
      "confidence": "high|medium|low",
      "source_ids": ["S1"]
    }
  ],
  "capacity_caveats": ["specific caveat to the theoretical capacity"],
  "checks_before_reliance": ["specific missing fact or applicability check"]
}
"""

    prompt = f"""
AUTOMATIC INVESTIGATION QUESTION:
{primary_question}

TRUSTED DLS / PLATFORM PARCEL CONTEXT:
{json.dumps(parcel_context, ensure_ascii=False, indent=2)}

RETRIEVED PLANNING SOURCE EXCERPTS:
{context}

Return the structured parcel-planning analysis JSON only.
"""

    response = openai_client.responses.create(
        model=PLANNING_ANALYSIS_MODEL,
        instructions=instructions.strip(),
        input=prompt.strip(),
    )
    raw = parse_json_object(response.output_text)

    verifier_instructions = """
You are the final evidence verifier for PLANA.CY automatic parcel-planning intelligence.
Return ONLY valid JSON in exactly the same shape as the draft JSON.

Check every material_provision against the supplied source excerpts and the source_ids it cites.
- Treat STRUCTURED_RULE_ANALYSIS as the current deterministic baseline for topics it covers.
- Order 4/2026 supersedes Order 4/2024 for its covered calculation/setback topics from 11 May 2026.
- The March 2026 ETEK guide is interpretive and must not override Order 4/2026.
- Remove a provision if the cited excerpts do not directly support the finding.
- Correct source_ids when another supplied excerpt directly supports the finding.
- Do not infer that a rule applies to the parcel when applicability depends on a missing use, development type, location category, threshold, or discretionary decision.
- Move such unresolved applicability issues into checks_before_reliance.
- Do not combine separate provisions into cumulative conditions.
- Keep general rules, exceptions, discretionary powers, and special cases separate.
- Do not restate the DLS zone coefficients as a material provision unless a source qualifies or changes how they can be relied upon.
- Keep no more than 6 material provisions.
- Each retained material_provision must have at least one exact valid source ID.
- Write user-facing text in English.
- confidence is evidence-coverage confidence, not legal certainty.
Do not add facts that are absent from the source excerpts.
"""
    verifier_prompt = f"""
TRUSTED DLS / PLATFORM PARCEL CONTEXT:
{json.dumps(parcel_context, ensure_ascii=False, indent=2)}

SOURCE EXCERPTS:
{context}

DRAFT STRUCTURED ANALYSIS:
{json.dumps(raw, ensure_ascii=False, indent=2)}

Return the corrected structured analysis JSON only.
"""
    verified_response = openai_client.responses.create(
        model=VERIFY_MODEL,
        instructions=verifier_instructions.strip(),
        input=verifier_prompt.strip(),
    )
    verified_raw = parse_json_object(verified_response.output_text)
    result = normalise_planning_analysis(verified_raw, source_map)
    result.update({
        "status": "complete" if result["material_provisions"] else "limited",
        "retrieval": retrieval_metrics,
        "analysis_engine_version": "planning-auto-v2-rules-4-2026",
        "model_passes": 3,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    })
    return result


def build_context(rows: List[Dict[str, Any]]) -> str:
    primary = [r for r in rows if r.get("context_type") == "hybrid_hit"]
    adjacent = [r for r in rows if r.get("context_type") == "adjacent_page_context"]

    adjacent.sort(
        key=lambda r: (
            str(r.get("title") or ""),
            int(r.get("page_number") or 0),
        )
    )

    ordered = primary + adjacent

    blocks = []
    total_chars = 0

    for i, row in enumerate(ordered, start=1):
        title = row.get("title") or "Unknown document"
        page = row.get("page_number") or "?"
        pub_date = row.get("publication_date") or "unknown"
        priority = row.get("authority_priority") or 0
        context_type = row.get("context_type") or "source"
        section_title = row.get("section_title") or "Μη καθορισμένη"
        content = (row.get("content") or "").strip()

        block = (
            f"[SOURCE {i}]\n"
            f"Document: {title}\n"
            f"Page: {page}\n"
            f"Publication date: {pub_date}\n"
            f"Internal source priority: {priority}\n"
            f"Context type: {context_type}\n"
            f"Section: {section_title}\n"
            f"Text:\n{content}\n"
        )

        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            break

        blocks.append(block)
        total_chars += len(block)

    return "\n".join(blocks)



def output_language_for_question(question: str) -> str:
    return "Greek" if contains_greek(question) else "English"


def answer_body_language_mismatch(text: str, target_language: str) -> bool:
    """
    Ignore bracketed citations because Greek document titles may legitimately
    appear inside an otherwise English answer.
    """
    body = re.sub(r"\[[^\]]+\]", " ", text or "")
    greek_letters = len(re.findall(r"[Α-ΩΆΈΉΊΌΎΏα-ωάέήίϊΐόύϋΰώ]", body))
    latin_letters = len(re.findall(r"[A-Za-z]", body))

    if target_language == "English":
        return greek_letters > max(30, latin_letters * 0.35)

    return latin_letters > max(60, greek_letters * 0.80)


def answer_question(
    question: str,
    rows: List[Dict[str, Any]],
    openai_client: OpenAI,
) -> str:
    if not rows:
        return (
            "Δεν βρέθηκαν επαρκώς σχετικά αποσπάσματα στη βάση γνώσης. "
            "Δοκίμασε να διατυπώσεις διαφορετικά την ερώτηση."
        )

    context = build_context(rows)
    target_language = output_language_for_question(question)
    required_note = (
        "Σημείωση: Η απάντηση βασίζεται στα διαθέσιμα έγγραφα της βάσης γνώσης και δεν υποκαθιστά επίσημη νομική ή πολεοδομική γνωμάτευση."
        if target_language == "Greek"
        else "Note: This answer is based on the available documents in the knowledge base and does not replace official legal or planning advice."
    )

    instructions = f"""
You are a Cyprus planning-regulations research assistant for architects.

OUTPUT LANGUAGE: {target_language}
The source excerpts may be in Greek. Ignore the source language when choosing the output language.
You must write the answer in {target_language} because that is the language of the user's question.

You must answer ONLY from the supplied retrieved source excerpts.

SOURCE PRECEDENCE:
- First identify the newest directly applicable source.
- For calculation of building coefficient, coverage, floors/height and boundary-distance topics covered by Ministerial Order 4/2026, treat Order 4/2026 as current from 11 May 2026 and Order 4/2024 as superseded.
- The March 2026 ETEK concise guide is interpretive context, is expressly non-exhaustive and states a scope limited to the four major-city Local Plans; it must not override the later-effective Order 4/2026.
- When newer and older sources differ, do not silently follow the older source.
- Use an older source only when it is consistent with newer material or when no newer applicable material is available.
- The internal source-priority number is only a retrieval hint, not a legal hierarchy.

LEGAL-READING RULES:
1. Never invent a regulation, number, exception, interpretation, or citation.
2. Never give a universal "yes" or "no" when the excerpts show that the answer depends on use, conditions, exceptions, discretion, or a category of space.
3. Before answering any yes/no question, explicitly check the supplied excerpts for:
   - exceptions
   - exclusions
   - partial counting
   - conditions
   - distinctions by use
   - newer rules that qualify older guidance
4. If the correct answer is conditional, start with "Εξαρτάται" in Greek or "It depends" in English.
5. Read neighboring page excerpts as continuous context across page breaks.
6. Resolve pronouns from preceding context before stating what an exception applies to.
7. Never generalize an exception from a specific object or use to a broader category.
8. NEVER combine conditions from separate provisions into one cumulative condition unless the source explicitly says they all apply together.
9. Treat the following as separate legal categories unless the source explicitly joins them:
   - general rule
   - definition
   - mandatory conditions
   - exception
   - special fire-safety provision
   - discretionary power of the Competent Authority
10. A special fire-safety rule must never be presented as a condition of the ordinary/general rule unless the source explicitly says so.
11. If the excerpts are insufficient or ambiguous, say so clearly.
12. Distinguish the general rule from exceptions and discretionary powers.
13. Answer in the same language as the user's question.
14. Be concise but practically useful to an architect.
15. Cite factual claims inline using:
    [Document title, p. X]
    or [Document title, pp. X–Y]
16. Every citation must include the FULL document title. Never shorten a citation to [p. X], [pp. X–Y], [σ. X], or [σσ. X–Y].
17. Do not cite SOURCE numbers.
18. End with exactly this note:
    {required_note}
"""

    prompt = f"""
USER QUESTION:
{question}

HYBRID-RETRIEVED SOURCE EXCERPTS:
{context}

Before drafting the answer, internally build a small legal rule map:
- GENERAL RULE
- DEFINITIONS
- MANDATORY CONDITIONS
- EXCEPTIONS
- SPECIAL CASES
- DISCRETIONARY POWERS
- SOURCE FOR EACH PROPOSITION

Do not show this internal map to the user.

Then:
- Identify the newest directly applicable source.
- Check exact keyword matches as well as semantic context.
- Check whether the answer has exceptions or depends on the type/use of space.
- Check whether any older source is qualified by newer material.
- Do not turn separate exceptions or special cases into extra conditions of the general rule.
- Do not join two source statements with "and", "provided that", or equivalent wording unless the source itself makes them cumulative.
- ALWAYS state the most directly applicable general rule first when the sources provide one.
- Do not replace an explicit general rule with a broad opening such as "it depends" or "there is no single rule".
- Put exceptions, limitations, unusual scenarios, and special zones after the general rule.
- If the question is broad, answer the ordinary/common case first, then explain when a different rule may apply.

Then write only the draft evidence-grounded answer.
"""

    response = openai_client.responses.create(
        model=ANSWER_MODEL,
        instructions=instructions.strip(),
        input=prompt.strip(),
    )

    draft_answer = response.output_text.strip()

    verifier_instructions = f"""
You are the final legal-consistency verifier for a Cyprus planning-regulations assistant.

You receive:
1. the user's question,
2. the exact retrieved source excerpts,
3. a draft answer.

OUTPUT LANGUAGE: {target_language}
Your job is to return a corrected final answer in {target_language}.
The source excerpts may be Greek. Do NOT switch to Greek merely because the source material is Greek.

Check especially for SOURCE PRECEDENCE AND SYNTHESIS ERRORS:
- For topics covered by Ministerial Order 4/2026, did the draft revive a superseded Order 4/2024 rule or allow the March 2026 ETEK guide to override Order 4/2026?
- Did the draft combine separate provisions into one cumulative condition?
- Did it turn an exception into a condition of the general rule?
- Did it turn a special fire-safety provision into a general requirement?
- Did it generalize a discretionary power?
- Did it merge facts from different source passages using "and", "provided that", or similar wording when the sources do not make them cumulative?
- Did it state something stronger than the excerpts support?
- Are general rule, conditions, exceptions, special cases, and discretionary powers clearly separated?
- If the sources contain a directly applicable general rule, is it stated first?
- Did the draft incorrectly open with "it depends" or "there is no single rule" even though a general rule is available?
- Are citations attached to the claims they actually support?

Rules:
1. Correct any such error.
2. Preserve useful, accurate content.
3. Do not add facts that are not in the excerpts.
4. For Order 4/2026 covered topics, prefer Order 4/2026 from 11 May 2026 over superseded Order 4/2024; use the March 2026 ETEK guide as interpretive context, not as an override.
5. Write the prose in {target_language}.
6. Every citation must include the FULL document title, for example:
   [Document title, p. X] or [Document title, pp. X–Y]
   Never shorten citations to [p. X], [pp. X–Y], [σ. X], or [σσ. X–Y].
7. Preserve Greek document titles inside citations even when the answer is in English.
8. Do not mention that you reviewed or corrected a draft.
9. End with exactly this note:
   {required_note}
10. Return ONLY the final answer to the user.
"""

    verifier_prompt = f"""
TARGET OUTPUT LANGUAGE:
{target_language}

USER QUESTION:
{question}

SOURCE EXCERPTS:
{context}

DRAFT ANSWER:
{draft_answer}

Return the corrected final answer only.
"""

    verified = openai_client.responses.create(
        model=VERIFY_MODEL,
        instructions=verifier_instructions.strip(),
        input=verifier_prompt.strip(),
    )

    final_answer = verified.output_text.strip()

    # Deterministic safeguard: if the verifier still switches language because
    # the source excerpts are Greek, rewrite only the prose language while
    # preserving meaning and full citations.
    if answer_body_language_mismatch(final_answer, target_language):
        language_fix_instructions = f"""
Rewrite the supplied answer in {target_language}.

Rules:
1. Preserve the legal meaning exactly.
2. Do not add or remove substantive claims.
3. Preserve every citation and its full Greek document title.
4. Every citation must remain in the form [Document title, p. X] or [Document title, pp. X–Y].
5. Never use bare citations such as [p. X], [σ. X], or [σσ. X–Y].
6. End with exactly this note:
   {required_note}
7. Return ONLY the rewritten final answer.
"""
        language_fixed = openai_client.responses.create(
            model=VERIFY_MODEL,
            instructions=language_fix_instructions.strip(),
            input=final_answer,
        )
        final_answer = language_fixed.output_text.strip()

    return final_answer


def main() -> None:
    load_dotenv()

    supabase_url = require_env("SUPABASE_URL")
    supabase_secret_key = require_env("SUPABASE_SECRET_KEY")
    openai_api_key = require_env("OPENAI_API_KEY")

    supabase = create_client(supabase_url, supabase_secret_key)
    openai_client = OpenAI(api_key=openai_api_key)

    print(f"PLANA.CY v11 — model: {ANSWER_MODEL}")
    print("Bilingual retrieval + hybrid search + legal verification + output-language guard are ON.")
    print("General-rule-first answers + condition/exception separation + full citations are ON.")
    print("Type 'exit' to quit.\n")

    # Only 424 chunks currently, so loading all rows for local lexical scoring is cheap.
    all_rows = fetch_all_chunks_with_metadata(supabase)
    print(f"Loaded {len(all_rows)} knowledge-base chunks for lexical search.\n")

    while True:
        question = input("Ask a planning question:\n> ").strip()

        if not question:
            continue

        if question.lower() in {"exit", "quit"}:
            break

        try:
            greek_search_query = generate_greek_search_query(question, openai_client)

            if greek_search_query != question:
                print(f"Greek retrieval query: {greek_search_query}")

            semantic_rows = merge_unique_rows([
                semantic_candidates(question, openai_client, supabase),
                semantic_candidates(greek_search_query, openai_client, supabase),
            ])

            lexical_rows = merge_unique_rows([
                lexical_candidates(question, all_rows),
                lexical_candidates(greek_search_query, all_rows),
            ])

            direct_rows = merge_unique_rows([
                direct_rule_candidates(question, all_rows),
                direct_rule_candidates(greek_search_query, all_rows),
            ])

            hybrid_rows = merge_and_rerank(semantic_rows, lexical_rows, direct_rows)
            reranked_rows = llm_rerank_candidates(question, hybrid_rows, openai_client)
            context_rows = expand_with_adjacent_pages(reranked_rows, supabase)

            print(
                f"\nSemantic candidates: {len(semantic_rows)} | "
                f"Lexical candidates: {len(lexical_rows)} | "
                f"Direct-rule candidates: {len(direct_rows)} | "
                f"Hybrid pool: {len(hybrid_rows)} | "
                f"LLM-reranked hits: {len(reranked_rows)} | "
                f"Context chunks: {len(context_rows)}"
            )
            print("Generating answer...\n")

            answer = answer_question(question, context_rows, openai_client)
            print(answer)

            print("\nTop LLM-reranked retrieval hits:")
            for i, row in enumerate(reranked_rows[:10], start=1):
                print(
                    f"{i}. {row.get('title')} — p. {row.get('page_number')} "
                    f"(semantic {float(row.get('semantic_score') or 0.0):.3f}, "
                    f"lexical {float(row.get('lexical_score') or 0.0):.3f}, "
                    f"direct {float(row.get('direct_score') or 0.0):.3f}, "
                    f"hybrid {float(row.get('hybrid_score') or 0.0):.3f})"
                )

            print("\n" + "=" * 90 + "\n")

        except Exception as exc:
            print(f"\nERROR: {exc}\n")
            print(
                "If this is a model-access error, set OPENAI_CHAT_MODEL in .env "
                "to a model available to your API project."
            )
            print()

# =========================
# WEB APP / API LAYER
# =========================

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


state: dict[str, Any] = {}


def unique_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int | None]] = set()
    sources: list[dict[str, Any]] = []

    for row in rows:
        title = row.get("title") or "Unknown document"
        page_number = row.get("page_number")
        key = (title, page_number)

        if key in seen:
            continue

        seen.add(key)
        sources.append(
            {
                "title": title,
                "page_number": page_number,
                "section_title": row.get("section_title"),
                "publication_date": (
                    str(row.get("publication_date"))
                    if row.get("publication_date")
                    else None
                ),
                "version": row.get("version"),
                "publisher": row.get("publisher"),
            }
        )

    return sources


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()

    supabase_url = require_env("SUPABASE_URL")
    supabase_secret_key = require_env("SUPABASE_SECRET_KEY")
    openai_api_key = require_env("OPENAI_API_KEY")

    state["supabase"] = create_client(supabase_url, supabase_secret_key)
    state["openai"] = OpenAI(api_key=openai_api_key)
    state["all_rows"] = fetch_all_chunks_with_metadata(state["supabase"])

    print(
        f"PLANA.CY ready — loaded "
        f"{len(state['all_rows'])} knowledge-base chunks."
    )

    yield
    state.clear()


app = FastAPI(
    title="PLANA.CY",
    version="1.2.1",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, Any]:
    rows = state.get("all_rows", [])
    normalized_titles = [normalize_text(row.get("title") or "") for row in rows]
    return {
        "status": "ok",
        "chunks_loaded": len(rows),
        "model": ANSWER_MODEL,
        "rule_engine_version": RULE_ENGINE_VERSION,
        "structured_rule_count": len(RULE_CATALOG),
        "structured_rule_sources_embedded": {
            "order_4_2026": True,
            "etek_march_2026_guide": True,
        },
        "knowledge_base_rule_sources": {
            "order_4_2026_detected": any("4/2026" in title for title in normalized_titles),
            "etek_march_2026_detected": any("συνοπτικ" in title and "πολεοδομ" in title for title in normalized_titles),
        },
    }


@app.post("/api/chat")
def chat(payload: ChatRequest) -> dict[str, Any]:
    question = payload.question.strip()

    supabase = state.get("supabase")
    openai_client = state.get("openai")
    all_rows = state.get("all_rows")

    if not supabase or not openai_client or all_rows is None:
        raise HTTPException(status_code=503, detail="Service is not ready yet.")

    try:
        greek_search_query = generate_greek_search_query(question, openai_client)

        semantic_rows = merge_unique_rows([
            semantic_candidates(question, openai_client, supabase),
            semantic_candidates(greek_search_query, openai_client, supabase),
        ])

        lexical_rows = merge_unique_rows([
            lexical_candidates(question, all_rows),
            lexical_candidates(greek_search_query, all_rows),
        ])

        direct_rows = merge_unique_rows([
            direct_rule_candidates(question, all_rows),
            direct_rule_candidates(greek_search_query, all_rows),
        ])

        hybrid_rows = merge_and_rerank(
            semantic_rows,
            lexical_rows,
            direct_rows,
        )

        reranked_rows = llm_rerank_candidates(
            question,
            hybrid_rows,
            openai_client,
        )

        context_rows = expand_with_adjacent_pages(
            reranked_rows,
            supabase,
        )

        answer = answer_question(
            question,
            context_rows,
            openai_client,
        )

        return {
            "question": question,
            "answer": answer,
            "language": output_language_for_question(question),
            "greek_search_query": greek_search_query,
            "sources": unique_sources(reranked_rows),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"PLANA.CY request failed: {exc}",
        ) from exc




# ==================== DLS SITE EXPLORER ====================
DLS_MAPSERVER = "https://eservices.dls.moi.gov.cy/arcgis/rest/services/National/CadastralMap_EN/MapServer"
PARCEL_QUERY = f"{DLS_MAPSERVER}/0/query"
GENERAL_IDENTIFY = "https://eservices.dls.moi.gov.cy/Services/Rest/Info/GeneralParcelIdentify"
NOMINATIM = "https://nominatim.openstreetmap.org/search"


GEOCODE_CACHE: dict[str, list[dict[str, Any]]] = {}
SITE_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
SITE_CACHE_TTL_SECONDS = 900
PARCEL_PLANNING_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
PARCEL_PLANNING_CACHE_TTL_SECONDS = 3600

# Confirmed / observed DLS map layers from the official viewer.
SPECIAL_LAYERS = {
    28: "Buildings",
    30: "Contour Lines 1993",
    31: "Coast Protection Zone",
    32: "State Land",
    36: "Surveyed Parcels",
}




@app.get("/api/geocode")
async def geocode(q: str = Query(min_length=3, max_length=200)):
    key = q.strip().casefold()
    if key in GEOCODE_CACHE:
        return {"results": GEOCODE_CACHE[key]}

    params = {
        "q": f"{q.strip()}, Cyprus",
        "format": "jsonv2",
        "limit": 5,
        "countrycodes": "cy",
    }
    headers = {
        "User-Agent": "PLANA.CY/1.0",
        "Accept-Language": "en,el;q=0.8",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(NOMINATIM, params=params, headers=headers)

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Address search failed.")

    results = [
        {
            "display_name": x.get("display_name"),
            "lat": float(x["lat"]),
            "lon": float(x["lon"]),
        }
        for x in r.json()
        if x.get("lat") and x.get("lon")
    ]
    GEOCODE_CACHE[key] = results
    return {"results": results}


async def get_parcel_at_point(lat: float, lon: float):
    params = {
        "f": "geojson",
        "where": "1=1",
        "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "resultRecordCount": 5,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(PARCEL_QUERY, params=params)

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="DLS parcel query failed.")

    data = r.json()
    features = data.get("features", [])
    if not features:
        raise HTTPException(status_code=404, detail="No DLS parcel found at that point.")
    return features[0]


async def get_general_identify(subproperty_id: int):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://eservices.dls.moi.gov.cy/",
        "User-Agent": "Mozilla/5.0 PLANA.CY/1.0",
    }

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        r = await client.get(
            GENERAL_IDENTIFY,
            params={"subPropertyId": subproperty_id},
            headers=headers,
        )

    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"DLS GeneralParcelIdentify failed ({r.status_code}).",
        )

    try:
        return r.json()
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="DLS GeneralParcelIdentify returned invalid JSON.",
        )


def clean_text(v):
    return v.strip() if isinstance(v, str) else v


def as_percent(v):
    if v in (None, ""):
        return None
    try:
        x = float(v)
        return round(x * 100, 2) if abs(x) <= 5 else round(x, 2)
    except Exception:
        return v


def pick_parcel_record(records, parcel_id):
    for x in records:
        if x.get("PrParcelId") == parcel_id and x.get("PropertyTypeName") == "Parcel":
            return x
    for x in records:
        if x.get("PropertyTypeName") == "Parcel":
            return x
    return records[0] if records else None


def parse_zone(z, link=None):
    if not z:
        return None

    affected = link.get("PrAffectedExtent") if link else None
    total = link.get("PrTotalExtent") if link else None
    overlap = None
    try:
        if affected is not None and total not in (None, 0):
            overlap = round(float(affected) / float(total) * 100, 2)
    except Exception:
        pass

    return {
        "zone": clean_text(z.get("PrName")),
        "density_percent": as_percent(z.get("PrDensityRateQty")),
        "coverage_percent": as_percent(z.get("PrCoverageRate")),
        "max_floors": z.get("PrStoreyNoQty"),
        "max_height_m": z.get("PrHeightMSR"),
        "remarks": clean_text(z.get("PrRemarkDesc")),
        "description_en": clean_text(z.get("PrNameEn")),
        "description_gr": clean_text(z.get("PrNameGr")),
        "affected_extent": affected,
        "total_extent": total,
        "overlap_percent": overlap,
    }


def haversine_m(lon1, lat1, lon2, lat2):
    r = 6371008.8
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def polygon_geometry_metrics(feature):
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []

    if geom.get("type") != "Polygon" or not coords:
        return {}

    outer = max(coords, key=len)
    if len(outer) < 2:
        return {}

    edge_lengths = []
    perimeter = 0.0
    for a, b in zip(outer, outer[1:]):
        d = haversine_m(a[0], a[1], b[0], b[1])
        edge_lengths.append(d)
        perimeter += d

    lons = [p[0] for p in outer]
    lats = [p[1] for p in outer]

    longest = max(edge_lengths) if edge_lengths else None
    shortest = min(edge_lengths) if edge_lengths else None

    orientation_deg = None
    orientation_label = None
    if edge_lengths:
        idx = edge_lengths.index(longest)
        a = outer[idx]
        b = outer[idx + 1]
        y = math.sin(math.radians(b[0] - a[0])) * math.cos(math.radians(b[1]))
        x = (
            math.cos(math.radians(a[1])) * math.sin(math.radians(b[1]))
            - math.sin(math.radians(a[1]))
            * math.cos(math.radians(b[1]))
            * math.cos(math.radians(b[0] - a[0]))
        )
        bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        orientation_deg = round(bearing, 1)
        orientation_label = dirs[int((bearing + 22.5) // 45) % 8]

    return {
        "approx_perimeter_m": round(perimeter, 2),
        "longest_edge_m": round(longest, 2) if longest is not None else None,
        "shortest_edge_m": round(shortest, 2) if shortest is not None else None,
        "centroid_lat": round(sum(lats) / len(lats), 7),
        "centroid_lon": round(sum(lons) / len(lons), 7),
        "longest_edge_orientation_deg": orientation_deg,
        "longest_edge_orientation": orientation_label,
    }


def geojson_to_esri_polygon(feature):
    geom = feature.get("geometry") or {}
    if geom.get("type") != "Polygon":
        return None
    return {
        "rings": geom.get("coordinates") or [],
        "spatialReference": {"wkid": 4326},
    }


async def query_layer_intersections(layer_id: int, parcel_feature: dict):
    esri_geom = geojson_to_esri_polygon(parcel_feature)
    if not esri_geom:
        return {"ok": False, "error": "Unsupported parcel geometry"}

    url = f"{DLS_MAPSERVER}/{layer_id}/query"
    params = {
        "f": "json",
        "where": "1=1",
        "geometry": json.dumps(esri_geom),
        "geometryType": "esriGeometryPolygon",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "resultRecordCount": 1000,
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url, params=params)

        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}

        data = r.json()
        if "error" in data:
            return {"ok": False, "error": data["error"]}

        return {
            "ok": True,
            "features": data.get("features", []),
            "exceeded_transfer_limit": data.get("exceededTransferLimit", False),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}



@app.get("/api/parcel-basic")
async def parcel_basic(
    lat: float = Query(ge=34.0, le=36.0),
    lon: float = Query(ge=31.0, le=35.0),
) -> dict[str, Any]:
    parcel_feature = await get_parcel_at_point(lat, lon)
    props = parcel_feature.get("properties", {})
    sbpi = props.get("SBPI_ID_NO")
    if sbpi is None:
        raise HTTPException(status_code=502, detail="DLS parcel did not return SBPI_ID_NO.")
    try:
        sbpi = int(sbpi)
    except Exception:
        raise HTTPException(status_code=502, detail=f"Unexpected SBPI_ID_NO: {sbpi}")

    return {
        "parcel_feature": parcel_feature,
        "parcel_id": sbpi,
        "parcel_number": props.get("PARCEL_NBR") or props.get("Parcel Number"),
        "sheet": props.get("SHEET") or props.get("Sheet"),
        "plan": props.get("PLAN_NBR") or props.get("Plan"),
        "block": props.get("BLCK_CODE") or props.get("Block Code"),
        "map_geometry_extent_m2": props.get("Parcel Extend") or props.get("SHAPE.STArea()"),
        "geometry_metrics": polygon_geometry_metrics(parcel_feature),
        "map_layer_attributes": props,
    }


def safe_sum(values: list[Any]) -> float | None:
    nums = []
    for value in values:
        try:
            if value not in (None, ""):
                nums.append(float(value))
        except Exception:
            pass
    return round(sum(nums), 2) if nums else None


def normalize_parcel_details(
    records: list[dict[str, Any]],
    parcel_id: int,
) -> dict[str, Any]:
    """Convert DLS GeneralParcelIdentify records into PLANA's canonical parcel payload."""
    parcel = pick_parcel_record(records, parcel_id)
    if not parcel:
        raise HTTPException(status_code=502, detail="Main parcel record could not be identified.")

    zones = []
    for link in parcel.get("ParcelPlanZones") or []:
        parsed = parse_zone(link.get("PrPlanningZone"), link)
        if parsed:
            zones.append(parsed)
    if not zones:
        parsed = parse_zone(parcel.get("PrPlanningZone"))
        if parsed:
            zones.append(parsed)

    related = []
    type_counter = Counter()
    enclosed_vals, covered_vals, uncovered_vals = [], [], []

    for rec in records:
        if rec is parcel:
            continue
        subitems = rec.get("PrPropertySubproperty") or []
        sub = subitems[0] if subitems else {}

        kind = clean_text(rec.get("SubPropertyKindName"))
        prop_type = clean_text(rec.get("PropertyTypeName"))
        type_counter[kind or prop_type or "Other"] += 1

        enclosed = sub.get("PrEnclosedExtent")
        covered = sub.get("PrCoveredExtent")
        uncovered = sub.get("PrUncoveredExtent")
        enclosed_vals.append(enclosed)
        covered_vals.append(covered)
        uncovered_vals.append(uncovered)

        related.append({
            "property_type": prop_type,
            "kind": kind,
            "registration_block": rec.get("PrRegistrationBlock"),
            "registration_no": clean_text(rec.get("PrRegistrationNo")),
            "price_2021": rec.get("PrPriceBase2"),
            "price_2018": rec.get("PrPriceBase1"),
            "price_1980": rec.get("PrPriceBase3"),
            "unit_floor_no": sub.get("UnitFloorNo"),
            "plan_no": clean_text(sub.get("PlanNo")),
            "enclosed_extent": enclosed,
            "covered_extent": covered,
            "uncovered_extent": uncovered,
            "is_legal": sub.get("PrIsLegal"),
        })

    parcel_area = parcel.get("PrParcelExtent")
    capacity_result = calculate_zoned_capacity(parcel_area, zones)
    max_floor_area = capacity_result.get("theoretical_max_floor_area_m2")
    max_ground_coverage = capacity_result.get("theoretical_max_ground_coverage_m2")

    value_2021 = parcel.get("PrPriceBase2")
    value_2018 = parcel.get("PrPriceBase1")
    valuation_change_percent = None
    try:
        if value_2021 is not None and value_2018 not in (None, 0):
            valuation_change_percent = round(
                (float(value_2021) - float(value_2018)) / float(value_2018) * 100,
                2,
            )
    except Exception:
        pass

    warnings = []
    if len(zones) > 1:
        warnings.append("Parcel is affected by multiple planning zones.")
    if any(zone.get("remarks") for zone in zones):
        warnings.append("One or more planning-zone remarks apply.")
    if related:
        warnings.append(f"Parcel has {len(related)} related registered properties or units.")
    if bool(parcel.get("PrIsPreserved")):
        warnings.append("Property is marked as preserved.")
    if bool(parcel.get("PrIsAncient")):
        warnings.append("Property is marked as ancient.")
    if bool(parcel.get("PrIsCommonProperty")):
        warnings.append("Property is marked as common property.")

    parcel_summary = {
        "parcel_id": parcel.get("PrParcelId") or parcel_id,
        "parcel_number": clean_text(parcel.get("PrParcelNo")),
        "registration_number": clean_text(parcel.get("PrRegistrationNo")),
        "district": clean_text(parcel.get("PrDistrictNameEn") or parcel.get("DistrictName")),
        "municipality": clean_text(parcel.get("PrMunicipalityNameEn") or parcel.get("MunicipalityName")),
        "quarter": clean_text(parcel.get("PrQuarterNameEn") or parcel.get("QuarterName")),
        "sheet": clean_text(parcel.get("PrSheetValue")),
        "plan": clean_text(parcel.get("PrPlanValue")),
        "block": clean_text(parcel.get("PrBlockValue")),
        "scale": clean_text(parcel.get("PrScaleValue")),
        "postal_code": clean_text(parcel.get("PrPostalCode")),
        "house_no": parcel.get("PrHouseNo"),
        "parcel_extent_m2": parcel_area,
        "price_2021": value_2021,
        "price_2018": value_2018,
        "price_1980": parcel.get("PrPriceBase3"),
        "valuation_change_percent": valuation_change_percent,
        "is_preserved": bool(parcel.get("PrIsPreserved")),
        "is_ancient": bool(parcel.get("PrIsAncient")),
        "is_common_property": bool(parcel.get("PrIsCommonProperty")),
    }

    result = {
        "parcel": parcel_summary,
        "planning_zones": zones,
        "development_potential": {
            "theoretical_max_floor_area_m2": max_floor_area,
            "theoretical_max_ground_coverage_m2": max_ground_coverage,
            "effective_density_percent": capacity_result.get("effective_density_percent"),
            "effective_coverage_percent": capacity_result.get("effective_coverage_percent"),
            "calculation_method": capacity_result.get("calculation_method"),
            "area_basis_status": capacity_result.get("area_basis_status"),
            "calculation_authority_status": capacity_result.get("calculation_authority_status"),
            "multi_zone_policy_status": capacity_result.get("multi_zone_policy_status"),
            "zone_overlap_total_percent": capacity_result.get("zone_overlap_total_percent"),
            "zone_overlap_complete": capacity_result.get("zone_overlap_complete"),
            "calculation_warnings": capacity_result.get("calculation_warnings") or [],
        },
        "registration_summary": {
            "total_related_records": len(related),
            "by_type": dict(type_counter),
            "total_enclosed_extent_m2": safe_sum(enclosed_vals),
            "total_covered_extent_m2": safe_sum(covered_vals),
            "total_uncovered_extent_m2": safe_sum(uncovered_vals),
        },
        "related_properties": related,
        "warnings": warnings,
        "building_summary": {"count": 0, "features": []},
        "contour_summary": {},
        "spatial_checks": {},
    }
    result["structured_rule_analysis"] = evaluate_parcel_rules(result)
    return result


async def get_canonical_parcel_details(parcel_id: int) -> dict[str, Any]:
    cached = SITE_CACHE.get(parcel_id)
    now = time.time()
    if cached and now - cached[0] < SITE_CACHE_TTL_SECONDS:
        return cached[1]

    records = await get_general_identify(parcel_id)
    if not isinstance(records, list) or not records:
        raise HTTPException(status_code=502, detail="DLS Identify returned no records.")

    result = normalize_parcel_details(records, parcel_id)
    SITE_CACHE[parcel_id] = (time.time(), result)
    return result


def parcel_planning_cache_key(parcel_details: dict[str, Any]) -> str:
    parcel = parcel_details.get("parcel") or {}
    zones = parcel_details.get("planning_zones") or []
    fingerprint = {
        "parcel_id": parcel.get("parcel_id"),
        "municipality": parcel.get("municipality"),
        "district": parcel.get("district"),
        "zones": [
            {
                "zone": z.get("zone"),
                "density_percent": z.get("density_percent"),
                "coverage_percent": z.get("coverage_percent"),
                "max_floors": z.get("max_floors"),
                "max_height_m": z.get("max_height_m"),
                "overlap_percent": z.get("overlap_percent"),
                "remarks": z.get("remarks"),
            }
            for z in zones
        ],
        "rule_engine_version": RULE_ENGINE_VERSION,
    }
    return json.dumps(
        fingerprint,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


@app.get("/api/parcel-details")
async def parcel_details(parcel_id: int = Query(gt=0)) -> dict[str, Any]:
    return await get_canonical_parcel_details(parcel_id)


class ParcelRuleRequest(BaseModel):
    parcel_id: int = Field(gt=0)
    scenario: dict[str, Any] | None = None


@app.post("/api/parcel-rule-analysis")
async def parcel_rule_analysis(payload: ParcelRuleRequest) -> dict[str, Any]:
    parcel_details = await get_canonical_parcel_details(payload.parcel_id)
    return evaluate_parcel_rules(parcel_details, payload.scenario or {})


class ParcelPlanningRequest(BaseModel):
    parcel_id: int = Field(gt=0)


@app.post("/api/parcel-planning-analysis")
async def parcel_planning_analysis(payload: ParcelPlanningRequest) -> dict[str, Any]:
    parcel_details = await get_canonical_parcel_details(payload.parcel_id)
    cache_key = parcel_planning_cache_key(parcel_details)
    cached = PARCEL_PLANNING_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < PARCEL_PLANNING_CACHE_TTL_SECONDS:
        return {**cached[1], "cached": True}

    supabase = state.get("supabase")
    openai_client = state.get("openai")
    all_rows = state.get("all_rows")
    if not supabase or not openai_client or all_rows is None:
        raise HTTPException(
            status_code=503,
            detail="Planning intelligence is not ready yet.",
        )

    try:
        result = await asyncio.to_thread(
            generate_parcel_planning_analysis,
            parcel_details,
            openai_client,
            supabase,
            all_rows,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Automatic planning analysis failed: {exc}",
        ) from exc

    result = {
        **result,
        "parcel_id": payload.parcel_id,
        "generated_at": time.time(),
        "cached": False,
    }
    PARCEL_PLANNING_CACHE[cache_key] = (time.time(), result)
    return result


@app.get("/api/site")
async def site(
    lat: float = Query(ge=34.0, le=36.0),
    lon: float = Query(ge=31.0, le=35.0),
) -> dict[str, Any]:
    """Backward-compatible composed site payload built from the canonical parcel details."""
    parcel_feature = await get_parcel_at_point(lat, lon)
    map_props = parcel_feature.get("properties", {})
    sbpi = map_props.get("SBPI_ID_NO")
    if sbpi is None:
        raise HTTPException(status_code=502, detail="DLS parcel did not return SBPI_ID_NO.")
    try:
        parcel_id = int(sbpi)
    except Exception:
        raise HTTPException(status_code=502, detail=f"Unexpected SBPI_ID_NO: {sbpi}")

    details = await get_canonical_parcel_details(parcel_id)
    return {
        **details,
        "parcel_feature": parcel_feature,
        "parcel": {
            **details["parcel"],
            "map_geometry_extent_m2": map_props.get("Parcel Extend") or map_props.get("SHAPE.STArea()"),
        },
        "geometry_metrics": polygon_geometry_metrics(parcel_feature),
    }


class ParcelAIRequest(BaseModel):
    question: str
    parcel_context: dict[str, Any]
    scenario: dict[str, Any] | None = None


@app.post("/api/parcel-ai")
def parcel_ai(payload: ParcelAIRequest) -> dict[str, Any]:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    context = payload.parcel_context or {}
    scenario = payload.scenario or {}

    parcel_prompt = f"""
You are answering about a specific Cyprus parcel.

TRUSTED PARCEL CONTEXT FROM DLS / PLATFORM:
{json.dumps(context, ensure_ascii=False, indent=2)}

USER DEVELOPMENT SCENARIO:
{json.dumps(scenario, ensure_ascii=False, indent=2)}

USER QUESTION:
{question}

Instructions:
- Treat the parcel facts above as trusted structured context.
- Treat structured_rule_analysis as the current deterministic baseline for topics it covers.
- For calculation/setback topics covered by Order 4/2026, do not substitute a superseded Order 4/2024 rule.
- The March 2026 ETEK guide is interpretive context and does not override Order 4/2026.
- Do not invent missing parcel facts.
- Use the planning-regulation knowledge base for additional legal/planning rules, exceptions and parcel-specific policies.
- Distinguish official DLS facts, platform calculations, structured rule calculations, user assumptions, and planning interpretation.
- Where the answer depends on missing facts, say exactly what is missing.
""".strip()

    result = chat(ChatRequest(question=parcel_prompt))
    return {
        "answer": result.get("answer"),
        "sources": result.get("sources", []),
        "language": result.get("language"),
    }


@app.post("/api/site-extra")
async def site_extra(payload: dict[str, Any]) -> dict[str, Any]:
    parcel_feature = payload.get("parcel_feature") or {}
    if not parcel_feature:
        raise HTTPException(status_code=400, detail="Missing parcel geometry.")

    layer_items = list(SPECIAL_LAYERS.items())
    layer_results = await asyncio.gather(
        *(query_layer_intersections(layer_id, parcel_feature) for layer_id, _ in layer_items),
        return_exceptions=True,
    )

    spatial_checks = {}
    for (layer_id, layer_name), result in zip(layer_items, layer_results):
        if isinstance(result, Exception):
            result = {"ok": False, "error": str(result)}
        spatial_checks[str(layer_id)] = {"layer_name": layer_name, **result}

    buildings = []
    bcheck = spatial_checks.get("28", {})
    if bcheck.get("ok"):
        for f in bcheck.get("features", []):
            a = f.get("attributes", {})
            buildings.append({
                "object_id": a.get("Object ID") or a.get("OBJECTID"),
                "building_code": a.get("BLDG_CODE"),
                "building_description": clean_text(a.get("BLDG_DESC")),
            })

    contour_values = []
    ccheck = spatial_checks.get("30", {})
    if ccheck.get("ok"):
        for f in ccheck.get("features", []):
            a = f.get("attributes", {})
            val = a.get("Elevation")
            if val is not None:
                try:
                    contour_values.append(float(val))
                except Exception:
                    pass

    flags = []
    if buildings:
        flags.append(f"{len(buildings)} mapped building feature(s)")
    for lid, label in (("31", "Coast protection overlap"), ("32", "State land overlap")):
        check = spatial_checks.get(lid, {})
        if check.get("ok") and check.get("features"):
            flags.append(label)

    return {
        "geometry_metrics": polygon_geometry_metrics(parcel_feature),
        "spatial_checks": spatial_checks,
        "building_summary": {"count": len(buildings), "features": buildings},
        "contour_summary": {
            "count": len(contour_values),
            "min_elevation_m": min(contour_values) if contour_values else None,
            "max_elevation_m": max(contour_values) if contour_values else None,
            "elevation_range_m": round(max(contour_values) - min(contour_values), 2) if len(contour_values) >= 2 else None,
            "values_m": sorted(set(contour_values)),
        },
        "flags": flags,
    }


SITE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PLANA.CY</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{
  --ink:#17211b;--green:#173f2b;--muted:#6f7972;--line:#e1e6e2;
  --bg:#f6f7f5;--card:#fff;--soft:#eef3ef;--warn:#fff6df
}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:var(--ink);background:var(--bg)}
header{height:64px;padding:0 18px;background:#fff;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px}
h1{font-size:18px;margin:0}.eyebrow{font-size:10px;font-weight:800;letter-spacing:.12em;color:var(--muted)}
.header-actions{display:flex;gap:8px;align-items:center}
.layout{display:grid;grid-template-columns:minmax(380px,460px) 1fr;height:calc(100vh - 64px)}
aside{background:#fff;border-right:1px solid var(--line);padding:14px;overflow:auto}
#map{height:100%}
form{display:flex;gap:8px}
.search{flex:1;padding:11px 12px;border:1px solid #ccd4ce;border-radius:10px;font:inherit}
button,.navbtn{border:0;border-radius:10px;background:var(--green);color:#fff;padding:10px 13px;font-weight:750;cursor:pointer;text-decoration:none;font-size:13px}
.secondary{background:var(--soft);color:var(--green)}
.result{width:100%;display:block;margin-top:6px;text-align:left;background:var(--soft);color:var(--ink)}
.hero{margin-top:14px;padding:14px;background:var(--green);color:#fff;border-radius:14px}
.hero-title{font-size:11px;opacity:.75;text-transform:uppercase;letter-spacing:.08em;font-weight:800}
.hero-value{font-size:28px;font-weight:850;margin-top:4px}
.hero-sub{font-size:12px;opacity:.82;margin-top:5px}
.section{margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}
.section h2{font-size:14px;margin:0 0 9px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
.label{font-size:9px;letter-spacing:.06em;text-transform:uppercase;font-weight:800;color:var(--muted)}
.value{font-weight:760;margin-top:3px;word-break:break-word}.big{font-size:22px;color:var(--green)}
.zone{border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:9px;background:#fff}
.zone-title{font-size:26px;font-weight:850;color:var(--green)}
.badge{display:inline-block;margin-top:6px;background:var(--soft);color:var(--green);font-size:10px;font-weight:800;padding:4px 7px;border-radius:999px}
.muted{font-size:12px;color:var(--muted);line-height:1.5}
.notice{background:#edf7ef;border:1px solid #c7dfcc;border-radius:9px;padding:9px;font-size:12px}
.warning{background:var(--warn);border:1px solid #ead39d;border-radius:9px;padding:9px;font-size:12px;margin-top:6px}
.summary-pills{display:flex;gap:6px;flex-wrap:wrap}
.pill{background:var(--soft);color:var(--green);padding:5px 8px;border-radius:999px;font-size:11px;font-weight:750}
.scenario-form{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.scenario-form label{font-size:10px;font-weight:700;color:var(--muted)}
.scenario-form input,.scenario-form select,.scenario-form textarea,#aiQuestion{
  width:100%;margin-top:4px;padding:9px;border:1px solid #ccd4ce;border-radius:8px;font:inherit;background:#fff
}
.full{grid-column:1/-1}
.ai-box{border:1px solid var(--line);border-radius:10px;padding:10px;background:#fff}
.ai-answer{white-space:pre-wrap;line-height:1.5;font-size:12px}
.report-block{border:1px solid var(--line);border-radius:10px;padding:11px;background:#fff}
.report-title{font-size:18px;font-weight:850;margin-bottom:4px}
details{margin-top:10px;border:1px solid var(--line);border-radius:10px;background:#fff}
summary{cursor:pointer;padding:10px 11px;font-weight:750;font-size:12px}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:11px;min-width:820px}
th,td{padding:7px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:#f3f5f3;font-size:9px;text-transform:uppercase}
.check{padding:7px 0;border-bottom:1px solid var(--line);font-size:11px}
.quick-actions{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:10px}
.compact-hide{display:none}
.intel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:9px}
.confidence{display:inline-block;border-radius:999px;padding:4px 8px;font-size:9px;font-weight:850;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}
.confidence-high{background:#e6f4ea;color:#1f6a38}.confidence-medium{background:#fff3d6;color:#75580c}.confidence-low{background:#fdeaea;color:#8a2c2c}
.intel-provision{border:1px solid var(--line);border-radius:10px;padding:10px;margin-top:8px;background:#fff}
.intel-category{font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:850}
.intel-title{font-size:13px;font-weight:850;margin:3px 0 5px}
.intel-impact{margin-top:6px;padding:7px 8px;background:var(--soft);border-radius:8px;font-size:11px;line-height:1.45}
.source-row{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}
.source-chip{display:inline-block;background:#f3f5f3;border:1px solid var(--line);border-radius:999px;padding:4px 6px;font-size:9px;color:var(--muted)}
.intel-list{margin:7px 0 0;padding-left:18px;font-size:11px;line-height:1.45}
.rule-baseline{border:1px solid #bdd8c5;background:#f5faf6;border-radius:11px;padding:10px;margin-bottom:10px}
.rule-meta{font-size:10px;color:var(--muted);line-height:1.45;margin-top:5px}
.rule-trigger{border-left:3px solid var(--green);padding:7px 8px;margin-top:7px;background:#fff;border-radius:0 8px 8px 0}
.rule-status{display:inline-block;border-radius:999px;padding:4px 7px;background:#e6f4ea;color:#1f6a38;font-size:9px;font-weight:850;text-transform:uppercase;letter-spacing:.04em}
.rule-track{margin-top:8px;padding-top:8px;border-top:1px solid #dce8df}
@media(max-width:900px){
  .layout{grid-template-columns:1fr;height:auto}
  aside{order:2;border-right:0}.map-wrap{order:1}#map{height:52vh}
}
@media print{
  .layout{display:block;height:auto}aside{border:0;overflow:visible}
  #map,form,#results,.header-actions,.quick-actions,details{display:none!important}
  .section{break-inside:avoid}.table-wrap{overflow:visible}table{min-width:0;font-size:8px}
}

.config-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}
.config-card{border:1px solid var(--line);border-radius:12px;padding:11px;background:#fff}
.config-card h3{margin:2px 0 4px;font-size:15px}
.config-visual{height:180px;border:1px solid var(--line);border-radius:10px;background:#f8faf8;overflow:hidden;margin:9px 0}
.config-visual svg{display:block;width:100%;height:100%}
.config-metrics{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.metric-mini{background:var(--soft);border-radius:8px;padding:7px}
.metric-mini .k{font-size:9px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:800}
.metric-mini .v{font-size:14px;font-weight:800;margin-top:2px}
.compare-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px;margin-top:10px}
.compare-table{width:100%;border-collapse:collapse;min-width:620px}
.compare-table th,.compare-table td{padding:8px;border-bottom:1px solid var(--line);font-size:11px;text-align:center}
.compare-table th:first-child,.compare-table td:first-child{text-align:left;position:sticky;left:0;background:#fff;font-weight:800}
.compare-tag{display:inline-block;padding:4px 7px;border-radius:999px;background:var(--soft);color:var(--green);font-size:10px;font-weight:800}
@media(max-width:700px){.config-grid{grid-template-columns:1fr}}


body{overflow:hidden}
.layout{grid-template-columns:1fr 450px}
aside{order:2;border-right:0;border-left:1px solid var(--line);padding:0;display:flex;flex-direction:column}
.map-wrap{order:1;position:relative}
#map{height:100%;width:100%}
.search-shell{padding:14px;border-bottom:1px solid var(--line);background:#fff}
.panel-scroll{overflow:auto;padding:14px;flex:1}
.sticky-parcel{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid var(--line);padding:12px 14px}
.sticky-parcel.empty{display:none}
.parcel-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.parcel-title{font-size:18px;font-weight:850}
.parcel-meta{font-size:12px;color:var(--muted);margin-top:3px}
.parcel-zoning{font-size:12px;color:var(--green);font-weight:800;margin-top:5px}
.flag-row{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
.flag{background:var(--warn);border:1px solid #ead39d;border-radius:999px;padding:5px 8px;font-size:10px;font-weight:800;color:#6b5320}
.loading-panel{display:none;position:absolute;top:14px;left:14px;z-index:800;background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 12px;box-shadow:0 12px 30px rgba(0,0,0,.12);min-width:220px}
.loading-panel.show{display:block}
.load-step{font-size:11px;color:var(--muted);padding:3px 0}
.load-step.active{color:var(--ink);font-weight:800}
.load-step.done{color:var(--green);font-weight:800}
.skeleton{background:linear-gradient(90deg,#f0f2f0 25%,#f8f9f8 50%,#f0f2f0 75%);background-size:200% 100%;animation:shimmer 1.2s infinite;border-radius:8px}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.sk-line{height:14px;margin:7px 0}.sk-card{height:72px}
.section:first-child{margin-top:0;padding-top:0;border-top:0}
.config-grid{grid-template-columns:1fr}
.config-card.primary{border:2px solid var(--green)}
.recommend-label{display:inline-block;margin-bottom:6px;background:var(--green);color:#fff;font-size:9px;font-weight:850;letter-spacing:.05em;text-transform:uppercase;padding:4px 7px;border-radius:999px}
.other-configs{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:9px}
.ai-context{font-size:10px;color:var(--muted);margin-bottom:7px}
.comparison-bars{margin-top:10px}
.bar-row{display:grid;grid-template-columns:110px 1fr;gap:8px;align-items:center;margin:7px 0}
.bar-label{font-size:10px;color:var(--muted);font-weight:800}
.bar-track{display:flex;gap:5px;align-items:end;height:28px}
.bar-seg{flex:1;background:var(--soft);border-radius:5px 5px 2px 2px;min-width:20px;position:relative}
.bar-seg span{position:absolute;bottom:2px;left:0;right:0;text-align:center;font-size:8px;font-weight:800;color:var(--green)}
@media(max-width:900px){
  body{overflow:auto}.layout{display:block;height:auto}.map-wrap{height:52vh}
  aside{border-left:0}.sticky-parcel{position:sticky;top:64px}.other-configs{grid-template-columns:1fr}
}

</style>
</head>
<body>
<header>
  <div>
    <div class="eyebrow">CYPRUS PLANNING INTELLIGENCE</div>
    <h1>PLANA.CY</h1>
  </div>
  <div class="header-actions"><a href="/chat" class="navbtn secondary">Planning AI</a></div>
</header>

<div class="layout">
<aside>
  <div class="search-shell">
    <form id="searchForm">
      <input id="searchInput" class="search" placeholder="Search address in Cyprus">
      <button>Search</button>
    </form>
    <div id="results"></div>
  </div>

  <div id="stickyParcel" class="sticky-parcel empty">
    <div class="parcel-top">
      <div>
        <div id="stickyParcelTitle" class="parcel-title"></div>
        <div id="stickyParcelMeta" class="parcel-meta"></div>
        <div id="stickyParcelZoning" class="parcel-zoning"></div>
      </div>
      <button id="newSiteBtn" type="button" class="secondary">New site</button>
    </div>
    <div id="stickyFlags" class="flag-row"></div>
  </div>

  <div class="panel-scroll">
    <section class="section"><h2>Planning</h2><div id="planning" class="muted">Select a parcel to begin.</div></section>
    <section class="section"><h2>Planning intelligence</h2><div id="planningIntelligence" class="muted">Select a parcel for automatic planning analysis.</div></section>
    <section class="section"><h2>Development potential</h2><div id="potential" class="muted">No parcel selected.</div></section>
    <section class="section"><h2>What could fit here?</h2><div id="configurations" class="muted">Select a parcel to generate concept configurations.</div><div id="configurationComparison"></div></section>

    <section class="section" id="aiSection">
      <h2>Ask PLANA.CY</h2>
      <div class="ai-box">
        <div id="aiContext" class="ai-context">No parcel selected.</div>
        <textarea id="aiQuestion" rows="3" placeholder="Ask anything about this parcel or Cyprus planning rules…"></textarea>
        <button id="askAiBtn" type="button" style="margin-top:7px;width:100%">Ask PLANA.CY</button>
        <div id="aiAnswer" class="ai-answer muted" style="margin-top:10px">Select a parcel first.</div><div id="loadTiming" class="muted" style="margin-top:6px;font-size:10px"></div>
      </div>
    </section>

    <details id="moreDetails" class="section">
      <summary>More site information</summary>
      <div style="padding:0 10px 10px">
        <h2>Buildings & terrain</h2><div id="terrain" class="muted">No parcel selected.</div>
        <h2 style="margin-top:14px">Site constraints</h2><div id="spatial" class="muted">No parcel selected.</div>
        <div id="warningsWrap" style="display:none"><h2 style="margin-top:14px">Site flags</h2><div id="warnings"></div></div>
        <h2 style="margin-top:14px">Parcel geometry</h2><div id="geometry" class="muted">No parcel selected.</div>
        <h2 style="margin-top:14px">DLS General Valuation</h2><div id="valuation" class="muted">No parcel selected.</div>
        <h2 style="margin-top:14px">Registrations</h2><div id="registrations" class="muted">No parcel selected.</div>
        <h2 style="margin-top:14px">Registered units</h2><div id="units" class="muted">No parcel selected.</div>
      </div>
    </details>

    <section class="section">
      <button id="generateReportBtn" type="button" class="secondary" style="width:100%">Generate PDF-ready report</button>
      <div id="report" class="muted" style="margin-top:10px"></div>
    </section>
  </div>
</aside>
<div class="map-wrap">
      <div id="loadingPanel" class="loading-panel">
        <div id="loadParcel" class="load-step active">Finding parcel…</div>
      </div>
      <div id="map"></div>
    </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/esri-leaflet@3.0.15/dist/esri-leaflet.js"></script>
<script>
const DLS="https://eservices.dls.moi.gov.cy/arcgis/rest/services/National/CadastralMap_EN/MapServer";
const map=L.map("map").setView([35.1264,33.4299],9);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:20,attribution:"&copy; OpenStreetMap contributors"}).addTo(map);
L.esri.dynamicMapLayer({url:DLS,layers:[0],opacity:1,minZoom:15}).addTo(map);

let selected=null;
let currentSite=null;
let currentAiAnswer='';
let currentPlanningAnalysis=null;
let currentConfigurations=[];
let selectionRequestId=0;
const $=id=>document.getElementById(id);
const esc=v=>String(v??"—").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
const present=v=>!(v===null||v===undefined||v==="");
function card(label,value,big=false,suffix=""){return `<div class="card"><div class="label">${esc(label)}</div><div class="value ${big?"big":""}">${present(value)?esc(value)+suffix:"—"}</div></div>`}
function money(v){return present(v)?"€"+Number(v).toLocaleString(undefined,{maximumFractionDigits:2}):"—"}

function renderZones(zones){
 if(!zones?.length)return '<div class="muted">No planning-zone data returned.</div>';
 return zones.map(z=>`<div class="zone"><div class="label">Planning zone</div><div class="zone-title">${esc(z.zone)}</div>
 ${present(z.overlap_percent)?`<div class="badge">${esc(z.overlap_percent)}% of parcel</div>`:""}
 <div class="grid" style="margin-top:12px">${card("Density / Δόμηση",z.density_percent,true,"%")}${card("Coverage / Κάλυψη",z.coverage_percent,true,"%")}${card("Maximum floors",z.max_floors,true)}${card("Maximum height",z.max_height_m,true," m")}</div>
 ${present(z.remarks)?`<p class="muted"><b>Remarks:</b> ${esc(z.remarks)}</p>`:""}</div>`).join("");
}

function confidenceBadge(value){
 const level=["high","medium","low"].includes(String(value||"").toLowerCase())?String(value).toLowerCase():"low";
 return `<span class="confidence confidence-${level}">${esc(level)} confidence</span>`;
}
function sourceChip(source){
 const paragraph=present(source?.paragraph)?` · § ${source.paragraph}`:"";
 const page=present(source?.page_number)?` · p. ${source.page_number}`:"";
 return `<span class="source-chip">${esc(source?.title||"Source")}${esc(paragraph)}${esc(page)}</span>`;
}
function uniqueStrings(values){return [...new Set((values||[]).filter(Boolean).map(x=>String(x).trim()).filter(Boolean))]}
function renderStructuredRuleHtml(data){
 if(!data)return "";
 const base=data.base_capacity||{};
 const applied=(data.applied_rules||[]).filter(r=>r.rule_id!=="order4_2026_current_source");
 const triggered=data.triggered_rules||[];
 const visible=[...applied,...triggered].slice(0,6);
 const tracked=(data.conditional_rules||[]).length;
 const baseMethod=base.calculation_method==="weighted_zone_overlap"?`Weighted multi-zone · ${present(base.effective_density_percent)?base.effective_density_percent+"% effective density":""}${present(base.effective_coverage_percent)?` · ${base.effective_coverage_percent}% effective coverage`:""}`:"Single-zone/base coefficient calculation";
 return `<div class="rule-baseline">
   <div class="intel-head"><div><div class="intel-category">Structured rule engine</div><div class="intel-title">Order 4/2026 baseline</div></div><span class="rule-status">Current · 11 May 2026</span></div>
   <div class="rule-meta">${esc(baseMethod)}. PLANA uses Order 4/2026 for covered calculation and setback topics; the March 2026 ETEK guide is interpretive context and does not override the newer Order.</div>
   ${visible.map(r=>`<div class="rule-trigger"><div class="intel-category">${esc(r.category||"Rule")}</div><div style="font-size:12px;font-weight:800;margin:2px 0">${esc(r.title)}</div><div class="muted">${esc(r.outcome||r.summary)}</div><div class="source-row">${(r.source_refs||[]).map(sourceChip).join("")}</div></div>`).join("")}
   <div class="rule-track"><div class="muted"><b>${esc(data.catalog_rule_count||0)} structured rules encoded.</b> ${tracked?`${esc(tracked)} design-dependent rules are tracked but are not applied until the required development inputs are known.`:""}</div></div>
 </div>`;
}
function renderPlanningIntelligence(data){
 const ruleData=currentSite?.structured_rule_analysis||null;
 if(!data){
   $("planningIntelligence").innerHTML=`${renderStructuredRuleHtml(ruleData)}<div class="muted">Automatic RAG analysis is unavailable.</div>`;
   return;
 }
 const provisions=data.material_provisions||[];
 const caveats=data.capacity_caveats||[];
 const checks=uniqueStrings([...(ruleData?.checks_before_reliance||[]),...(data.checks_before_reliance||[])]);
 const provisionsHtml=provisions.length?provisions.map(p=>`<div class="intel-provision">
   <div class="intel-head" style="margin-bottom:4px"><div><div class="intel-category">${esc(p.category||"Planning provision")}</div><div class="intel-title">${esc(p.title)}</div></div>${confidenceBadge(p.confidence)}</div>
   <div class="muted">${esc(p.finding)}</div>
   ${p.development_impact?`<div class="intel-impact"><b>Why it matters:</b> ${esc(p.development_impact)}</div>`:""}
   <div class="source-row">${(p.source_refs||[]).map(sourceChip).join("")}</div>
 </div>`).join(""):'<div class="notice">No additional parcel-specific provision was established strongly enough from the retrieved knowledge-base sources.</div>';
 $("planningIntelligence").innerHTML=`${renderStructuredRuleHtml(ruleData)}
 <div class="intel-head"><div><div class="intel-category">Parcel-specific RAG review</div><div class="muted">${esc(data.summary||"Automatic evidence review complete.")}</div></div>${confidenceBadge(data.confidence)}</div>
 ${provisionsHtml}
 ${caveats.length?`<div style="margin-top:10px"><div class="label">Capacity caveats</div><ul class="intel-list">${caveats.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>`:""}
 ${checks.length?`<div style="margin-top:10px"><div class="label">Confirm before relying on capacity</div><ul class="intel-list">${checks.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>`:""}
 <p class="muted" style="margin-top:9px">Structured rules are deterministic only where the required inputs are known. RAG confidence reflects evidence coverage, not planning approval or legal certainty.</p>`;
}
async function loadPlanningIntelligence(requestId,parcelId){
 $("planningIntelligence").innerHTML=`${renderStructuredRuleHtml(currentSite?.structured_rule_analysis)}<div class="skeleton sk-line"></div><div class="skeleton sk-card"></div><p class="muted">PLANA.CY is checking additional parcel-specific provisions in the knowledge base…</p>`;
 const started=performance.now();
 try{
   const r=await fetch("/api/parcel-planning-analysis",{
     method:"POST",
     headers:{"Content-Type":"application/json"},
     body:JSON.stringify({parcel_id:parcelId})
   });
   const d=await r.json();
   if(requestId!==selectionRequestId || currentSite?.parcel?.parcel_id!==parcelId)return;
   if(!r.ok)throw new Error(d.detail||"Automatic planning analysis failed");
   currentPlanningAnalysis=d;
   currentSite.planning_analysis=d;
   renderPlanningIntelligence(d);
   const seconds=((performance.now()-started)/1000).toFixed(1);
   const previous=$("loadTiming").textContent;
   $("loadTiming").textContent=`${previous}${previous?" · ":""}Planning intelligence ${seconds}s${d.cached?" cached":""}`;
 }catch(err){
   if(requestId!==selectionRequestId || currentSite?.parcel?.parcel_id!==parcelId)return;
   currentPlanningAnalysis=null;
   $("planningIntelligence").innerHTML=`${renderStructuredRuleHtml(currentSite?.structured_rule_analysis)}<div class="warning">${esc(err.message||err)}</div><p class="muted">The structured Order 4/2026 checks and DLS parcel data remain available. You can use Ask PLANA.CY for a narrower planning question.</p>`;
 }
}

function renderUnitTable(rows){
 if(!rows?.length)return '<div class="muted">No related registered units returned.</div>';
 return `<div class="table-wrap"><table><thead><tr><th>Registration</th><th>Type</th><th>Plan</th><th>Floor</th><th>2021</th><th>2018</th><th>1980</th><th>Enclosed</th><th>Covered</th><th>Uncovered</th></tr></thead><tbody>
 ${rows.map(x=>`<tr><td>${esc(present(x.registration_block)&&present(x.registration_no)?x.registration_block+"/"+x.registration_no:x.registration_no)}</td><td>${esc(x.kind||x.property_type)}</td><td>${esc(x.plan_no)}</td><td>${esc(x.unit_floor_no)}</td><td>${esc(money(x.price_2021))}</td><td>${esc(money(x.price_2018))}</td><td>${esc(money(x.price_1980))}</td><td>${esc(x.enclosed_extent)}</td><td>${esc(x.covered_extent)}</td><td>${esc(x.uncovered_extent)}</td></tr>`).join("")}
 </tbody></table></div>`;
}


function buildParcelContext(){
 if(!currentSite)return {};
 return {
  parcel:currentSite.parcel,
  planning_zones:currentSite.planning_zones,
  development_potential:currentSite.development_potential,
  geometry_metrics:currentSite.geometry_metrics,
  building_summary:currentSite.building_summary,
  contour_summary:currentSite.contour_summary,
  registration_summary:currentSite.registration_summary,
  warnings:currentSite.warnings,
  structured_rule_analysis:currentSite.structured_rule_analysis,
  planning_analysis:currentPlanningAnalysis
 };
}

function generateReport(){
 if(!currentSite){$("report").innerHTML='<div class="warning">Select a parcel first.</div>';return}
 const p=currentSite.parcel;
 const zones=(currentSite.planning_zones||[]).map(z=>`${z.zone}: ${z.density_percent}% density, ${z.coverage_percent}% coverage, ${z.max_floors} floors, ${z.max_height_m} m`).join("<br>");
 $("report").innerHTML=`<div class="report-block">
 <div class="report-title">PLANA.CY Site Feasibility Summary</div>
 <div class="muted">Parcel ${esc(p.parcel_number)} · ${esc(p.district)} · ${esc(p.municipality)}</div>
 <hr style="border:0;border-top:1px solid var(--line);margin:12px 0">
 <b>Parcel</b><br>${esc(p.parcel_extent_m2)} m² · Sheet ${esc(p.sheet)} / Plan ${esc(p.plan)} · Block ${esc(p.block)}<br><br>
 <b>Planning</b><br>${zones||"No planning data"}<br><br>
 <b>Structured Order 4/2026 rule checks</b><br>${currentSite.structured_rule_analysis?`${esc(currentSite.structured_rule_analysis.source_precedence)}<br>${[...(currentSite.structured_rule_analysis.applied_rules||[]),...(currentSite.structured_rule_analysis.triggered_rules||[])].map(x=>`• ${esc(x.title)}: ${esc(x.outcome||x.summary)}`).join("<br>")}`:"Not generated"}<br><br>
 <b>Automatic parcel-specific planning intelligence</b><br>${currentPlanningAnalysis?`${esc(currentPlanningAnalysis.summary||"Analysis complete")}<br>${(currentPlanningAnalysis.material_provisions||[]).map(x=>`• ${esc(x.title)}: ${esc(x.finding)}${(x.source_refs||[]).length?` [${(x.source_refs||[]).map(s=>`${esc(s.title)}${present(s.page_number)?`, p. ${esc(s.page_number)}`:""}`).join("; ")}]`:""}`).join("<br>")||"No additional material provisions established."}`:"Not generated"}<br><br>
 <b>Calculated potential</b><br>
 Theoretical max floor area: ${esc(currentSite.development_potential?.theoretical_max_floor_area_m2)} m²<br>
 Theoretical max ground coverage: ${esc(currentSite.development_potential?.theoretical_max_ground_coverage_m2)} m²<br><br>
 <b>Warnings</b><br>${(currentSite.warnings||[]).map(esc).join("<br>")||"None generated"}
 ${currentConfigurations.length?`<br><br><b>Concept configurations</b><br>${currentConfigurations.map((x,i)=>`Option ${i+1}: ${esc(x.name)} · ${esc(x.floors)} floors · ${esc(x.total_floor_area_m2)} m² total · ${esc(x.ground_footprint_m2)} m² footprint`).join("<br>")}`:""}
 ${currentAiAnswer?`<br><br><b>AI planning summary</b><div class="ai-answer">${esc(currentAiAnswer)}</div>`:""}
 <p class="muted">Official DLS facts, platform calculations, user assumptions and AI interpretation should be reviewed separately before relying on this report for a formal planning decision.</p>
 </div>`;
}


document.addEventListener("click",async e=>{
 if(e.target?.id==="newSiteBtn"){resetSite();return}
 if(e.target?.id==="focusAiBtn"){
   document.getElementById("aiSection")?.scrollIntoView({behavior:"smooth",block:"start"});
   setTimeout(()=>document.getElementById("aiQuestion")?.focus(),300);
 }
 if(e.target?.id==="askAiBtn"){
   if(!currentSite){$("aiAnswer").textContent="Select a parcel first.";return}
   const q=$("aiQuestion").value.trim();
   if(!q){$("aiAnswer").textContent="Enter a question first.";return}
   $("aiAnswer").textContent="Asking the planning AI…";
   try{
     const r=await fetch("/api/parcel-ai",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
       question:q,parcel_context:buildParcelContext()
     })});
     const d=await r.json();
     if(!r.ok)throw new Error(d.detail||"AI request failed");
     currentAiAnswer=d.answer||"No answer returned.";
     $("aiAnswer").textContent=currentAiAnswer;
   }catch(err){
     $("aiAnswer").textContent=String(err.message||err);
   }
 }
 if(e.target?.id==="generateReportBtn")generateReport();
});





function setLoadingStage(stage){
 const ids=["loadParcel"];
 $("loadingPanel").classList.add("show");
 ids.forEach((id,i)=>{
   const el=$(id); el.classList.remove("active","done");
   if(i<stage)el.classList.add("done"); else if(i===stage)el.classList.add("active");
 });
}
function hideLoading(){ $("loadingPanel").classList.remove("show"); }
function planningSkeleton(){
 return `<div class="grid"><div class="card"><div class="skeleton sk-line"></div><div class="skeleton sk-card"></div></div><div class="card"><div class="skeleton sk-line"></div><div class="skeleton sk-card"></div></div></div>`;
}
function configSkeleton(){
 return `<div class="config-card"><div class="skeleton sk-line"></div><div class="skeleton" style="height:180px"></div></div>`;
}
function updateStickyParcel(d){
 const p=d.parcel||{}, zones=d.planning_zones||[];
 $("stickyParcel").classList.remove("empty");
 $("stickyParcelTitle").textContent=`Parcel ${p.parcel_number??"—"}`;
 $("stickyParcelMeta").textContent=`${p.parcel_extent_m2??"—"} m² · ${p.district??""}${p.municipality?` · ${p.municipality}`:""}`;
 $("stickyParcelZoning").textContent=zones.length?zones.map(z=>`${z.zone} · ${z.density_percent}% density · ${z.max_floors} floors`).join(" | "):"Planning zone not returned";
 const flags=[];
 if((d.building_summary?.count||0)>0)flags.push(`${d.building_summary.count} mapped building${d.building_summary.count===1?"":"s"}`);
 if(zones.length>1)flags.push("Multiple planning zones");
 if(d.spatial_checks?.["32"]?.ok && d.spatial_checks["32"].features?.length)flags.push("State land overlap");
 if(d.spatial_checks?.["31"]?.ok && d.spatial_checks["31"].features?.length)flags.push("Coast protection overlap");
 if(p.is_preserved)flags.push("Preserved property");
 if(p.is_ancient)flags.push("Ancient property");
 $("stickyFlags").innerHTML=flags.map(f=>`<span class="flag">${esc(f)}</span>`).join("");
}
function resetSite(){
 selectionRequestId++;
 currentSite=null; currentConfigurations=[]; currentAiAnswer=""; currentPlanningAnalysis=null;
 if(selected){map.removeLayer(selected);selected=null}
 $("stickyParcel").classList.add("empty"); $("stickyFlags").innerHTML="";
 $("planning").innerHTML='<div class="muted">Select a parcel to begin.</div>';
 $("planningIntelligence").innerHTML='<div class="muted">Select a parcel for automatic planning analysis.</div>';
 $("potential").innerHTML='<div class="muted">No parcel selected.</div>';
 $("configurations").innerHTML='<div class="muted">Select a parcel to generate concept configurations.</div>';
 $("configurationComparison").innerHTML="";
 $("aiContext").textContent="No parcel selected."; $("aiQuestion").value=""; $("aiAnswer").textContent="Select a parcel first.";
 ["terrain","spatial","geometry","valuation","registrations","units"].forEach(id=>$(id).innerHTML='<div class="muted">No parcel selected.</div>');
 $("warningsWrap").style.display="none"; $("warnings").innerHTML=""; $("report").innerHTML=""; $("results").innerHTML=""; $("searchInput").value="";
 hideLoading(); map.setView([35.1264,33.4299],9); setTimeout(()=>$("searchInput").focus(),150);
}

function polygonToSvgPath(feature){
  const geom=feature?.geometry;
  if(!geom || geom.type!=="Polygon" || !geom.coordinates?.length)return "";
  const ring=geom.coordinates[0];
  if(!ring?.length)return "";
  const xs=ring.map(p=>p[0]), ys=ring.map(p=>p[1]);
  const minX=Math.min(...xs), maxX=Math.max(...xs), minY=Math.min(...ys), maxY=Math.max(...ys);
  const pad=14, W=240, H=160;
  const sx=(W-2*pad)/Math.max(maxX-minX,1e-9);
  const sy=(H-2*pad)/Math.max(maxY-minY,1e-9);
  const s=Math.min(sx,sy);
  const ox=(W-(maxX-minX)*s)/2;
  const oy=(H-(maxY-minY)*s)/2;
  return ring.map((p,i)=>{
    const x=ox+(p[0]-minX)*s;
    const y=H-(oy+(p[1]-minY)*s);
    return `${i===0?"M":"L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ")+" Z";
}

function configVisualSvg(x){
  const parcelArea=Number(currentSite?.parcel?.parcel_extent_m2)||1;
  const footprint=Math.max(1,Number(x.ground_footprint_m2)||1);
  const floors=Math.max(1,Number(x.floors)||1);
  const parcelPath=polygonToSvgPath(currentSite?.parcel_feature);
  const ratio=Math.min(.82,Math.max(.10,Math.sqrt(Math.min(footprint/parcelArea,1))));
  const bw=120*ratio, bh=82*ratio;
  const bx=120-bw/2, by=88-bh/2;
  const bands=Math.min(floors,8);
  let floorLines="";
  for(let i=1;i<bands;i++){
    const yy=by+(bh*i/bands);
    floorLines+=`<line x1="${bx}" y1="${yy}" x2="${bx+bw}" y2="${yy}" stroke="currentColor" stroke-width="1" opacity=".28"/>`;
  }
  return `<svg viewBox="0 0 240 160" aria-label="Conceptual configuration diagram">
    <rect width="240" height="160" fill="#f8faf8"/>
    ${parcelPath?`<path d="${parcelPath}" fill="none" stroke="currentColor" stroke-width="2" opacity=".35"/>`:""}
    <rect x="${bx}" y="${by}" width="${bw}" height="${bh}" rx="3" fill="currentColor" opacity=".16"/>
    <rect x="${bx}" y="${by}" width="${bw}" height="${bh}" rx="3" fill="none" stroke="currentColor" stroke-width="2"/>
    ${floorLines}
    <text x="120" y="146" text-anchor="middle" font-size="10" fill="currentColor" opacity=".65">${floors} floor${floors===1?"":"s"} · ${Math.round(footprint)} m² footprint</text>
  </svg>`;
}

function comparisonLabel(items){
  if(!items?.length)return [];
  const minFoot=Math.min(...items.map(x=>Number(x.ground_footprint_m2)||Infinity));
  const minFloors=Math.min(...items.map(x=>Number(x.floors)||Infinity));
  const maxFloors=Math.max(...items.map(x=>Number(x.floors)||0));
  const maxArea=Math.max(...items.map(x=>Number(x.total_floor_area_m2)||0));
  return items.map(x=>{
    const tags=[];
    if(Number(x.ground_footprint_m2)===minFoot)tags.push("Smallest footprint");
    if(Number(x.floors)===minFloors)tags.push("Lowest rise");
    if(Number(x.floors)===maxFloors && maxFloors!==minFloors)tags.push("Most vertical");
    if(Number(x.total_floor_area_m2)===maxArea)tags.push("Largest floor area");
    return tags;
  });
}

function renderConfigurationComparison(items){
 if(!items?.length){$("configurationComparison").innerHTML="";return}
 const tags=comparisonLabel(items);
 const maxArea=Math.max(...items.map(x=>Number(x.total_floor_area_m2)||0),1);
 const maxFoot=Math.max(...items.map(x=>Number(x.ground_footprint_m2)||0),1);
 $("configurationComparison").innerHTML=`
 <div class="section" style="margin-top:12px;padding-top:12px">
  <h2>Compare options</h2>
  <div class="comparison-bars">
   <div class="bar-row"><div class="bar-label">Total floor area</div><div class="bar-track">${items.map((x,i)=>`<div class="bar-seg" style="height:${8+18*((Number(x.total_floor_area_m2)||0)/maxArea)}px"><span>${i+1}</span></div>`).join("")}</div></div>
   <div class="bar-row"><div class="bar-label">Ground footprint</div><div class="bar-track">${items.map((x,i)=>`<div class="bar-seg" style="height:${8+18*((Number(x.ground_footprint_m2)||0)/maxFoot)}px"><span>${i+1}</span></div>`).join("")}</div></div>
  </div>
  <div class="compare-wrap"><table class="compare-table">
   <thead><tr><th>Metric</th>${items.map((x,i)=>`<th>Option ${i+1}<br><span class="compare-tag">${esc(x.name)}</span></th>`).join("")}</tr></thead>
   <tbody>
    <tr><td>Floors</td>${items.map(x=>`<td>${esc(x.floors)}</td>`).join("")}</tr>
    <tr><td>Total floor area</td>${items.map(x=>`<td>${esc(x.total_floor_area_m2)} m²</td>`).join("")}</tr>
    <tr><td>Ground footprint</td>${items.map(x=>`<td>${esc(x.ground_footprint_m2)} m²</td>`).join("")}</tr>
    <tr><td>Profile</td>${tags.map(x=>`<td>${esc(x.join(" · ")||"Balanced")}</td>`).join("")}</tr>
   </tbody>
  </table></div>
 </div>`;
}
function configScore(x,maxArea,maxFloors){
 const area=Number(x.total_floor_area_m2)||0, floors=Number(x.floors)||0;
 const areaUse=maxArea?Math.min(area/maxArea,1):0;
 const floorBalance=maxFloors?1-Math.abs((floors/maxFloors)-0.7):0.5;
 return areaUse*.65+floorBalance*.35;
}
function configCard(x,i,primary=false){
 return `<div class="config-card ${primary?"primary":""}">
   ${primary?'<div class="recommend-label">Recommended starting point</div>':`<div class="label">Option ${i+1}</div>`}
   <h3>${esc(x.name)}</h3><div class="muted">${esc(x.concept)}</div>
   <div class="config-visual">${configVisualSvg(x)}</div>
   <div class="config-metrics">
    <div class="metric-mini"><div class="k">Floors</div><div class="v">${esc(x.floors)}</div></div>
    <div class="metric-mini"><div class="k">Total area</div><div class="v">${esc(x.total_floor_area_m2)} m²</div></div>
    <div class="metric-mini"><div class="k">Footprint</div><div class="v">${esc(x.ground_footprint_m2)} m²</div></div>
    <div class="metric-mini"><div class="k">Area / floor</div><div class="v">${esc(x.floor_area_per_floor_m2)} m²</div></div>
   </div>
   <p class="muted"><b>Best for:</b> ${esc(x.why_it_may_suit)}</p>
   <p class="muted"><b>Main uncertainty:</b> ${esc(x.key_caveat)}</p>
 </div>`;
}
function renderConfigurations(items){
 if(!items?.length){$("configurations").innerHTML='<div class="muted">No concept configurations were generated.</div>';$("configurationComparison").innerHTML="";return}
 const maxArea=Number(currentSite?.development_potential?.theoretical_max_floor_area_m2)||0;
 const maxFloors=Math.max(...(currentSite?.planning_zones||[]).map(z=>Number(z.max_floors)||0),0);
 const ranked=items.map((x,i)=>({x,i,score:configScore(x,maxArea,maxFloors)})).sort((a,b)=>b.score-a.score);
 const first=ranked[0], rest=ranked.slice(1);
 $("configurations").innerHTML=`${configCard(first.x,first.i,true)}
 ${rest.length?`<div class="muted" style="margin-top:10px;font-weight:800">Other possible configurations</div><div class="other-configs">${rest.map(o=>configCard(o.x,o.i,false)).join("")}</div>`:""}
 <p class="muted">These diagrams are conceptual massing illustrations only. They do not represent verified building placement, setbacks, access, parking or architectural design.</p>`;
 renderConfigurationComparison(items);
}
async function loadConfigurations(){
 if(!currentSite){
   $("configurations").innerHTML='<div class="muted">Select a parcel first.</div>';
   return;
 }
 const maxArea=Number(currentSite.development_potential?.theoretical_max_floor_area_m2)||0;
 const maxFoot=Number(currentSite.development_potential?.theoretical_max_ground_coverage_m2)||0;
 const maxFloors=Math.max(...(currentSite.planning_zones||[]).map(z=>Number(z.max_floors)||0),0);

 if(!maxArea || !maxFoot || !maxFloors){
   $("configurations").innerHTML='<div class="muted">Not enough confirmed planning data to generate configurations.</div>';
   $("configurationComparison").innerHTML="";
   return;
 }

 const candidates=[];
 const floorSet=[1,2,3,4,5,6].filter(f=>f<=maxFloors);
 floorSet.forEach(floors=>{
   const usableTotal=Math.min(maxArea, maxFoot*floors);
   const footprint=Math.min(maxFoot, usableTotal/floors);
   if(usableTotal<=0 || footprint<=0)return;
   candidates.push({
     name:floors===1?"Single-level":floors===2?"Low-rise":floors===maxFloors?"Maximum-height":"Compact",
     concept:`A ${floors}-floor concept using the known parcel-level density and coverage limits.`,
     floors,
     total_floor_area_m2:Math.round(usableTotal),
     ground_footprint_m2:Math.round(footprint),
     floor_area_per_floor_m2:Math.round(usableTotal/floors),
     why_it_may_suit:floors===maxFloors?"Uses more vertical development and a smaller footprint.":floors<=2?"Keeps the building lower and spreads more area across the site.":"Balances footprint and building height.",
     key_caveat:"Setbacks, access, parking, use permissions and other detailed rules are not included in this concept."
   });
 });

 // Keep up to four distinct options, prioritising low / middle / max floors.
 const chosen=[];
 const picks=[floorSet[0], floorSet[Math.floor((floorSet.length-1)/2)], floorSet[floorSet.length-1]];
 [...new Set(picks)].forEach(f=>{
   const found=candidates.find(x=>x.floors===f);
   if(found)chosen.push(found);
 });
 for(const c of candidates){
   if(chosen.length>=4)break;
   if(!chosen.some(x=>x.floors===c.floors))chosen.push(c);
 }

 currentConfigurations=chosen;
 renderConfigurations(chosen);
}
async function loadSiteExtras(){
 if(!currentSite?.parcel_feature)return;
 const parcelId=currentSite.parcel?.parcel_id;
 const parcelFeature=currentSite.parcel_feature;
 $("terrain").innerHTML='<div class="muted">Loading building and terrain checks…</div>';
 $("spatial").innerHTML='<div class="muted">Loading site constraints…</div>';
 try{
   const r=await fetch("/api/site-extra",{
     method:"POST",
     headers:{"Content-Type":"application/json"},
     body:JSON.stringify({parcel_feature:parcelFeature})
   });
   const d=await r.json();
   if(currentSite?.parcel?.parcel_id!==parcelId)return;
   if(!r.ok)throw new Error(d.detail||"Site checks failed");

   currentSite={...currentSite,...d,_extrasLoaded:true};
   updateStickyParcel(currentSite);

   const buildings=currentSite.building_summary||{};
   const contours=currentSite.contour_summary||{};
   $("terrain").innerHTML=`<div class="grid">
     ${card("Mapped buildings",buildings.count||0,true)}
     ${card("Contour lines",contours.count||0)}
     ${card("Lowest contour",contours.min_elevation_m,false," m")}
     ${card("Highest contour",contours.max_elevation_m,false," m")}
     ${card("Elevation range",contours.elevation_range_m,false," m")}
   </div>
   <p class="muted">Map-layer intersections are indicative GIS checks from DLS map services.</p>`;

   const checks=currentSite.spatial_checks||{};
   const relevant=[];
   if(checks["31"]?.ok && checks["31"].features?.length)relevant.push("Coast protection overlap detected.");
   if(checks["32"]?.ok && checks["32"].features?.length)relevant.push("State land overlap detected.");
   const failed=Object.values(checks).filter(x=>x && !x.ok).length;
   $("spatial").innerHTML=relevant.length
     ? relevant.map(x=>`<div class="warning">${esc(x)}</div>`).join("")
     : '<div class="notice">No coast-protection or state-land overlap was detected in the queried map layers.</div>';
   if(failed)$("spatial").innerHTML+=`<p class="muted">${failed} map-layer check(s) could not be completed.</p>`;
 }catch(err){
   if(currentSite?.parcel?.parcel_id!==parcelId)return;
   currentSite._extrasLoaded=false;
   $("terrain").innerHTML=`<div class="warning">${esc(err.message||err)}</div>`;
   $("spatial").innerHTML='<div class="muted">Site constraints were not loaded. Close and reopen this section to retry.</div>';
 }
}

async function selectSite(lat,lon){
 const requestId=++selectionRequestId;
 currentPlanningAnalysis=null;
 $("loadTiming").textContent="";
 setLoadingStage(0);
 $("planning").innerHTML=planningSkeleton();
 $("planningIntelligence").innerHTML=planningSkeleton()+'<p class="muted">Waiting for parcel planning data…</p>';
 $("potential").innerHTML=planningSkeleton();
 $("configurations").innerHTML='<div class="muted">Waiting for planning data…</div>';
 $("configurationComparison").innerHTML="";
 ["geometry","terrain","spatial","warnings","valuation","registrations","units"].forEach(id=>$(id).innerHTML='<div class="muted">Loading…</div>');

 // STEP 1: fast parcel lookup — highlight and show identity immediately.
 let basicRes,basic;
 try{
   basicRes=await fetch(`/api/parcel-basic?lat=${lat}&lon=${lon}`);
   basic=await basicRes.json();
 }catch(err){
   if(requestId!==selectionRequestId)return;
   hideLoading();
   alert(String(err.message||err||"Parcel lookup failed"));
   return;
 }
 if(requestId!==selectionRequestId)return;
 if(!basicRes.ok){hideLoading();alert(basic.detail||"Parcel lookup failed");return}

 if(selected)map.removeLayer(selected);
 selected=L.geoJSON(basic.parcel_feature,{style:{color:"#ff7a00",weight:4,fillColor:"#ffb15c",fillOpacity:.26}}).addTo(map);
 map.fitBounds(selected.getBounds(),{padding:[25,25],maxZoom:19});

 currentSite={
   parcel_feature:basic.parcel_feature,
   parcel:{
     parcel_id:basic.parcel_id,
     parcel_number:basic.parcel_number,
     parcel_extent_m2:basic.map_geometry_extent_m2,
     sheet:basic.sheet,
     plan:basic.plan,
     block:basic.block
   },
   planning_zones:[],
   development_potential:{},
   structured_rule_analysis:null,
   geometry_metrics:basic.geometry_metrics||{},
   building_summary:{count:0,features:[]},
   contour_summary:{},
   spatial_checks:{},
   registration_summary:{},
   related_properties:[],
   warnings:[]
 };
 currentConfigurations=[];
 currentAiAnswer="";
 currentPlanningAnalysis=null;

 $("stickyParcel").classList.remove("empty");
 $("stickyParcelTitle").textContent=`Parcel ${basic.parcel_number??"—"}`;
 $("stickyParcelMeta").textContent=`${basic.map_geometry_extent_m2?Math.round(Number(basic.map_geometry_extent_m2))+" m²":"Loading official area…"}`;
 $("stickyParcelZoning").textContent="Loading planning data…";
 $("stickyFlags").innerHTML="";
 $("aiContext").textContent=`Asking about Parcel ${basic.parcel_number??"—"}`;
 $("aiAnswer").textContent="Planning data is still loading…";

 hideLoading();

 // STEP 2: load official parcel/planning details in background.
 try{
   const detailStart=performance.now();
   const detailsRes=await fetch(`/api/parcel-details?parcel_id=${basic.parcel_id}`);
   const details=await detailsRes.json();
   if(requestId!==selectionRequestId)return;
   $("loadTiming").textContent=`Planning data loaded in ${((performance.now()-detailStart)/1000).toFixed(1)}s`;
   if(!detailsRes.ok)throw new Error(details.detail||"Planning details failed");

   currentSite={...currentSite,...details,parcel_feature:basic.parcel_feature};
   currentSite.parcel={...details.parcel,map_geometry_extent_m2:basic.map_geometry_extent_m2};

   updateStickyParcel(currentSite);
   $("aiContext").textContent=`Asking about Parcel ${currentSite.parcel?.parcel_number||"—"} · ${currentSite.planning_zones?.[0]?.zone||"zone not returned"} · ${currentSite.parcel?.parcel_extent_m2||"—"} m²`;
   $("aiAnswer").textContent="Ask a planning question about this selected parcel.";

   $("planning").innerHTML=renderZones(currentSite.planning_zones);
   $("planningIntelligence").innerHTML=`${renderStructuredRuleHtml(currentSite.structured_rule_analysis)}<p class="muted">Preparing parcel-specific knowledge-base review…</p>`;

   const potential=currentSite.development_potential||{};
   const potentialWarnings=potential.calculation_warnings||[];
   $("potential").innerHTML=`<div class="grid">
     ${card("Maximum floor area",potential.theoretical_max_floor_area_m2,true," m²")}
     ${card("Maximum footprint",potential.theoretical_max_ground_coverage_m2,true," m²")}
   </div>
   ${potential.calculation_method==="weighted_zone_overlap"?`<p class="muted">Weighted across the DLS zone-overlap shares using an effective density of ${esc(potential.effective_density_percent)}% and effective coverage of ${esc(potential.effective_coverage_percent)}%.</p>`:""}
   <p class="muted">The current area basis is the DLS parcel extent. Order 4/2026 calculations ultimately rely on the applicable clean/net development area, so road, access or public-space commitments must be confirmed before treating these figures as final capacity.</p>
   ${potentialWarnings.map(x=>`<div class="warning">${esc(x)}</div>`).join("")}`;

   $("valuation").innerHTML=`<div class="grid">
     ${card("General valuation 1.1.2021",money(currentSite.parcel?.price_2021),true)}
     ${card("General valuation 1.1.2018",money(currentSite.parcel?.price_2018),true)}
     ${card("General valuation 1.1.1980",money(currentSite.parcel?.price_1980))}
     ${card("Change 2018 → 2021",present(currentSite.parcel?.valuation_change_percent)?(currentSite.parcel.valuation_change_percent>0?"+":"")+currentSite.parcel.valuation_change_percent+"%":"—")}
   </div>`;

   const reg=currentSite.registration_summary||{};
   $("registrations").innerHTML=`<div class="grid">
     ${card("Registered units",reg.total_related_records,true)}
     ${card("Total enclosed extent",reg.total_enclosed_extent_m2,false," m²")}
     ${card("Total covered extent",reg.total_covered_extent_m2,false," m²")}
     ${card("Total uncovered extent",reg.total_uncovered_extent_m2,false," m²")}
   </div>`;

   $("units").innerHTML=renderUnitTable(currentSite.related_properties||[]);

   const geom=currentSite.geometry_metrics||{};
   $("geometry").innerHTML=`<div class="grid">
     ${card("Approx. perimeter",geom.approx_perimeter_m,false," m")}
     ${card("Longest edge",geom.longest_edge_m,false," m")}
     ${card("Shortest edge",geom.shortest_edge_m,false," m")}
     ${card("Longest-edge orientation",present(geom.longest_edge_orientation)?`${geom.longest_edge_orientation} · ${geom.longest_edge_orientation_deg}°`:null)}
   </div>`;

   if(currentSite.warnings?.length){
     $("warningsWrap").style.display="block";
     $("warnings").innerHTML=currentSite.warnings.map(w=>`<div class="warning">${esc(w)}</div>`).join("");
   }else{
     $("warningsWrap").style.display="none";
     $("warnings").innerHTML="";
   }

   // Run one automatic evidence-grounded planning investigation in the background.
   loadPlanningIntelligence(requestId,basic.parcel_id);

   // Generate configurations instantly from confirmed planning limits.
   loadConfigurations();

   // Detailed GIS checks only load when the user opens "More site information".
   $("terrain").innerHTML='<div class="muted">Open this section to load building and terrain checks.</div>';
   $("spatial").innerHTML='<div class="muted">Open this section to load site constraints.</div>';
 }catch(err){
   if(requestId!==selectionRequestId)return;
   $("planning").innerHTML=`<div class="warning">Parcel found, but detailed planning data is taking too long or could not be loaded.</div>`;
   $("planningIntelligence").innerHTML='<div class="muted">Automatic planning intelligence requires the canonical parcel details.</div>';
   $("potential").innerHTML='<div class="muted">Detailed development potential is unavailable until planning data loads.</div>';
   $("configurations").innerHTML='<div class="muted">Configurations require planning data.</div>';
   $("aiAnswer").textContent="You can still ask a general planning question, but parcel-specific planning details are not fully loaded.";
 }
}
document.getElementById("moreDetails")?.addEventListener("toggle",e=>{
 if(e.target.open && currentSite && !currentSite._extrasLoaded){
   loadSiteExtras();
 }
});

map.on("click",e=>{
 if(map.getZoom()<15){alert("Zoom in further before selecting a parcel.");return}
 selectSite(e.latlng.lat,e.latlng.lng);
});

$("searchForm").addEventListener("submit",async e=>{
 e.preventDefault();
 const q=$("searchInput").value.trim();
 if(!q)return;
 const r=await fetch(`/api/geocode?q=${encodeURIComponent(q)}`);
 const d=await r.json();
 $("results").innerHTML="";
 if((d.results||[]).length)$("results").innerHTML='<div class="muted" style="margin:8px 2px 2px">Choose a result, then click the parcel you want to analyse.</div>';
 (d.results||[]).forEach(x=>{
   const b=document.createElement("button");
   b.className="result";b.type="button";b.textContent=x.display_name;
   b.onclick=()=>map.setView([x.lat,x.lon],18);
   $("results").appendChild(b);
 });
});
</script>
</body>
</html>
"""

CHAT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PLANA.CY</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f3f4f2;color:#17211b}
header{height:78px;padding:15px 26px;border-bottom:1px solid #dfe4e0;background:#fff;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0}
.eyebrow{font-size:11px;letter-spacing:.14em;font-weight:700;color:#68726c}.title{font-size:22px;font-weight:750;margin-top:4px}
.status{font-size:13px;color:#68726c}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#2f8f5b;margin-right:7px}
main{width:min(940px,100%);margin:auto;padding:42px 20px 180px}.welcome{text-align:center;max-width:700px;margin:40px auto}.icon{width:50px;height:50px;border-radius:14px;background:#173f2b;color:#fff;display:grid;place-items:center;margin:0 auto 18px;font-size:23px}
h1{font-size:31px;margin:0 0 10px}.muted{color:#68726c;line-height:1.6}.examples{display:grid;gap:10px;margin-top:26px}
.example{border:1px solid #dfe4e0;background:#fff;border-radius:14px;padding:14px 16px;text-align:left;cursor:pointer}
.row{display:flex;margin:22px 0}.user{justify-content:flex-end}.bubble{max-width:72%;background:#173f2b;color:#fff;border-radius:18px 18px 4px 18px;padding:13px 16px;line-height:1.5}
.card{width:100%;background:#fff;border:1px solid #dfe4e0;border-radius:18px;padding:22px;box-shadow:0 8px 28px rgba(21,44,30,.05)}
.label{color:#173f2b;font-size:11px;font-weight:800;letter-spacing:.12em;margin-bottom:14px}.answer{line-height:1.7;font-size:15.5px;white-space:pre-wrap}
details{margin-top:18px;border-top:1px solid #dfe4e0;padding-top:14px}summary{cursor:pointer;color:#68726c;font-weight:600}.source{background:#e9f0eb;border:1px solid #d7e2da;border-radius:12px;padding:12px 14px;margin-top:10px}.source-title{font-weight:700}.source-meta{font-size:12px;color:#68726c;margin-top:5px}
.composer-wrap{position:fixed;left:0;right:0;bottom:0;padding:18px 20px 14px;background:linear-gradient(to top,#f3f4f2 72%,rgba(243,244,242,0))}
form{width:min(900px,calc(100% - 36px));margin:auto;background:#fff;border:1px solid #cfd8d1;border-radius:18px;padding:10px 10px 10px 16px;display:flex;gap:10px;align-items:flex-end;box-shadow:0 12px 34px rgba(20,42,28,.1)}
textarea{flex:1;border:0;resize:none;outline:none;min-height:42px;max-height:180px;padding:10px 2px;font:inherit;line-height:1.45}
button.send{border:0;background:#173f2b;color:#fff;border-radius:12px;padding:11px 18px;font-weight:700;cursor:pointer}button:disabled{opacity:.55}
.note{width:min(900px,calc(100% - 36px));margin:8px auto 0;text-align:center;font-size:11px;color:#68726c}
.error{color:#9b2c2c}
@media(max-width:700px){header{padding:14px 16px}.bubble{max-width:88%}.card{padding:17px}}
</style>
</head>
<body>
<header>
  <div><div class="eyebrow">PLANA.CY</div><div class="title">PLANA.CY</div></div>
  <div style="display:flex;align-items:center;gap:14px"><a href="/" style="text-decoration:none;color:#173f2b;font-weight:700">Site Explorer</a><div class="status"><span class="dot"></span><span id="statusText">Checking…</span></div></div>
</header>

<main id="messages">
  <section class="welcome" id="welcome">
    <div class="icon">⌂</div>
    <h1>Ask a planning question</h1>
    <p class="muted">Ask in English or Greek. Answers are grounded in the planning documents loaded in the knowledge base.</p>
    <div class="examples">
      <button class="example">Does a basement count toward the building coefficient?</button>
      <button class="example">How many parking spaces are required for a house?</button>
      <button class="example">Πώς μετριέται το ύψος σε επικλινές έδαφος;</button>
    </div>
  </section>
</main>

<div class="composer-wrap">
  <form id="form">
    <textarea id="input" rows="1" placeholder="Ask about Cyprus planning regulations…"></textarea>
    <button class="send" id="send" type="submit">Ask</button>
  </form>
  <div class="note">Research assistant only. Verify critical decisions against the official applicable planning instruments.</div>
</div>

<script>
const messages=document.getElementById("messages");
const form=document.getElementById("form");
const input=document.getElementById("input");
const send=document.getElementById("send");

function addUser(text){
  const row=document.createElement("div");row.className="row user";
  const bubble=document.createElement("div");bubble.className="bubble";bubble.textContent=text;
  row.appendChild(bubble);messages.appendChild(row);
}

function addLoading(){
  const row=document.createElement("div");row.className="row";
  row.innerHTML='<div class="card"><div class="label">PLANNING AI</div><div class="answer">Searching planning sources and checking the answer…</div></div>';
  messages.appendChild(row);return row;
}

function addAssistant(data){
  const row=document.createElement("div");row.className="row";
  const card=document.createElement("div");card.className="card";
  const label=document.createElement("div");label.className="label";label.textContent="PLANNING AI";
  const answer=document.createElement("div");answer.className="answer";answer.textContent=data.answer;
  card.append(label,answer);

  if(data.sources && data.sources.length){
    const details=document.createElement("details");
    const summary=document.createElement("summary");summary.textContent="Sources used";
    details.appendChild(summary);
    const seen=new Set();
    data.sources.forEach(s=>{
      const key=s.title+"|"+s.page_number;if(seen.has(key))return;seen.add(key);
      const box=document.createElement("div");box.className="source";
      const t=document.createElement("div");t.className="source-title";t.textContent=s.title;
      const m=document.createElement("div");m.className="source-meta";
      const parts=[];if(s.page_number!=null)parts.push("PDF page "+s.page_number);if(s.section_title)parts.push(s.section_title);if(s.publication_date)parts.push(s.publication_date);
      m.textContent=parts.join(" · ");box.append(t,m);details.appendChild(box);
    });
    card.appendChild(details);
  }
  row.appendChild(card);messages.appendChild(row);
}

async function ask(q){
  q=q.trim();if(!q)return;
  const welcome=document.getElementById("welcome");if(welcome)welcome.remove();
  addUser(q);input.value="";send.disabled=true;
  const loading=addLoading();window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"});
  try{
    const r=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:q})});
    const data=await r.json();loading.remove();
    if(!r.ok)throw new Error(data.detail||"Request failed");
    addAssistant(data);
  }catch(e){
    loading.remove();
    const row=document.createElement("div");row.className="row";
    row.innerHTML='<div class="card error">Could not get an answer: '+String(e.message)+'</div>';
    messages.appendChild(row);
  }finally{
    send.disabled=false;input.focus();window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"});
  }
}

form.addEventListener("submit",e=>{e.preventDefault();ask(input.value)});
input.addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();form.requestSubmit()}});
document.querySelectorAll(".example").forEach(b=>b.addEventListener("click",()=>ask(b.textContent)));

fetch("/health").then(r=>r.json()).then(d=>document.getElementById("statusText").textContent="Online · "+d.chunks_loaded+" chunks").catch(()=>document.getElementById("statusText").textContent="Offline");
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def homepage() -> HTMLResponse:
    return HTMLResponse(SITE_HTML)


@app.get("/chat", response_class=HTMLResponse)
def chat_page() -> HTMLResponse:
    return HTMLResponse(CHAT_HTML)
