#!/usr/bin/env python3
"""Test script for ChatGPT Search API."""
import sys
import time
import json
import argparse
from pathlib import Path

import requests
from requests.exceptions import RequestException

# Default API URL
DEFAULT_API_URL = "http://localhost:8000"


class APITester:
    """Test ChatGPT Search API endpoints."""
    
    def __init__(self, api_url: str):
        """Initialize tester with API URL."""
        self.api_url = api_url.rstrip('/')
        self.results = {
            'passed': 0,
            'failed': 0,
            'tests': []
        }
    
    def test_endpoint(self, name: str, method: str, endpoint: str, 
                     params: dict = None, expected_status: int = 200):
        """Test a single API endpoint."""
        url = f"{self.api_url}{endpoint}"
        print(f"\nTesting {name}...")
        
        try:
            start_time = time.time()
            
            if method == 'GET':
                response = requests.get(url, params=params, timeout=10)
            else:
                response = requests.post(url, json=params, timeout=10)
            
            duration = time.time() - start_time
            
            # Check status code
            if response.status_code == expected_status:
                print(f"✓ {name} - Status: {response.status_code} - Time: {duration:.3f}s")
                self.results['passed'] += 1
                test_result = 'passed'
            else:
                print(f"✗ {name} - Expected: {expected_status}, Got: {response.status_code}")
                print(f"  Response: {response.text[:200]}")
                self.results['failed'] += 1
                test_result = 'failed'
            
            # Store test result
            self.results['tests'].append({
                'name': name,
                'endpoint': endpoint,
                'status': test_result,
                'status_code': response.status_code,
                'duration': duration,
                'response': response.json() if response.status_code == 200 else response.text
            })
            
            return response
            
        except RequestException as e:
            print(f"✗ {name} - Error: {e}")
            self.results['failed'] += 1
            self.results['tests'].append({
                'name': name,
                'endpoint': endpoint,
                'status': 'error',
                'error': str(e)
            })
            return None
    
    def run_tests(self):
        """Run all API tests."""
        print(f"Testing API at: {self.api_url}")
        print("=" * 50)
        
        # Test health endpoint
        self.test_endpoint(
            "Health Check",
            "GET",
            "/health"
        )
        
        # Test stats endpoint
        stats_response = self.test_endpoint(
            "Database Statistics",
            "GET",
            "/stats"
        )
        
        # Test search endpoint - basic search
        self.test_endpoint(
            "Basic Search",
            "GET",
            "/search",
            params={"q": "test", "limit": 10}
        )
        
        # Test search with special characters
        self.test_endpoint(
            "Search with Special Characters",
            "GET",
            "/search",
            params={"q": "python AND code", "limit": 5}
        )
        
        # Test search with quotes
        self.test_endpoint(
            "Phrase Search",
            "GET",
            "/search",
            params={"q": '"machine learning"', "limit": 5}
        )
        
        # Test pagination
        self.test_endpoint(
            "Search with Pagination",
            "GET",
            "/search",
            params={"q": "data", "limit": 20, "offset": 10}
        )
        
        # Test invalid search
        self.test_endpoint(
            "Empty Search Query",
            "GET",
            "/search",
            params={"q": ""},
            expected_status=422  # FastAPI validation error
        )
        
        # Test conversation endpoint if we have data
        if stats_response and stats_response.status_code == 200:
            stats = stats_response.json()
            if stats.get('total_conversations', 0) > 0:
                # Get a conversation ID from search
                search_resp = requests.get(
                    f"{self.api_url}/search",
                    params={"q": "*", "limit": 1}
                )
                if search_resp.status_code == 200 and search_resp.json()['results']:
                    conv_id = search_resp.json()['results'][0]['conversation_id']
                    self.test_endpoint(
                        "Get Conversation",
                        "GET",
                        f"/conversations/{conv_id}"
                    )
        
        # Test non-existent conversation
        self.test_endpoint(
            "Non-existent Conversation",
            "GET",
            "/conversations/non-existent-id",
            expected_status=404
        )
        
        # Test metrics endpoint
        self.test_endpoint(
            "Prometheus Metrics",
            "GET",
            "/metrics"
        )
        
        # Test suggestions endpoint
        self.test_endpoint(
            "Query Suggestions",
            "GET",
            "/suggest",
            params={"q": "pyth"}
        )
        
        # Print summary
        print("\n" + "=" * 50)
        print("Test Summary:")
        print(f"  Passed: {self.results['passed']}")
        print(f"  Failed: {self.results['failed']}")
        print(f"  Total:  {len(self.results['tests'])}")
        
        return self.results['failed'] == 0
    
    def save_results(self, output_file: str):
        """Save test results to JSON file."""
        with open(output_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        print(f"\nTest results saved to: {output_file}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test ChatGPT Search API endpoints"
    )
    parser.add_argument(
        '--api-url',
        default=DEFAULT_API_URL,
        help=f'API base URL (default: {DEFAULT_API_URL})'
    )
    parser.add_argument(
        '--output',
        help='Save test results to JSON file'
    )
    
    args = parser.parse_args()
    
    # Run tests
    tester = APITester(args.api_url)
    success = tester.run_tests()
    
    # Save results if requested
    if args.output:
        tester.save_results(args.output)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()