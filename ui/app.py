"""Streamlit UI for ChatGPT conversation search."""
import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

import streamlit as st
import requests
import pandas as pd
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

# API configuration
API_BASE_URL = os.getenv('API_URL', 'http://localhost:8000')


def search_conversations(query: str, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    """Search conversations via API."""
    try:
        response = requests.get(
            f"{API_BASE_URL}/search",
            params={"q": query, "limit": limit, "offset": offset},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Search API error: {e}")
        return {"error": str(e), "results": []}


def get_conversation(conversation_id: str) -> Dict[str, Any]:
    """Get full conversation via API."""
    try:
        response = requests.get(
            f"{API_BASE_URL}/conversations/{conversation_id}",
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Conversation API error: {e}")
        return {"error": str(e)}


def get_stats() -> Dict[str, Any]:
    """Get database statistics via API."""
    try:
        response = requests.get(f"{API_BASE_URL}/stats", timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Stats API error: {e}")
        return {}


def format_timestamp(timestamp_str: str) -> str:
    """Format ISO timestamp to readable format."""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return timestamp_str


def render_search_result(result: Dict[str, Any]):
    """Render a single search result."""
    with st.container():
        col1, col2 = st.columns([3, 1])
        
        with col1:
            st.markdown(f"**{result['title']}**")
            # Display snippet with highlighting preserved
            snippet_html = result['snippet'].replace('<mark>', '**').replace('</mark>', '**')
            st.markdown(f"_{snippet_html}_")
            
        with col2:
            st.text(f"Messages: {result['message_count']}")
            st.text(f"Updated: {format_timestamp(result['updated_at'])}")
        
        if st.button("View Full Conversation", key=f"view_{result['conversation_id']}"):
            st.session_state.selected_conversation = result['conversation_id']
        
        st.divider()


def render_conversation(conversation: Dict[str, Any]):
    """Render full conversation view."""
    st.subheader(conversation['title'])
    st.text(f"Messages: {conversation['message_count']} | "
            f"Created: {format_timestamp(conversation['created_at'])} | "
            f"Updated: {format_timestamp(conversation['updated_at'])}")
    
    st.divider()
    
    for msg in conversation['messages']:
        role = msg['sender']
        content = msg['content']
        timestamp = format_timestamp(msg['timestamp'])
        
        if role.lower() == 'user':
            with st.chat_message("user"):
                st.write(content)
                st.caption(timestamp)
        else:
            with st.chat_message("assistant"):
                st.write(content)
                st.caption(timestamp)


def export_results(results: List[Dict[str, Any]], format: str = "json"):
    """Export search results to file."""
    if format == "json":
        return json.dumps(results, indent=2)
    elif format == "csv":
        # Flatten results for CSV
        flattened = []
        for r in results:
            flattened.append({
                'conversation_id': r['conversation_id'],
                'title': r['title'],
                'snippet': r['snippet'].replace('<mark>', '').replace('</mark>', ''),
                'message_count': r['message_count'],
                'updated_at': r['updated_at']
            })
        df = pd.DataFrame(flattened)
        return df.to_csv(index=False)
    return ""


def main():
    """Main Streamlit application."""
    st.set_page_config(
        page_title="ChatGPT Search",
        page_icon="🔍",
        layout="wide"
    )
    
    # Initialize session state
    if 'search_history' not in st.session_state:
        st.session_state.search_history = []
    if 'selected_conversation' not in st.session_state:
        st.session_state.selected_conversation = None
    if 'search_results' not in st.session_state:
        st.session_state.search_results = None
    
    # Header
    st.title("🔍 ChatGPT Conversation Search")
    
    # Sidebar
    with st.sidebar:
        st.header("Search History")
        if st.session_state.search_history:
            for query in reversed(st.session_state.search_history[-10:]):
                if st.button(query, key=f"history_{query}"):
                    st.session_state.search_query = query
        else:
            st.info("No search history yet")
        
        st.divider()
        
        # Stats
        stats = get_stats()
        if stats:
            st.header("Database Statistics")
            st.metric("Total Conversations", f"{stats.get('total_conversations', 0):,}")
            st.metric("Total Messages", f"{stats.get('total_messages', 0):,}")
            st.metric("Database Size", f"{stats.get('database_size_mb', 0):.1f} MB")
            
            if date_range := stats.get('date_range'):
                st.text(f"Date Range:")
                st.text(f"  From: {format_timestamp(date_range['earliest'])}")
                st.text(f"  To: {format_timestamp(date_range['latest'])}")
    
    # Main content
    if st.session_state.selected_conversation:
        # Show full conversation
        if st.button("← Back to Search"):
            st.session_state.selected_conversation = None
            st.rerun()
        
        conversation = get_conversation(st.session_state.selected_conversation)
        if 'error' not in conversation:
            render_conversation(conversation)
        else:
            st.error(f"Error loading conversation: {conversation['error']}")
    
    else:
        # Search interface
        col1, col2 = st.columns([4, 1])
        
        with col1:
            search_query = st.text_input(
                "Search conversations",
                placeholder="Enter search terms...",
                help="Use quotes for exact phrases, AND/OR for boolean search"
            )
        
        with col2:
            st.write("")  # Spacing
            search_button = st.button("Search", type="primary", use_container_width=True)
        
        # Perform search
        if search_button and search_query:
            with st.spinner("Searching..."):
                results = search_conversations(search_query)
                st.session_state.search_results = results
                
                # Add to history
                if search_query not in st.session_state.search_history:
                    st.session_state.search_history.append(search_query)
        
        # Display results
        if st.session_state.search_results:
            results = st.session_state.search_results
            
            if 'error' in results:
                st.error(f"Search error: {results['error']}")
            else:
                # Results header
                col1, col2, col3 = st.columns([2, 1, 1])
                
                with col1:
                    st.subheader(f"Found {results['total']} results for \"{results['query']}\"")
                
                with col2:
                    export_format = st.selectbox("Export format", ["JSON", "CSV"])
                
                with col3:
                    if st.button("Export Results"):
                        export_data = export_results(
                            results['results'], 
                            export_format.lower()
                        )
                        st.download_button(
                            label="Download",
                            data=export_data,
                            file_name=f"search_results.{export_format.lower()}",
                            mime=f"application/{export_format.lower()}"
                        )
                
                # Display results
                if results['results']:
                    for result in results['results']:
                        render_search_result(result)
                    
                    # Pagination
                    if results['has_more']:
                        if st.button("Load More Results"):
                            more_results = search_conversations(
                                results['query'],
                                limit=20,
                                offset=results['offset'] + results['limit']
                            )
                            if 'error' not in more_results:
                                results['results'].extend(more_results['results'])
                                results['offset'] = more_results['offset']
                                results['has_more'] = more_results['has_more']
                                st.session_state.search_results = results
                                st.rerun()
                else:
                    st.info("No results found. Try different search terms.")


if __name__ == "__main__":
    main()