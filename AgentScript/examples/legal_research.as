agent legal_researcher {
  retry(3, backoff=exponential, base_delay_seconds=0.2, max_delay_seconds=1.0)
  fallback {
    step cached_sources using recall_cached(query=query)
  }
  circuit_breaker(threshold=0.50, window=2, cooldown_seconds=5, half_open_max_calls=1, min_calls=2)
}

tool search_indian_kanoon(query: string) -> list[Citation]
tool filter_relevance(citations: list[Citation], query: string) -> list[Citation]
tool summarize_claim(citations: list[Citation], query: string) -> Claim
tool recall_cached(query: string) -> list[Citation]

workflow legal_brief(query: string) -> Claim {
  step sources using search_indian_kanoon(query)
  step relevant using filter_relevance(citations=sources, query=query)
  let brief: Claim = summarize_claim(citations=relevant, query=query)
  let stored_brief: string = brief.text
  return brief
}

workflow recall_notes(query: string) -> list[MemoryEntry] {
  return mem_search(query)
}
