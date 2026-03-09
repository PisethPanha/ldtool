"""Test script to demonstrate Smart Title by Bot functionality."""

from src.core.smart_title_service import SmartTitleService


def test_smart_title_examples():
    """Test various filename patterns."""
    
    service = SmartTitleService(log_fn=print)
    
    test_cases = [
        # Example from requirements
        "Africa's $80 Billion Dam Proposal! #power #geography #africa (01).mp4",
        
        # Filename without hashtags
        "Amazon Lost Tribe Documentary (01).mp4",
        
        # Various trailing patterns
        "Best Travel Tips #travel #lifestyle (copy).mov",
        "Cooking Tutorial #food #recipe _01.mkv",
        "Tech Review #technology #gadgets-02.avi",
        
        # Complex case
        "Why $100M Companies Fail! #business #startup #entrepreneurship (final).mp4",
        
        # No trailing patterns
        "Simple Title #tag1 #tag2.mp4",
    ]
    
    print("=" * 80)
    print("SMART TITLE BY BOT - TEST RESULTS")
    print("=" * 80)
    
    for i, filename in enumerate(test_cases, 1):
        print(f"\n[Test {i}]")
        print(f"Input:  {filename}")
        result = service.parse_filename(filename)
        print(f"Title:  {result['title']}")
        print(f"Desc:   {result['description']}")
        print("-" * 80)


if __name__ == "__main__":
    test_smart_title_examples()
