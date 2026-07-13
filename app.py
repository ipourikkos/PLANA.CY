import json
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
            "Ισχύουσες νεότερες πρόνοιες 2025, Εντολή 4/2024, "
            "τρέχοντες κανόνες και ειδικές εξαιρέσεις."
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
- When a 2025 source and a 2011 source differ, do not silently follow the 2011 source.
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

Check especially for SYNTHESIS ERRORS:
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
4. Write the prose in {target_language}.
5. Every citation must include the FULL document title, for example:
   [Document title, p. X] or [Document title, pp. X–Y]
   Never shorten citations to [p. X], [pp. X–Y], [σ. X], or [σσ. X–Y].
6. Preserve Greek document titles inside citations even when the answer is in English.
7. Do not mention that you reviewed or corrected a draft.
8. End with exactly this note:
   {required_note}
9. Return ONLY the final answer to the user.
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

    print(f"Cyprus Planning AI v11 — model: {ANSWER_MODEL}")
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
        f"Cyprus Planning AI ready — loaded "
        f"{len(state['all_rows'])} knowledge-base chunks."
    )

    yield
    state.clear()


app = FastAPI(
    title="Cyprus Planning AI",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "chunks_loaded": len(state.get("all_rows", [])),
        "model": ANSWER_MODEL,
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
            detail=f"Planning AI request failed: {exc}",
        ) from exc




# ==================== DLS SITE EXPLORER ====================
DLS_MAPSERVER = "https://eservices.dls.moi.gov.cy/arcgis/rest/services/National/CadastralMap_EN/MapServer"
PARCEL_QUERY = f"{DLS_MAPSERVER}/0/query"
GENERAL_IDENTIFY = "https://eservices.dls.moi.gov.cy/Services/Rest/Info/GeneralParcelIdentify"
NOMINATIM = "https://nominatim.openstreetmap.org/search"


GEOCODE_CACHE: dict[str, list[dict[str, Any]]] = {}

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
        "User-Agent": "CyprusDLSSiteExplorer/1.0",
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
        "User-Agent": "Mozilla/5.0 CyprusDLSSiteExplorer/1.0",
    }

    async with httpx.AsyncClient(timeout=75.0, follow_redirects=True) as client:
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
        async with httpx.AsyncClient(timeout=40.0) as client:
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


