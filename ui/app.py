import requests
import streamlit as st
import os
import logging
import sys
import json
import pandas as pd
from datetime import datetime
import traceback

# Configure logging to stdout for Docker visibility
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True
)
logger = logging.getLogger(__name__)

# Environment configuration
API_URL = os.getenv("API_URL", "http://api:8000")
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# Initialize session state
if 'search_history' not in st.session_state:
    st.session_state.search_history = []
if 'current_results' not in st.session_state:
    st.session_state.current_results = None
if 'debug_mode' not in st.session_state:
    st.session_state.debug_mode = DEBUG_MODE

logger.info(f"Streamlit UI starting - API URL: {API_URL}")

# App configuration
st.set_page_config(
    page_title="ChatGPT Search",
    page_icon="🔍",
    layout="wide"
)

def format_snippet(text):
    """Replace FTS5 mark tags with Markdown formatting"""
    if text:
        return text.replace('<mark>', '**').replace('</mark>', '**')
    return text

def display_search_results(data):
    """Display search results with proper error handling and multiple view formats"""
    try:
        results = data.get('results', [])
        
        if not results:
            st.info("No results found for your search query.")
            return
        
        st.success(f"Found {len(results)} results")
        
        # Create tabs for different view formats
        tab1, tab2, tab3 = st.tabs(["📋 Formatted View", "📊 Table View", "🔧 Raw JSON"])
        
        with tab1:
            # Formatted view
            for idx, result in enumerate(results):
                with st.container():
                    col1, col2 = st.columns([3, 1])
                    
                    with col1:
                        # Use unique key for each expander
                        with st.expander(f"Conversation: {result.get('title', 'Untitled')} (ID: {result.get('id', 'Unknown')})", expanded=idx == 0):
                            # Display conversation metadata
                            st.markdown(f"**Created:** {result.get('create_time', 'Unknown')}")
                            st.markdown(f"**Updated:** {result.get('update_time', 'Unknown')}")
                            
                            # Display messages
                            messages = result.get('messages', [])
                            if messages:
                                st.markdown("### Messages")
                                for msg_idx, msg in enumerate(messages):
                                    role = msg.get('role', 'unknown')
                                    content = format_snippet(msg.get('content', ''))
                                    
                                    if role == 'user':
                                        st.markdown(f"**👤 User:** {content}")
                                    elif role == 'assistant':
                                        st.markdown(f"**🤖 Assistant:** {content}")
                                    else:
                                        st.markdown(f"**{role}:** {content}")
                                    
                                    st.divider()
                    
                    with col2:
                        # Action buttons with unique keys
                        if st.button("Copy ID", key=f"copy_id_{idx}"):
                            st.code(result.get('id', 'Unknown'))
        
        with tab2:
            # Table view
            table_data = []
            for result in results:
                table_data.append({
                    'ID': result.get('id', 'Unknown'),
                    'Title': result.get('title', 'Untitled'),
                    'Created': result.get('create_time', 'Unknown'),
                    'Updated': result.get('update_time', 'Unknown'),
                    'Message Count': len(result.get('messages', []))
                })
            
            df = pd.DataFrame(table_data)
            st.dataframe(df, use_container_width=True)
        
        with tab3:
            # Raw JSON view
            st.json(results)
    
    except Exception as e:
        logger.error(f"Error displaying search results: {str(e)}")
        logger.error(traceback.format_exc())
        st.error(f"Error displaying results: {str(e)}")
        
        if st.session_state.debug_mode:
            st.error("Stack trace:")
            st.code(traceback.format_exc())

