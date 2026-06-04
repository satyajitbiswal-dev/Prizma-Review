"""Per-language review hints for the LLM (not Python-only)."""

LANGUAGE_HINTS: dict[str, str] = {
    "python": (
        "Use Python idioms in fixed_code (list comprehensions, sets, deque, dict lookups). "
        "Flag list membership in loops, missing context managers, pickle.loads on untrusted data."
    ),
    "java": (
        "Use Java syntax in fixed_code (ArrayList, HashMap, HashSet, try-with-resources). "
        "Flag nested loops with .contains() on List, stream misuse, SQLException without try-with-resources, "
        "String concatenation in loops, synchronized blocks on wrong monitors."
    ),
    "go": (
        "Use Go syntax in fixed_code (slices, maps, defer, goroutines with care). "
        "Flag O(n²) nested range loops, slice append in tight loops without capacity, "
        "missing defer close(), busy-wait, global mutable state without mutex, ignoring errors (err != nil)."
    ),
    "javascript": (
        "Use JavaScript syntax in fixed_code. Flag .includes inside nested loops on arrays, "
        "missing await on promises, eval on user input."
    ),
    "typescript": (
        "Same as JavaScript; use TypeScript types in fixed_code when helpful."
    ),
    "cpp": "Use C++ syntax; flag manual memory without RAII, O(n²) nested loops.",
    "c": "Use C syntax; flag malloc without free, buffer overflows, nested loops.",
    "rust": "Use Rust syntax; flag clone() in hot loops, unnecessary allocations.",
    "ruby": "Use Ruby syntax; flag array.include? inside nested each loops.",
    "csharp": "Use C# syntax; flag LINQ in tight loops, missing IDisposable.",
    "php": "Use PHP syntax; flag SQL string concat, missing prepared statements.",
    "unknown": (
        "Use the same programming language as the file extension. "
        "Never write fixed_code in Python unless the file is Python."
    ),
}


def hint_for_language(language: str) -> str:
    lang = (language or "unknown").lower().strip()
    return LANGUAGE_HINTS.get(lang, LANGUAGE_HINTS["unknown"])
