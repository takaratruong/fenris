def reverse_string(s: str) -> str:
    """Reverse a string.
    
    Args:
        s: The string to reverse.
        
    Returns:
        The reversed string.
    """
    return s[::-1]


# Quick test
if __name__ == "__main__":
    test_cases = ["hello", "world", "racecar", "", "a", "12345"]
    for tc in test_cases:
        result = reverse_string(tc)
        print(f"reverse_string('{tc}') = '{result}'")