@app.get("/api/site")
async def site(
    lat: float = Query(ge=34.0, le=36.0),
    lon: float = Query(ge=31.0, le=35.0),
):
    parcel_feature = await get_parcel_at_point(lat, lon)
    map_props = parcel_feature.get("properties", {})

    sbpi = map_props.get("SBPI_ID_NO")
    if sbpi is None:
        raise HTTPException(status_code=502, detail="DLS parcel did not return SBPI_ID_NO.")

    try:
        sbpi = int(sbpi)
    except Exception:
        raise HTTPException(status_code=502, detail=f"Unexpected SBPI_ID_NO: {sbpi}")

    records = await get_general_identify(sbpi)
    if not isinstance(records, list) or not records:
        raise HTTPException(status_code=502, detail="DLS Identify returned no records.")

    parcel = pick_parcel_record(records, sbpi)
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

    def safe_sum(values):
        nums = []
        for v in values:
            try:
                if v not in (None, ""):
                    nums.append(float(v))
            except Exception:
                pass
        return round(sum(nums), 2) if nums else None

    parcel_area = parcel.get("PrParcelExtent")
    max_floor_area = None
    max_ground_coverage = None

    try:
        area = float(parcel_area)
        if zones:
            if len(zones) == 1:
                z = zones[0]
                if z.get("density_percent") is not None:
                    max_floor_area = round(area * float(z["density_percent"]) / 100, 2)
                if z.get("coverage_percent") is not None:
                    max_ground_coverage = round(area * float(z["coverage_percent"]) / 100, 2)
            else:
                floor_total = 0.0
                cov_total = 0.0
                floor_ok = cov_ok = False
                for z in zones:
                    overlap = z.get("overlap_percent")
                    if overlap is None:
                        continue
                    affected_area = area * float(overlap) / 100
                    if z.get("density_percent") is not None:
                        floor_total += affected_area * float(z["density_percent"]) / 100
                        floor_ok = True
                    if z.get("coverage_percent") is not None:
                        cov_total += affected_area * float(z["coverage_percent"]) / 100
                        cov_ok = True
                if floor_ok:
                    max_floor_area = round(floor_total, 2)
                if cov_ok:
                    max_ground_coverage = round(cov_total, 2)
    except Exception:
        pass

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

    geometry_metrics = polygon_geometry_metrics(parcel_feature)

    spatial_checks = {}
    for layer_id, layer_name in SPECIAL_LAYERS.items():
        result = await query_layer_intersections(layer_id, parcel_feature)
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

    warnings = []
    if len(zones) > 1:
        warnings.append("Parcel is affected by multiple planning zones.")
    if any(z.get("remarks") for z in zones):
        warnings.append("One or more planning-zone remarks apply.")
    if related:
        warnings.append(f"Parcel has {len(related)} related registered properties or units.")
    if buildings:
        warnings.append(f"{len(buildings)} DLS building feature(s) intersect the parcel.")
    if bool(parcel.get("PrIsPreserved")):
        warnings.append("Property is marked as preserved.")
    if bool(parcel.get("PrIsAncient")):
        warnings.append("Property is marked as ancient.")
    if bool(parcel.get("PrIsCommonProperty")):
        warnings.append("Property is marked as common property.")

    for lid in ("31", "32"):
        check = spatial_checks.get(lid, {})
        if check.get("ok") and check.get("features"):
            warnings.append(f"Parcel intersects {check['layer_name']}.")

    parcel_summary = {
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
        "map_geometry_extent_m2": map_props.get("Parcel Extend") or map_props.get("SHAPE.STArea()"),
        "price_2021": value_2021,
        "price_2018": value_2018,
        "price_1980": parcel.get("PrPriceBase3"),
        "valuation_change_percent": valuation_change_percent,
        "is_preserved": bool(parcel.get("PrIsPreserved")),
        "is_ancient": bool(parcel.get("PrIsAncient")),
        "is_common_property": bool(parcel.get("PrIsCommonProperty")),
    }

    return {
        "parcel_feature": parcel_feature,
        "parcel": parcel_summary,
        "planning_zones": zones,
        "development_potential": {
            "theoretical_max_floor_area_m2": max_floor_area,
            "theoretical_max_ground_coverage_m2": max_ground_coverage,
        },
        "geometry_metrics": geometry_metrics,
        "building_summary": {
            "count": len(buildings),
            "features": buildings,
        },
        "contour_summary": {
            "count": len(contour_values),
            "min_elevation_m": min(contour_values) if contour_values else None,
            "max_elevation_m": max(contour_values) if contour_values else None,
            "elevation_range_m": round(max(contour_values) - min(contour_values), 2) if len(contour_values) >= 2 else None,
            "values_m": sorted(set(contour_values)),
        },
        "spatial_checks": spatial_checks,
        "registration_summary": {
            "total_related_records": len(related),
            "by_type": dict(type_counter),
            "total_enclosed_extent_m2": safe_sum(enclosed_vals),
            "total_covered_extent_m2": safe_sum(covered_vals),
            "total_uncovered_extent_m2": safe_sum(uncovered_vals),
        },
        "related_properties": related,
        "warnings": warnings,
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
- Do not invent missing parcel facts.
- Use the planning-regulation knowledge base for legal/planning rules.
- Distinguish official DLS facts, platform calculations, user assumptions, and planning interpretation.
- Where the answer depends on missing facts, say exactly what is missing.
""".strip()

    result = chat(ChatRequest(question=parcel_prompt))
    return {
        "answer": result.get("answer"),
        "sources": result.get("sources", []),
        "language": result.get("language"),
    }


SITE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cyprus DLS Site Explorer V10</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{--ink:#17211b;--green:#173f2b;--muted:#68726c;--line:#dfe5e0;--bg:#f4f5f3;--card:#f7f8f7;--warn:#fff4dc}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:var(--ink);background:var(--bg)}
header{min-height:72px;padding:14px 20px;background:#fff;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between}
h1{font-size:20px;margin:2px 0 0}.eyebrow{font-size:10px;font-weight:800;letter-spacing:.14em;color:var(--muted)}
.layout{display:grid;grid-template-columns:540px 1fr;height:calc(100vh - 72px)}
aside{background:#fff;border-right:1px solid var(--line);padding:16px;overflow:auto}#map{height:100%}
form{display:flex;gap:8px}.search{flex:1;padding:12px;border:1px solid #ccd4ce;border-radius:11px;font:inherit}
button{border:0;border-radius:11px;background:var(--green);color:#fff;padding:11px 14px;font-weight:750;cursor:pointer}.secondary{background:#edf3ef;color:var(--green)}
.result{width:100%;display:block;margin-top:7px;text-align:left;background:#edf3ef;color:var(--ink)}
.section{margin-top:20px;padding-top:17px;border-top:1px solid var(--line)}.section h2{font-size:16px;margin:0 0 10px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.card{background:var(--card);border:1px solid #e5e9e6;border-radius:12px;padding:11px}
.label{font-size:10px;letter-spacing:.07em;text-transform:uppercase;font-weight:800;color:var(--muted)}.value{font-weight:780;margin-top:4px;word-break:break-word}.big{font-size:26px;color:var(--green)}
.zone{border:1px solid var(--line);border-radius:15px;padding:14px;margin-bottom:12px}.zone-title{font-size:31px;font-weight:850;color:var(--green)}
.badge{display:inline-block;margin-top:8px;background:#e8f0ea;color:var(--green);font-size:11px;font-weight:800;padding:5px 8px;border-radius:999px}
.muted{font-size:12px;color:var(--muted);line-height:1.5}.notice{background:#e9f6ec;border:1px solid #bbdec3;border-radius:10px;padding:10px;font-size:12px}
.warning{background:var(--warn);border:1px solid #ead39d;border-radius:10px;padding:10px;font-size:12px;margin-top:7px}
.summary-pills{display:flex;gap:7px;flex-wrap:wrap}.pill{background:#edf3ef;color:var(--green);padding:6px 9px;border-radius:999px;font-size:12px;font-weight:750}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px}table{width:100%;border-collapse:collapse;font-size:12px;min-width:900px}
th,td{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:#f3f5f3;font-size:10px;text-transform:uppercase}
.check{padding:8px 0;border-bottom:1px solid var(--line);font-size:12px}
.scenario-form{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.scenario-form label{font-size:11px;font-weight:700;color:var(--muted)}
.scenario-form input,.scenario-form select,.scenario-form textarea{width:100%;margin-top:4px;padding:10px;border:1px solid #ccd4ce;border-radius:9px;font:inherit;background:#fff}
.full{grid-column:1/-1}
.ai-box{border:1px solid var(--line);border-radius:12px;padding:12px;background:#fafbfa}
.ai-answer{white-space:pre-wrap;line-height:1.55;font-size:13px}
.report-block{border:1px solid var(--line);border-radius:12px;padding:12px;background:#fff}
.report-title{font-size:22px;font-weight:850;margin-bottom:4px}
@media(max-width:900px){.layout{grid-template-columns:1fr;height:auto}#map{height:65vh}}
@media print{.layout{display:block;height:auto}aside{border:0;overflow:visible}#map,form,#results,.secondary{display:none!important}.section{break-inside:avoid}.table-wrap{overflow:visible}table{min-width:0;font-size:9px}}
</style>
</head>
<body>
<header>
<div><div class="eyebrow">DLS SITE INTELLIGENCE REPORT</div><h1>Cyprus Site Explorer V10</h1></div>
<button class="secondary" onclick="window.print()">Print / Save PDF</button>
</header>

<div class="layout">
<aside>
<form id="searchForm"><input id="searchInput" class="search" placeholder="Search address in Cyprus"><button>Search</button></form>
<div id="results"></div>

<section class="section"><h2>Overview</h2><div id="overview" class="muted">Search, zoom in and click a parcel.</div></section>
<section class="section"><h2>Planning</h2><div id="planning" class="muted">No parcel selected.</div></section>
<section class="section"><h2>Development potential</h2><div id="potential" class="muted">No parcel selected.</div></section>
<section class="section">
<h2>Development scenario</h2>
<div class="scenario-form">
<label>Proposed use<select id="scenarioUse"><option>Residential</option><option>Commercial</option><option>Mixed use</option><option>Other</option></select></label>
<label>Units<input id="scenarioUnits" type="number" min="0" placeholder="e.g. 8"></label>
<label>Proposed floors<input id="scenarioFloors" type="number" min="0" placeholder="e.g. 3"></label>
<label>Proposed floor area (m²)<input id="scenarioArea" type="number" min="0" step="0.1" placeholder="e.g. 620"></label>
<label>Basement<select id="scenarioBasement"><option value="">Not specified</option><option>Yes</option><option>No</option></select></label>
<label>Pool<select id="scenarioPool"><option value="">Not specified</option><option>Yes</option><option>No</option></select></label>
<label>Parking spaces<input id="scenarioParking" type="number" min="0" placeholder="e.g. 12"></label>
<label class="full">Notes<textarea id="scenarioNotes" rows="3" placeholder="Any project assumptions"></textarea></label>
</div>
<div id="scenarioCheck" style="margin-top:10px" class="muted">Enter a scenario after selecting a parcel.</div>
</section>
<section class="section">
<h2>Ask AI about this parcel</h2>
<div class="ai-box">
<textarea id="aiQuestion" rows="4" style="width:100%;padding:10px;border:1px solid #ccd4ce;border-radius:9px;font:inherit" placeholder="e.g. Can I build 8 apartments with a basement on this parcel?"></textarea>
<button id="askAiBtn" type="button" style="margin-top:8px;width:100%">Ask planning AI</button>
<div id="aiAnswer" class="ai-answer muted" style="margin-top:12px">Select a parcel first.</div>
</div>
</section>
<section class="section">
<h2>Feasibility report</h2>
<button id="generateReportBtn" type="button" class="secondary" style="width:100%">Generate report summary</button>
<div id="report" class="muted" style="margin-top:10px">No report generated.</div>
</section>
<section class="section"><h2>Parcel geometry</h2><div id="geometry" class="muted">No parcel selected.</div></section>
<section class="section"><h2>Buildings & terrain</h2><div id="terrain" class="muted">No parcel selected.</div></section>
<section class="section"><h2>Spatial checks</h2><div id="spatial" class="muted">No parcel selected.</div></section>
<section class="section"><h2>Warnings</h2><div id="warnings" class="muted">No parcel selected.</div></section>
<section class="section"><h2>DLS General Valuation</h2><div id="valuation" class="muted">No parcel selected.</div></section>
<section class="section"><h2>Registrations on parcel</h2><div id="registrations" class="muted">No parcel selected.</div></section>
<section class="section"><h2>All registered units</h2><div id="units" class="muted">No parcel selected.</div></section>
</aside>
<div id="map"></div>
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

function renderUnitTable(rows){
 if(!rows?.length)return '<div class="muted">No related registered units returned.</div>';
 return `<div class="table-wrap"><table><thead><tr><th>Registration</th><th>Type</th><th>Plan</th><th>Floor</th><th>2021</th><th>2018</th><th>1980</th><th>Enclosed</th><th>Covered</th><th>Uncovered</th></tr></thead><tbody>
 ${rows.map(x=>`<tr><td>${esc(present(x.registration_block)&&present(x.registration_no)?x.registration_block+"/"+x.registration_no:x.registration_no)}</td><td>${esc(x.kind||x.property_type)}</td><td>${esc(x.plan_no)}</td><td>${esc(x.unit_floor_no)}</td><td>${esc(money(x.price_2021))}</td><td>${esc(money(x.price_2018))}</td><td>${esc(money(x.price_1980))}</td><td>${esc(x.enclosed_extent)}</td><td>${esc(x.covered_extent)}</td><td>${esc(x.uncovered_extent)}</td></tr>`).join("")}
 </tbody></table></div>`;
}


function getScenario(){
 return {
  use:$("scenarioUse").value,
  units:$("scenarioUnits").value?Number($("scenarioUnits").value):null,
  proposed_floors:$("scenarioFloors").value?Number($("scenarioFloors").value):null,
  proposed_floor_area_m2:$("scenarioArea").value?Number($("scenarioArea").value):null,
  basement:$("scenarioBasement").value||null,
  pool:$("scenarioPool").value||null,
  parking_spaces:$("scenarioParking").value?Number($("scenarioParking").value):null,
  notes:$("scenarioNotes").value.trim()||null
 };
}

function updateScenarioCheck(){
 if(!currentSite){$("scenarioCheck").innerHTML='<div class="muted">Select a parcel first.</div>';return}
 const s=getScenario();
 const maxA=currentSite.development_potential?.theoretical_max_floor_area_m2;
 const maxF=Math.max(...(currentSite.planning_zones||[]).map(z=>Number(z.max_floors)||0),0);
 const warnings=[];
 const checks=[];
 if(s.proposed_floor_area_m2!=null && maxA!=null){
   const diff=s.proposed_floor_area_m2-maxA;
   if(diff>0) warnings.push(`Proposed floor area exceeds the theoretical maximum by ${diff.toFixed(1)} m².`);
   else checks.push(`Proposed floor area is ${(maxA-s.proposed_floor_area_m2).toFixed(1)} m² below the theoretical maximum.`);
 }
 if(s.proposed_floors!=null && maxF){
   if(s.proposed_floors>maxF) warnings.push(`Proposed ${s.proposed_floors} floors exceed the DLS zone limit of ${maxF}.`);
   else checks.push(`Proposed floor count does not exceed the DLS zone maximum of ${maxF}.`);
 }
 if(!warnings.length && !checks.length){
   $("scenarioCheck").innerHTML='<div class="muted">Add proposed floor area or floors for an instant comparison.</div>';
   return;
 }
 $("scenarioCheck").innerHTML=[
   ...warnings.map(x=>`<div class="warning">${esc(x)}</div>`),
   ...checks.map(x=>`<div class="notice" style="margin-top:7px">${esc(x)}</div>`)
 ].join("");
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
  warnings:currentSite.warnings
 };
}

function generateReport(){
 if(!currentSite){$("report").innerHTML='<div class="warning">Select a parcel first.</div>';return}
 const p=currentSite.parcel;
 const zones=(currentSite.planning_zones||[]).map(z=>`${z.zone}: ${z.density_percent}% density, ${z.coverage_percent}% coverage, ${z.max_floors} floors, ${z.max_height_m} m`).join("<br>");
 const s=getScenario();
 $("report").innerHTML=`<div class="report-block">
 <div class="report-title">Site Feasibility Summary</div>
 <div class="muted">Parcel ${esc(p.parcel_number)} · ${esc(p.district)} · ${esc(p.municipality)}</div>
 <hr style="border:0;border-top:1px solid var(--line);margin:12px 0">
 <b>Parcel</b><br>${esc(p.parcel_extent_m2)} m² · Sheet ${esc(p.sheet)} / Plan ${esc(p.plan)} · Block ${esc(p.block)}<br><br>
 <b>Planning</b><br>${zones||"No planning data"}<br><br>
 <b>Calculated potential</b><br>
 Theoretical max floor area: ${esc(currentSite.development_potential?.theoretical_max_floor_area_m2)} m²<br>
 Theoretical max ground coverage: ${esc(currentSite.development_potential?.theoretical_max_ground_coverage_m2)} m²<br><br>
 <b>Scenario</b><br>${esc(JSON.stringify(s))}<br><br>
 <b>Warnings</b><br>${(currentSite.warnings||[]).map(esc).join("<br>")||"None generated"}
 ${currentAiAnswer?`<br><br><b>AI planning summary</b><div class="ai-answer">${esc(currentAiAnswer)}</div>`:""}
 <p class="muted">Official DLS facts, platform calculations, user assumptions and AI interpretation should be reviewed separately before relying on this report for a formal planning decision.</p>
 </div>`;
}

["scenarioUse","scenarioUnits","scenarioFloors","scenarioArea","scenarioBasement","scenarioPool","scenarioParking","scenarioNotes"]
.forEach(id=>document.addEventListener("input",e=>{if(e.target&&e.target.id===id)updateScenarioCheck()}));

document.addEventListener("click",async e=>{
 if(e.target?.id==="askAiBtn"){
   if(!currentSite){$("aiAnswer").textContent="Select a parcel first.";return}
   const q=$("aiQuestion").value.trim();
   if(!q){$("aiAnswer").textContent="Enter a question first.";return}
   $("aiAnswer").textContent="Asking the planning AI…";
   try{
     const r=await fetch("/api/parcel-ai",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
       question:q,parcel_context:buildParcelContext(),scenario:getScenario()
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


async function selectSite(lat,lon){
 ["overview","planning","potential","geometry","terrain","spatial","warnings","valuation","registrations","units"]
 .forEach(id=>$(id).innerHTML='<div class="muted">Loading parcel and parcel-wide DLS checks…</div>');

 const r=await fetch(`/api/site?lat=${lat}&lon=${lon}`);
 const d=await r.json();
 if(!r.ok){alert(d.detail||"Lookup failed");return}
 currentSite=d;
 currentAiAnswer='';
 $("aiAnswer").textContent='Ask a planning question about this selected parcel.';
 updateScenarioCheck();

 if(selected)map.removeLayer(selected);
 selected=L.geoJSON(d.parcel_feature,{style:{color:"#ff7a00",weight:4,fillColor:"#ffb15c",fillOpacity:.26}}).addTo(map);
 map.fitBounds(selected.getBounds(),{padding:[25,25],maxZoom:19});

 const p=d.parcel;
 $("overview").innerHTML=`<div class="notice">Official DLS parcel data combined with parcel-wide DLS map-layer checks.</div>
 <div class="summary-pills" style="margin-top:10px"><div class="pill">Parcel ${esc(p.parcel_number)}</div><div class="pill">${esc(p.parcel_extent_m2)} m²</div><div class="pill">${esc(p.district)}</div><div class="pill">${esc(p.quarter)}</div></div>
 <div class="grid" style="margin-top:10px">${card("Parcel number",p.parcel_number)}${card("Official parcel area",p.parcel_extent_m2,false," m²")}${card("Map geometry area",p.map_geometry_extent_m2,false," m²")}${card("District",p.district)}${card("Municipality / community",p.municipality)}${card("Quarter",p.quarter)}${card("Postal code",p.postal_code)}${card("Sheet",p.sheet)}${card("Plan",p.plan)}${card("Block",p.block)}${card("Scale",p.scale)}${card("Registration no.",p.registration_number)}</div>`;

 $("planning").innerHTML=renderZones(d.planning_zones);

 $("potential").innerHTML=`<div class="grid">${card("Theoretical max floor area",d.development_potential.theoretical_max_floor_area_m2,true," m²")}${card("Theoretical max ground coverage",d.development_potential.theoretical_max_ground_coverage_m2,true," m²")}</div>
 <p class="muted">Calculated from DLS parcel area and planning coefficients. These are theoretical planning indicators, not guaranteed development rights.</p>`;

 const g=d.geometry_metrics||{};
 $("geometry").innerHTML=`<div class="grid">${card("Approx. perimeter",g.approx_perimeter_m,false," m")}${card("Longest edge",g.longest_edge_m,false," m")}${card("Shortest edge",g.shortest_edge_m,false," m")}${card("Longest-edge orientation",present(g.longest_edge_orientation)?g.longest_edge_orientation+" · "+g.longest_edge_orientation_deg+"°":"—")}${card("Centroid latitude",g.centroid_lat)}${card("Centroid longitude",g.centroid_lon)}</div>
 <p class="muted">Geometry metrics are calculated by the platform from the DLS parcel polygon and are approximate.</p>`;

 const b=d.building_summary||{};
 const c=d.contour_summary||{};
 $("terrain").innerHTML=`<div class="grid">${card("DLS building features",b.count,true)}${card("Contour lines intersecting parcel",c.count,true)}${card("Minimum contour elevation",c.min_elevation_m,false," m")}${card("Maximum contour elevation",c.max_elevation_m,false," m")}${card("Approx. elevation range",c.elevation_range_m,false," m")}</div>
 ${b.features?.length?`<div class="summary-pills" style="margin-top:10px">${b.features.map(x=>`<div class="pill">${esc(x.building_description||"Building")} ${present(x.building_code)?"· code "+esc(x.building_code):""}</div>`).join("")}</div>`:""}
 ${c.values_m?.length?`<p class="muted"><b>Contour values:</b> ${c.values_m.map(esc).join(", ")} m</p>`:""}`;

 $("spatial").innerHTML=Object.entries(d.spatial_checks||{}).map(([id,x])=>{
   const count=x.ok?(x.features||[]).length:null;
   return `<div class="check"><b>${esc(x.layer_name)}</b><br><span class="muted">${x.ok?`${count} intersecting feature(s)`:`Check unavailable`}</span></div>`;
 }).join("");

 $("warnings").innerHTML=d.warnings?.length?d.warnings.map(w=>`<div class="warning">${esc(w)}</div>`).join(""):'<div class="muted">No automatic warnings generated.</div>';

 $("valuation").innerHTML=`<div class="grid">${card("General valuation 1.1.2021",money(p.price_2021),true)}${card("General valuation 1.1.2018",money(p.price_2018),true)}${card("General valuation 1.1.1980",money(p.price_1980))}${card("Change 2018 → 2021",present(p.valuation_change_percent)?(p.valuation_change_percent>0?"+":"")+p.valuation_change_percent+"%":"—")}</div>
 <p class="muted">DLS general valuation values are for taxation and fee purposes and are not market valuations.</p>`;

 const reg=d.registration_summary;
 $("registrations").innerHTML=`<div class="grid">${card("Total related registrations",reg.total_related_records,true)}${card("Total enclosed extent",reg.total_enclosed_extent_m2,false," m²")}${card("Total covered extent",reg.total_covered_extent_m2,false," m²")}${card("Total uncovered extent",reg.total_uncovered_extent_m2,false," m²")}</div>
 <div class="summary-pills" style="margin-top:10px">${Object.entries(reg.by_type||{}).map(([k,v])=>`<div class="pill">${esc(k)}: ${esc(v)}</div>`).join("")}</div>`;

 $("units").innerHTML=renderUnitTable(d.related_properties);
}

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
<title>Cyprus Planning AI</title>
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
  <div><div class="eyebrow">CYPRUS PLANNING INTELLIGENCE</div><div class="title">Cyprus Planning AI</div></div>
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
