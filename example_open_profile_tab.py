"""
Example usage of open_profile_tab function.

This demonstrates how to use the Facebook Profile tab automation with:
- Selected state detection (returns immediately if already selected)
- 3 retry attempts with progressive delays
- Multiple selector strategies (exact, partial, coordinate fallback)
- Verification after each tap
- Crash detection and relaunch
- XML dump on failure for debugging

Target element:
- class: android.view.View
- content-desc: "Profile, tab 5 of 5"
- clickable: true
- selected: true/false
- bounds: [576,48][720,136] (center: 648,92)
"""

from src.core.adb_manager import ADBManager
from src.core.ui_dump import open_profile_tab


def example_open_profile():
    """Example: Open Facebook Profile tab on a device."""
    
    # Initialize ADB manager
    adb = ADBManager()
    
    # Get list of connected devices
    devices = adb.list_devices()
    
    if not devices:
        print("No devices connected!")
        return
    
    # Use first device
    serial = devices[0]
    print(f"Using device: {serial}")
    
    # Simple logging function
    def log_fn(msg: str):
        print(msg)
    
    # Attempt to open Profile tab
    print("\n" + "="*70)
    print("Attempting to open Facebook Profile tab...")
    print("="*70 + "\n")
    
    try:
        success = open_profile_tab(
            adb=adb,
            serial=serial,
            log_fn=log_fn,
            timeout_s=20  # 20 second timeout
        )
        
        print("\n" + "="*70)
        if success:
            print("✓✓ SUCCESS: Profile tab opened and verified!")
            print("   • Tab found at bounds [576,48][720,136]")
            print("   • Tapped center (648, 92)")
            print("   • Profile screen confirmed")
        print("="*70 + "\n")
        
        return success
    
    except Exception as exc:
        print("\n" + "="*70)
        print(f"✗✗ EXCEPTION: {exc}")
        print("="*70 + "\n")
        print("Check the ui_dump_failure_*.xml file for debugging.")
        return False


def example_with_error_handling():
    """Example with comprehensive error handling."""
    
    adb = ADBManager()
    devices = adb.list_devices()
    
    if not devices:
        print("❌ No devices connected!")
        return False
    
    serial = devices[0]
    
    def log_fn(msg: str):
        print(f"  {msg}")
    
    print(f"\n🔧 Device: {serial}")
    print("📱 Opening Facebook Profile tab with robust retry logic...")
    print("-" * 70)
    
    try:
        result = open_profile_tab(adb, serial, log_fn, timeout_s=20)
        
        if result:
            print("\n✅ Profile tab successfully opened!")
            print("   • Selected state checked first")
            print("   • Selector strategies worked")
            print("   • Profile screen verified")
            return True
    
    except Exception as exc:
        print(f"\n❌ Error: {str(exc)}")
        if "not found in xml" in str(exc):
            print("   • Profile tab element not found in UI")
            print("   • Check if Facebook app UI changed")
            print("   • Review ui_dump_failure_*.xml for element structure")
        elif "timeout" in str(exc):
            print("   • Operation timed out")
            print("   • Facebook may be slow or unresponsive")
        else:
            print("   • All strategies exhausted")
            print("   • Check ui_dump_failure_*.xml in current directory")
        return False


if __name__ == "__main__":
    print("="*70)
    print("Facebook Profile Tab Automation - Example Usage")
    print("="*70)
    
    # Run basic example
    example_open_profile()
    
    # Uncomment to run example with enhanced error handling
    # example_with_error_handling()
