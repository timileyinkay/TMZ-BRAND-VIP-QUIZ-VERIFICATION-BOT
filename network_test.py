import requests
import socket
import urllib3

def test_network():
    print("🔍 Testing network connectivity...")
    
    # Test basic internet
    try:
        response = requests.get('https://www.google.com', timeout=10)
        print("✅ Basic internet connection: OK")
    except:
        print("❌ Basic internet connection: FAILED")
        return False
    
    # Test Telegram API
    try:
        response = requests.get('https://api.telegram.org', timeout=10)
        print("✅ Telegram API access: OK")
    except:
        print("❌ Telegram API access: BLOCKED")
        print("💡 Telegram is likely blocked in your network")
        return False
    
    # Test DNS resolution
    try:
        socket.gethostbyname('api.telegram.org')
        print("✅ DNS resolution: OK")
    except:
        print("❌ DNS resolution: FAILED")
        return False
    
    return True

if __name__ == "__main__":
    if test_network():
        print("\n🎯 Solution: Use a VPN or mobile data")
    else:
        print("\n🎯 Solution: Check your internet connection or use VPN")