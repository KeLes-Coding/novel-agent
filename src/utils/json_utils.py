import json
import re
from typing import Any, Optional, Dict, List

def extract_json(text: str) -> Any:
    """
    Extract JSON from text, handling Markdown code blocks and common formatting issues.
    
    Args:
        text: The text containing JSON.
        
    Returns:
        The parsed JSON object (dict or list).
        
    Raises:
        ValueError: If JSON cannot be found or parsed.
    """
    text = text.strip()
    
    # 1. Try to find Markdown code blocks
    code_block_pattern = r"```(?:json)?\s*(.*?)\s*```"
    match = re.search(code_block_pattern, text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        # 2. Heuristic: locate first '{' or '[' and last '}' or ']'
        # This handles cases where LLM outputs text before/after JSON without code blocks
        json_str = text
        
        # Simple extraction based on first/last brace/bracket
        # We need to determine if it's likely an object or a list
        first_brace = text.find('{')
        first_bracket = text.find('[')
        
        start_idx = -1
        end_idx = -1
        
        if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
            start_idx = first_brace
            end_idx = text.rfind('}') + 1
        elif first_bracket != -1:
            start_idx = first_bracket
            end_idx = text.rfind(']') + 1
            
        if start_idx != -1 and end_idx != -1:
            json_str = text[start_idx:end_idx]

    # 3. Clean up common issues (e.g. trailing commas - simple regex approach)
    # Note: Regex removal of trailing commas is risky but often necessary for LLM output.
    # A safer way is using a library like `json_repair` or `dirtyjson`, but we Stick to stdlib for now.
    # identifying trailing commas in objects: , \s* } -> }
    # identifying trailing commas in lists: , \s* ] -> ]
    json_str = re.sub(r",\s*}", "}", json_str)
    json_str = re.sub(r",\s*]", "]", json_str)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        # raise with the extracted string for debugging
        raise ValueError(f"Failed to parse JSON: {e}\nExtracted content: {json_str[:100]}...") from e

def robust_api_call(
    provider_call_func,
    parse_func=extract_json,
    max_retries: int = 3,
    logger=None,
    **kwargs
) -> Any:
    """
    Helper to retry API calls on parsing failure.
    
    Args:
        provider_call_func: A lambda/function that returns the LLM response text.
        parse_func: Function to parse the response text. Defaults to extract_json.
        max_retries: Number of retries.
        logger: Logger instance.
        
    Returns:
        Parsed result.
    """
    last_error = None
    
    for attempt in range(max_retries):
        try:
            response_text = provider_call_func()
            return parse_func(response_text)
        except Exception as e:
            last_error = e
            if logger:
                logger.warning(f"JSON Parse attempt {attempt+1}/{max_retries} failed: {e}")
            # Optional: We could modify the prompt here for the next attempt?
            # For now, just retry raw.
            
    raise RuntimeError(f"Failed to generate valid JSON after {max_retries} attempts.") from last_error
