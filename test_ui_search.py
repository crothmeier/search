#!/usr/bin/env python3
"""Test script to verify UI search functionality."""
import requests
import json

# Test from outside container
print("Testing API from host:")
try:
    response = requests.get("http://localhost:8000/search?q=python&limit=3")
    print(f"Status: {response.status_code}")
    print(f"Results: {len(response.json()['results'])} found")
except Exception as e:
    print(f"Error: {e}")

print("\n" + "="*50 + "\n")

# Simulate what the UI does
print("Simulating UI search request:")
API_BASE_URL = "http://localhost:8000"
query = "python"
limit = 20
offset = 0

try:
    url = f"{API_BASE_URL}/search"
    params = {"q": query, "limit": limit, "offset": offset}
    print(f"URL: {url}")
    print(f"Params: {params}")
    
    response = requests.get(url, params=params, timeout=10)
    print(f"Status: {response.status_code}")
    
    data = response.json()
    print(f"Results: {len(data.get('results', []))} found")
    print(f"Total: {data.get('total', 0)}")
    print(f"Query: '{data.get('query', '')}'")
except Exception as e:
    print(f"Error: {e}")