def main():
    st.title("🔍 ChatGPT Conversation Search")
    
    # Sidebar with debug controls
    with st.sidebar:
        st.header("Settings")
        st.session_state.debug_mode = st.checkbox(
            "Debug Mode", 
            value=st.session_state.debug_mode,
            help="Show raw API responses and detailed error information"
        )
        
        if st.session_state.debug_mode:
            st.info(f"API URL: {API_URL}")
        
        # Search history
        if st.session_state.search_history:
            st.header("Recent Searches")
            for hist_idx, hist_query in enumerate(reversed(st.session_state.search_history[-10:])):
                if st.button(hist_query, key=f"hist_{hist_idx}"):
                    st.session_state.query_rerun = hist_query
    
    # Main search interface
    query = st.text_input(
        "Search conversations", 
        value=st.session_state.get('query_rerun', ''),
        placeholder="Enter search terms..."
    )
    
    # Clear rerun query after use
    if 'query_rerun' in st.session_state:
        del st.session_state.query_rerun
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        search_button = st.button("🔍 Search", type="primary", use_container_width=True)
    
    with col2:
        clear_button = st.button("🗑️ Clear Results", use_container_width=True)
    
    with col3:
        if st.session_state.current_results:
            st.download_button(
                "📥 Export JSON",
                data=json.dumps(st.session_state.current_results, indent=2),
                file_name=f"search_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True
            )
    
    if clear_button:
        st.session_state.current_results = None
        st.rerun()
    
    if search_button and query:
        # Add to search history
        if query not in st.session_state.search_history:
            st.session_state.search_history.append(query)
        
        with st.spinner("Searching..."):
            try:
                # Log the request
                logger.info(f"Searching for: {query}")
                logger.info(f"Making API call to {API_URL}/search")
                
                # Make API request
                response = requests.get(
                    f"{API_URL}/search", 
                    params={"q": query}, 
                    timeout=10
                )
                
                # Log response details
                logger.info(f"Response status: {response.status_code}")
                
                if st.session_state.debug_mode:
                    st.info(f"API Response Status: {response.status_code}")
                    st.info(f"Response Headers: {dict(response.headers)}")
                
                response.raise_for_status()
                data = response.json()
                
                # Log response data
                logger.info(f"Response data keys: {list(data.keys())}")
                if 'results' in data:
                    logger.info(f"Number of results: {len(data.get('results', []))}")
                
                if st.session_state.debug_mode:
                    with st.expander("Raw API Response"):
                        st.json(data)
                
                # Check for API errors
                if data.get('error'):
                    st.error(f"API Error: {data['error']}")
                    logger.error(f"API returned error: {data['error']}")
                else:
                    # Store results in session state
                    st.session_state.current_results = data.get('results', [])
                    
                    # Display results
                    display_search_results(data)
            
            except requests.exceptions.Timeout:
                error_msg = "Search request timed out. Please try again."
                st.error(error_msg)
                logger.error(error_msg)
            
            except requests.exceptions.ConnectionError:
                error_msg = f"Failed to connect to API at {API_URL}. Please check if the service is running."
                st.error(error_msg)
                logger.error(error_msg)
            
            except requests.exceptions.RequestException as e:
                error_msg = f"Search request failed: {str(e)}"
                st.error(error_msg)
                logger.exception("API request failed")
                
                if st.session_state.debug_mode:
                    st.error("Stack trace:")
                    st.code(traceback.format_exc())
            
            except json.JSONDecodeError as e:
                error_msg = f"Failed to parse API response: {str(e)}"
                st.error(error_msg)
                logger.error(error_msg)
                
                if st.session_state.debug_mode:
                    st.error("Raw response:")
                    st.code(response.text if 'response' in locals() else "No response available")
            
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                st.error(error_msg)
                logger.exception("Unexpected error during search")
                
                if st.session_state.debug_mode:
                    st.error("Stack trace:")
                    st.code(traceback.format_exc())
    
    # Display existing results if available
    elif st.session_state.current_results and not search_button:
        st.info("Showing previous search results")
        display_search_results({'results': st.session_state.current_results})

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error in Streamlit app")
        st.error(f"Fatal error: {str(e)}")
        if DEBUG_MODE:
            st.error("Stack trace:")
            st.code(traceback.format_exc())
