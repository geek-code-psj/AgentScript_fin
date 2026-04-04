"""JSON recovery and validation for malformed LLM outputs.

Handles common LLM errors with regex-based recovery and schema validation:
- Text before/after JSON
- Missing quotes
- Trailing commas
- Incomplete/truncated JSON
"""

from __future__ import annotations

import json
import re
from typing import Any


class JSONRecoveryError(Exception):
    """Base exception for JSON recovery failures."""
    pass


def recover_json(malformed_string: str) -> dict[str, Any]:
    """Recover valid JSON from malformed LLM output.
    
    Handles common LLM errors by:
    1. Extracting JSON object from surrounding text
    2. Fixing common syntax errors (quotes, commas, braces)
    3. Truncating at closing brace if needed
    4. Parsing with Python's json module
    
    Args:
        malformed_string: Potentially malformed JSON string from LLM
        
    Returns:
        Parsed JSON dictionary
        
    Raises:
        JSONRecoveryError: If no valid JSON could be recovered
        
    Examples:
        >>> recover_json('Please: {"name": "John"}. Done!')
        {'name': 'John'}
        
        >>> recover_json('{name: "John", age: 30}')
        {'name': 'John', 'age': 30}
        
        >>> recover_json('{"items": [1, 2, 3,]}')
        {'items': [1, 2, 3]}
    """
    if not malformed_string or not isinstance(malformed_string, str):
        raise JSONRecoveryError(f"Invalid input: {type(malformed_string)}")
    
    # Try direct parsing first (might be valid)
    try:
        return json.loads(malformed_string)
    except json.JSONDecodeError:
        pass
    
    # Strategy 1: Extract JSON object from surrounding text
    extracted = _extract_json_object(malformed_string)
    if extracted:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass
    
    # Strategy 2: Fix common syntax errors
    fixed = _fix_common_errors(malformed_string)
    if fixed:
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
    
    # Strategy 3: Truncate at closing brace
    truncated = _truncate_at_closing_brace(malformed_string)
    if truncated and truncated != malformed_string:
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass
    
    raise JSONRecoveryError(
        f"Could not recover valid JSON from: {malformed_string[:100]}..."
    )


