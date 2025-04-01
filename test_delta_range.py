def test_delta_range_checks():
    """
    Test the difference between 'if delta_range:' and 'if delta_range is not None'
    to demonstrate how they handle various input cases.
    """
    test_cases = [
        None,                    # No range provided
        (),                      # Empty tuple
        [],                      # Empty list
        {},                      # Empty dict
        0,                       # Zero
        False,                   # False
        "",                      # Empty string
        (-0.35, -0.25),         # Valid range
        (None, None),           # Tuple with None values
        (0, 0),                 # Tuple with zeros
        (0.25, 0.35),           # Positive range
        (-0.35, 0.25),          # Range spanning zero
    ]
    
    print("\n=== Testing 'if delta_range:' (truthiness check) ===")
    print("This checks if the value is 'truthy' (non-empty, non-zero)")
    for case in test_cases:
        delta_range = case
        if delta_range:
            print(f"✓ '{case}' evaluated to True")
        else:
            print(f"✗ '{case}' evaluated to False")
            
    print("\n=== Testing 'if delta_range is not None' ===")
    print("This only checks if the value is not None")
    for case in test_cases:
        delta_range = case
        if delta_range is not None:
            print(f"✓ '{case}' evaluated to True")
        else:
            print(f"✗ '{case}' evaluated to False")

if __name__ == "__main__":
    test_delta_range_checks() 