import requests
import streamlit as st
import os
import logging

API_URL = os.getenv("API_URL", "http://api:8000")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info(f"Using API URL: {API_URL}")

st.title("🔍 ChatGPT Conversation Search")

query = st.text_input("Search conversations")

if st.button("Search"):
    with st.spinner("Searching..."):
        try:
            logger.info(f"Making API call to {API_URL}/search")
            resp = requests.get(f"{API_URL}/search", params={"q": query}, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if data.get('error'):
                st.error(f"API Error: {data['error']}")
                logger.error(f"API returned error: {data['error']}")
            else:
                st.write("Search Results:", data['results'])
                logger.info(f"Received {len(data['results'])} results from API.")
        except requests.exceptions.RequestException as e:
            st.error(f"Search request failed: {e}")
            logger.exception("API request failed")