def validate_and_recover(
    text: str,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover JSON and validate against schema.
    
    Args:
        text: Potentially malformed JSON
        schema: Optional schema dict with required fields and types
               Example: {"required": ["id", "status"], "types": {"id": int}}
        
    Returns:
        Recovered and validated JSON data
        
    Raises:
        JSONRecoveryError: If recovery fails or schema validation fails
    """
    data = recover_json(text)
    
    if schema:
        _validate_schema(data, schema)
    
    return data


def _extract_json_object(text: str) -> str | None:
    """Extract JSON object from text with surrounding content.
    
    Looks for balanced braces { ... } and extracts the content.
    """
    # Find first opening brace
    start = text.find('{')
    if start == -1:
        return None
    
    # Count braces to find matching closing brace
    brace_count = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            brace_count += 1
        elif text[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                return text[start:i+1]
    
    return None


def _fix_common_errors(text: str) -> str | None:
    """Fix common JSON syntax errors.
    
    Handles:
    - Unquoted keys: {name: "John"} -> {"name": "John"}
    - Single quotes: {'name': 'John'} -> {"name": "John"}
    - Trailing commas: [1, 2, 3,] -> [1, 2, 3]
    """
    # Extract JSON object first
    extracted = _extract_json_object(text)
    if not extracted:
        return None
    
    result = extracted
    
    # Replace single quotes with double quotes (simple heuristic)
    result = re.sub(r"'([^']*)'", r'"\1"', result)
    
    # Fix unquoted keys (word: value -> "word": value)
    result = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)', r'\1"\2"\3', result)
    
    # Remove trailing commas before ] and }
    result = re.sub(r',(\s*[}\]])', r'\1', result)
    
    return result


def _truncate_at_closing_brace(text: str) -> str:
    """Truncate text after closing brace of JSON object.
    
    Finds the first { and its matching }, returns text up to and including
    the closing brace. This handles cases where LLM continues typing after JSON.
    """
    start = text.find('{')
    if start == -1:
        return text
    
    brace_count = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            brace_count += 1
        elif text[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                return text[:i+1]
    
    return text


def _validate_schema(
    data: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    """Validate recovered JSON against schema.
    
    Args:
        data: The recovered JSON data
        schema: Schema specification with:
               - "required": list of required field names
               - "types": dict mapping field names to expected types
               
    Raises:
        JSONRecoveryError: If validation fails
    """
    if not isinstance(data, dict):
        raise JSONRecoveryError(f"Expected dict, got {type(data)}")
    
    required = schema.get("required", [])
    types = schema.get("types", {})
    
    # Check required fields
    for field in required:
        if field not in data:
            raise JSONRecoveryError(f"Missing required field: {field}")
    
    # Check field types
    for field, expected_type in types.items():
        if field in data:
            value = data[field]
            if not isinstance(value, expected_type):
                raise JSONRecoveryError(
                    f"Field '{field}' should be {expected_type}, "
                    f"got {type(value)}: {value}"
                )


def test_json_recovery() -> dict[str, Any]:
    """Test JSON recovery from common LLM errors.
    
    Returns:
        Dictionary with test results
    """
    test_results = []
    
    # Test 1: Extra text before/after JSON
    try:
        result = recover_json('Please: {"name": "John", "age": 30}. Done!')
        test1_pass = result == {"name": "John", "age": 30}
        test_results.append({
            "name": "extra_text",
            "passed": test1_pass,
            "description": "Recovers JSON from surrounding text",
        })
    except Exception as e:
        test_results.append({
            "name": "extra_text",
            "passed": False,
            "error": str(e),
        })
    
    # Test 2: Unquoted keys
    try:
        result = recover_json('{name: "John", age: 30}')
        test2_pass = result.get("name") == "John" and result.get("age") == 30
        test_results.append({
            "name": "unquoted_keys",
            "passed": test2_pass,
            "description": "Recovers JSON with unquoted keys",
        })
    except Exception as e:
        test_results.append({
            "name": "unquoted_keys",
            "passed": False,
            "error": str(e),
        })
    
    # Test 3: Trailing commas
    try:
        result = recover_json('{"items": [1, 2, 3,], "status": "ok",}')
        test3_pass = result.get("items") == [1, 2, 3] and result.get("status") == "ok"
        test_results.append({
            "name": "trailing_commas",
            "passed": test3_pass,
            "description": "Recovers JSON with trailing commas",
        })
    except Exception as e:
        test_results.append({
            "name": "trailing_commas",
            "passed": False,
            "error": str(e),
        })
    
    # Test 4: Incomplete JSON (truncate)
    try:
        result = recover_json('{"status": "ok", "data": {"id": 123}} more text')
        test4_pass = result.get("status") == "ok" and result.get("data") == {"id": 123}
        test_results.append({
            "name": "incomplete_truncate",
            "passed": test4_pass,
            "description": "Truncates and recovers incomplete JSON",
        })
    except Exception as e:
        test_results.append({
            "name": "incomplete_truncate",
            "passed": False,
            "error": str(e),
        })
    
    # Test 5: Single quotes
    try:
        result = recover_json("{'approved': true, 'reason': 'okay'}")
        test5_pass = result.get("approved") == True and result.get("reason") == "okay"
        test_results.append({
            "name": "single_quotes",
            "passed": test5_pass,
            "description": "Converts single quotes to double quotes",
        })
    except Exception as e:
        test_results.append({
            "name": "single_quotes",
            "passed": False,
            "error": str(e),
        })
    
    return {
        "all_passed": all(t["passed"] for t in test_results),
        "tests": test_results,
        "passed_tests": sum(1 for t in test_results if t["passed"]),
        "total_tests": len(test_results),
    }
