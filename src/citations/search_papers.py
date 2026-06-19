"""Semantic Scholar citation search and BibTeX generation.

Usage:
    python -m src.citations.search_papers "UVM coverage closure"
    python -m src.citations.search_papers "LLM hardware verification" --year 2022- --limit 20
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

load_dotenv()

S2_API_KEY = os.getenv("S2_API_KEY", "")
BASE_URL = "https://api.semanticscholar.org/graph/v1"
DEFAULT_FIELDS = (
    "paperId,title,year,authors,venue,externalIds,"
    "citationCount,abstract,publicationTypes,journal,publicationDate"
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BIB_PATH = PROJECT_ROOT / "docs_latex" / "referinte.bib"


def _build_session() -> Session:
    session = Session()
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=5,
                backoff_factor=2.0,
                backoff_jitter=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods={"GET", "POST"},
            )
        ),
    )
    return session


def search_papers(
    query: str,
    *,
    limit: int = 10,
    year: str | None = None,
    fields_of_study: str | None = None,
    publication_types: str | None = None,
    fields: str = DEFAULT_FIELDS,
) -> list[dict[str, Any]]:
    """Search Semantic Scholar for papers matching *query*."""
    session = _build_session()
    params: dict[str, Any] = {
        "query": query,
        "fields": fields,
        "limit": min(limit, 100),
    }
    if year:
        params["year"] = year
    if fields_of_study:
        params["fieldsOfStudy"] = fields_of_study
    if publication_types:
        params["publicationTypes"] = publication_types

    headers: dict[str, str] = {}
    if S2_API_KEY:
        headers["X-API-KEY"] = S2_API_KEY

    results: list[dict[str, Any]] = []
    offset = 0

    while len(results) < limit:
        params["offset"] = offset
        params["limit"] = min(limit - len(results), 100)

        rsp = session.get(f"{BASE_URL}/paper/search", headers=headers, params=params)
        rsp.raise_for_status()
        data = rsp.json()

        papers = data.get("data", [])
        if not papers:
            break
        results.extend(papers)

        if "next" not in data or data.get("next", 0) >= data.get("total", 0):
            break
        offset = data["next"]

        # Respect rate limits — S2 aggressively rate-limits even with key
        time.sleep(3.0 if not S2_API_KEY else 1.5)

    return results[:limit]


def get_paper(paper_id: str, fields: str = DEFAULT_FIELDS) -> dict[str, Any]:
    """Fetch a single paper by ID (S2 paperId, DOI:xxx, ArXiv:xxx, CorpusID:xxx)."""
    session = _build_session()
    headers: dict[str, str] = {}
    if S2_API_KEY:
        headers["X-API-KEY"] = S2_API_KEY

    rsp = session.get(
        f"{BASE_URL}/paper/{paper_id}",
        headers=headers,
        params={"fields": fields},
    )
    rsp.raise_for_status()
    return rsp.json()


def _make_citation_key(paper: dict[str, Any]) -> str:
    """Generate a citation key: firstauthorlastname + year + first meaningful word."""
    authors = paper.get("authors", [])
    if authors:
        name = authors[0].get("name", "unknown")
        last_name = name.split()[-1].lower() if name else "unknown"
    else:
        last_name = "unknown"

    year = paper.get("year", "")

    title = paper.get("title", "")
    # Remove common short words to find a meaningful keyword
    stop_words = {
        "a",
        "an",
        "the",
        "of",
        "for",
        "in",
        "on",
        "to",
        "and",
        "with",
        "using",
        "based",
        "from",
    }
    words = re.findall(r"[a-zA-Z]+", title.lower())
    keyword = next((w for w in words if w not in stop_words and len(w) > 2), "paper")

    return f"{last_name}{year}{keyword}"


def _format_authors(authors: list[dict[str, str]]) -> str:
    """Format author list for BibTeX: 'First Last and First Last and ...'."""
    names = [a.get("name", "") for a in authors if a.get("name")]
    return " and ".join(names)


def paper_to_bibtex(paper: dict[str, Any], key: str | None = None) -> str:
    """Convert an S2 paper result dict to a BibTeX string."""
    if key is None:
        key = _make_citation_key(paper)

    title = paper.get("title", "")
    authors = _format_authors(paper.get("authors", []))
    year = paper.get("year", "")
    external_ids = paper.get("externalIds") or {}
    venue = paper.get("venue", "")
    journal_info = paper.get("journal") or {}
    pub_types = paper.get("publicationTypes") or []

    arxiv_id = external_ids.get("ArXiv", "")
    doi = external_ids.get("DOI", "")

    # Decide entry type
    if arxiv_id and not doi:
        # ArXiv preprint
        lines = [
            f"@misc{{{key},",
            f"      title={{{title}}},",
            f"      author={{{authors}}},",
            f"      year={{{year}}},",
            f"      eprint={{{arxiv_id}}},",
            f"      archivePrefix={{arXiv}},",
            f"      url={{https://arxiv.org/abs/{arxiv_id}}}",
            f"}}",
        ]
    elif "Conference" in pub_types or "conference" in venue.lower():
        lines = [
            f"@inproceedings{{{key},",
            f"  title     = {{{title}}},",
            f"  author    = {{{authors}}},",
            f"  booktitle = {{{venue}}},",
            f"  year      = {{{year}}},",
        ]
        if doi:
            lines.append(f"  doi       = {{{doi}}},")
        # Remove trailing comma from last field
        lines[-1] = lines[-1].rstrip(",")
        lines.append("}")
    else:
        # Journal article or generic
        journal_name = journal_info.get("name", "") or venue
        lines = [
            f"@article{{{key},",
            f"  title     = {{{title}}},",
            f"  author    = {{{authors}}},",
            f"  journal   = {{{journal_name}}},",
            f"  year      = {{{year}}},",
        ]
        if doi:
            lines.append(f"  doi       = {{{doi}}},")
        lines[-1] = lines[-1].rstrip(",")
        lines.append("}")

    return "\n".join(lines)


def _existing_ids(bib_path: Path) -> set[str]:
    """Extract ArXiv IDs and DOIs already present in the .bib file."""
    ids: set[str] = set()
    if not bib_path.exists():
        return ids
    content = bib_path.read_text(encoding="utf-8")
    # ArXiv eprints
    for m in re.finditer(r"eprint\s*=\s*\{([^}]+)\}", content):
        ids.add(m.group(1).strip())
    # DOIs
    for m in re.finditer(r"[Dd][Oo][Ii]\s*=\s*\{([^}]+)\}", content):
        ids.add(m.group(1).strip())
    return ids


def append_to_bib(entries: list[str], bib_path: Path | None = None) -> int:
    """Append BibTeX entries to the .bib file. Returns count of entries added."""
    if bib_path is None:
        bib_path = BIB_PATH

    existing = _existing_ids(bib_path)
    added = 0

    with open(bib_path, "a", encoding="utf-8") as f:
        for entry in entries:
            # Check for duplicate by ArXiv or DOI
            arxiv_match = re.search(r"eprint\s*=\s*\{([^}]+)\}", entry)
            doi_match = re.search(r"doi\s*=\s*\{([^}]+)\}", entry)

            is_dup = False
            if arxiv_match and arxiv_match.group(1).strip() in existing:
                is_dup = True
            if doi_match and doi_match.group(1).strip() in existing:
                is_dup = True

            if not is_dup:
                f.write("\n" + entry + "\n")
                added += 1

    return added


def print_results(papers: list[dict[str, Any]]) -> None:
    """Pretty-print search results for user selection."""
    for idx, paper in enumerate(papers):
        title = paper.get("title", "N/A")
        year = paper.get("year", "?")
        citations = paper.get("citationCount", 0)
        authors = paper.get("authors", [])
        author_str = ", ".join(a.get("name", "") for a in authors[:3])
        if len(authors) > 3:
            author_str += " et al."
        venue = paper.get("venue", "")
        abstract = (paper.get("abstract") or "")[:150]

        print(f"\n[{idx}] {title}")
        print(f"    Year: {year} | Citations: {citations} | Venue: {venue}")
        print(f"    Authors: {author_str}")
        if abstract:
            print(f"    Abstract: {abstract}...")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search Semantic Scholar and generate BibTeX entries"
    )
    parser.add_argument("query", help="Search query keywords")
    parser.add_argument(
        "--limit", type=int, default=10, help="Max results (default: 10)"
    )
    parser.add_argument(
        "--year", default=None, help="Year filter, e.g. '2020-2025' or '2022-'"
    )
    parser.add_argument(
        "--fields-of-study", default=None, help="Fields of study filter"
    )
    parser.add_argument("--pub-types", default=None, help="Publication types filter")
    parser.add_argument(
        "--auto-add", action="store_true", help="Add all results without prompting"
    )

    args = parser.parse_args()

    print(f"Searching Semantic Scholar for: '{args.query}'")
    papers = search_papers(
        args.query,
        limit=args.limit,
        year=args.year,
        fields_of_study=args.fields_of_study,
        publication_types=args.pub_types,
    )

    if not papers:
        print("No results found.")
        sys.exit(0)

    print(f"\nFound {len(papers)} results:")
    print_results(papers)

    if args.auto_add:
        selected = papers
    else:
        print(
            f"\nEnter paper numbers to add (comma-separated), or 'all', or 'q' to quit:"
        )
        choice = input("> ").strip()
        if choice.lower() == "q":
            sys.exit(0)
        elif choice.lower() == "all":
            selected = papers
        else:
            indices = [int(x.strip()) for x in choice.split(",") if x.strip().isdigit()]
            selected = [papers[i] for i in indices if 0 <= i < len(papers)]

    if not selected:
        print("No papers selected.")
        sys.exit(0)

    entries = [paper_to_bibtex(p) for p in selected]

    print(f"\nGenerated BibTeX entries:")
    for entry in entries:
        print(f"\n{entry}")

    added = append_to_bib(entries)
    print(f"\n✓ Added {added} new entries to {BIB_PATH.relative_to(PROJECT_ROOT)}")
    if added < len(entries):
        print(f"  ({len(entries) - added} duplicates skipped)")


if __name__ == "__main__":
    main()
