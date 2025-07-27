#\!/usr/bin/env python3
"""
Standalone debug harness for testing ChatGPT Search UI/API integration.
Can be run locally or against production endpoints.
"""

import requests
import json
import sys
import argparse
from datetime import datetime
import time
import traceback
from typing import Dict, List, Optional

class SearchDebugHarness:
    def __init__(self, api_url: str, ui_url: str):
        self.api_url = api_url.rstrip('/')
        self.ui_url = ui_url.rstrip('/')
        self.session = requests.Session()
        
    def test_api_health(self) -> bool:
        """Test if API is healthy"""
        print(f"\n🔍 Testing API health at {self.api_url}...")
        try:
            response = self.session.get(f"{self.api_url}/health", timeout=5)
            if response.status_code == 200:
                print("✅ API is healthy")
                return True
            else:
                print(f"❌ API returned status {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ API health check failed: {e}")
            return False
    
    def test_ui_health(self) -> bool:
        """Test if UI is healthy"""
        print(f"\n🔍 Testing UI health at {self.ui_url}...")
        try:
            response = self.session.get(f"{self.ui_url}/_stcore/health", timeout=5)
            if response.status_code == 200:
                print("✅ UI is healthy")
                return True
            else:
                print(f"❌ UI returned status {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ UI health check failed: {e}")
            return False
    
    def test_search(self, query: str) -> Dict:
        """Test search functionality"""
        print(f"\n🔍 Testing search with query: '{query}'")
        try:
            start_time = time.time()
            response = self.session.get(
                f"{self.api_url}/search",
                params={"q": query},
                timeout=10
            )
            elapsed = time.time() - start_time
            
            print(f"📊 Response time: {elapsed:.2f}s")
            print(f"📊 Status code: {response.status_code}")
            print(f"📊 Headers: {dict(response.headers)}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Search successful")
                print(f"📊 Results count: {len(data.get('results', []))}")
                
                # Print first result summary
                if data.get('results'):
                    first = data['results'][0]
                    print(f"\n📄 First result:")
                    print(f"   ID: {first.get('id', 'N/A')}")
                    print(f"   Title: {first.get('title', 'N/A')}")
                    print(f"   Messages: {len(first.get('messages', []))}")
                
                return data
            else:
                print(f"❌ Search failed with status {response.status_code}")
                print(f"Response: {response.text}")
                return {}
                
        except Exception as e:
            print(f"❌ Search failed: {e}")
            traceback.print_exc()
            return {}
    
    def test_edge_cases(self):
        """Test various edge cases"""
        print("\n🧪 Testing edge cases...")
        
        # Empty query
        print("\n1️⃣ Empty query test:")
        self.test_search("")
        
        # Special characters
        print("\n2️⃣ Special characters test:")
        self.test_search("test & <script>alert('xss')</script>")
        
        # Very long query
        print("\n3️⃣ Long query test:")
        self.test_search("a" * 1000)
        
        # Unicode
        print("\n4️⃣ Unicode test:")
        self.test_search("emoji test 🔍 日本語 العربية")
    
    def stress_test(self, query: str, requests_count: int = 10):
        """Run stress test"""
        print(f"\n🏃 Running stress test: {requests_count} requests...")
        
        success_count = 0
        total_time = 0
        
        for i in range(requests_count):
            start_time = time.time()
            try:
                response = self.session.get(
                    f"{self.api_url}/search",
                    params={"q": f"{query} {i}"},
                    timeout=10
                )
                elapsed = time.time() - start_time
                total_time += elapsed
                
                if response.status_code == 200:
                    success_count += 1
                    print(f"✅ Request {i+1}/{requests_count} - {elapsed:.2f}s")
                else:
                    print(f"❌ Request {i+1}/{requests_count} - Status {response.status_code}")
                    
            except Exception as e:
                print(f"❌ Request {i+1}/{requests_count} - Failed: {e}")
        
        print(f"\n📊 Stress test results:")
        print(f"   Success rate: {success_count}/{requests_count} ({success_count/requests_count*100:.1f}%)")
        print(f"   Average response time: {total_time/requests_count:.2f}s")
    
    def run_full_test(self, include_stress: bool = False):
        """Run complete test suite"""
        print("=" * 60)
        print(f"🚀 ChatGPT Search Debug Harness")
        print(f"   API URL: {self.api_url}")
        print(f"   UI URL: {self.ui_url}")
        print(f"   Time: {datetime.now().isoformat()}")
        print("=" * 60)
        
        # Health checks
        api_healthy = self.test_api_health()
        ui_healthy = self.test_ui_health()
        
        if not api_healthy:
            print("\n⚠️  API is not healthy, skipping further tests")
            return
        
        # Basic search tests
        self.test_search("python")
        self.test_search("machine learning")
        
        # Edge cases
        self.test_edge_cases()
        
        # Stress test
        if include_stress:
            self.stress_test("test query", 20)
        
        print("\n" + "=" * 60)
        print("✅ Test suite completed")
        print("=" * 60)

def main():
    parser = argparse.ArgumentParser(description="Debug harness for ChatGPT Search UI/API")
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--ui-url",
        default="http://localhost:8501",
        help="UI base URL (default: http://localhost:8501)"
    )
    parser.add_argument(
        "--query",
        help="Run a single search query"
    )
    parser.add_argument(
        "--stress",
        action="store_true",
        help="Include stress testing"
    )
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="Only run health checks"
    )
    
    args = parser.parse_args()
    
    harness = SearchDebugHarness(args.api_url, args.ui_url)
    
    if args.health_only:
        harness.test_api_health()
        harness.test_ui_health()
    elif args.query:
        harness.test_api_health()
        harness.test_search(args.query)
    else:
        harness.run_full_test(include_stress=args.stress)

if __name__ == "__main__":
    main()
EOF < /dev/null
