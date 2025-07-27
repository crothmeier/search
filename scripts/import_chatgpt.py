#!/usr/bin/env python3
"""Import ChatGPT conversations from JSON export to SQLite database."""
import sys
import time
import logging
import argparse
from pathlib import Path
from typing import List, Tuple
from datetime import datetime
import os

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tqdm import tqdm
from dotenv import load_dotenv
from src.streaming_parser import StreamingJSONParser, Conversation
from src.database import DatabaseManager

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ChatGPTImporter:
    """Import ChatGPT conversations to database."""
    
    def __init__(self, db_path: str, batch_size: int = 1000):
        """Initialize importer with database path."""
        self.db = DatabaseManager(db_path)
        self.batch_size = batch_size
        self.stats = {
            'conversations': 0,
            'messages': 0,
            'errors': 0
        }
    
    def import_file(self, json_path: Path) -> dict:
        """
        Import ChatGPT export file to database.
        
        Args:
            json_path: Path to ChatGPT export JSON file
            
        Returns:
            Import statistics
        """
        start_time = time.time()
        parser = StreamingJSONParser(json_path)
        
        # Get file info
        file_info = parser.get_file_info()
        logger.info(f"Starting import of {file_info['path']} ({file_info['size_mb']:.1f} MB)")
        
        # Count conversations for progress bar
        logger.info("Counting conversations...")
        total_conversations = parser.count_conversations()
        logger.info(f"Found {total_conversations} conversations to import")
        
        # Process conversations with progress bar
        with tqdm(total=total_conversations, desc="Importing conversations") as pbar:
            batch = []
            
            for conversation in parser.parse_conversations():
                try:
                    # Convert conversation to database format
                    messages = self._prepare_messages(conversation)
                    
                    if messages:
                        batch.append((conversation, messages))
                        
                        # Process batch when full
                        if len(batch) >= self.batch_size:
                            self._process_batch(batch)
                            batch = []
                    
                    pbar.update(1)
                    
                except Exception as e:
                    logger.error(f"Error processing conversation {conversation.id}: {e}")
                    self.stats['errors'] += 1
                    pbar.update(1)
            
            # Process remaining batch
            if batch:
                self._process_batch(batch)
        
        # Record import in database
        duration = time.time() - start_time
        self.db.record_import(
            str(json_path),
            int(file_info['size_mb'] * 1024 * 1024),
            self.stats['conversations'],
            self.stats['messages'],
            duration
        )
        
        # Optimize database after import
        logger.info("Optimizing database...")
        self.db.optimize()
        
        # Final stats
        self.stats['duration'] = duration
        self.stats['file_size_mb'] = file_info['size_mb']
        
        logger.info(f"Import completed in {duration:.1f} seconds")
        logger.info(f"Imported {self.stats['conversations']} conversations with {self.stats['messages']} messages")
        if self.stats['errors']:
            logger.warning(f"Encountered {self.stats['errors']} errors during import")
        
        return self.stats
    
    def _prepare_messages(self, conversation: Conversation) -> List[Tuple[str, str, datetime]]:
        """
        Prepare messages for database insertion.
        
        Returns:
            List of (sender, content, timestamp) tuples
        """
        messages = []
        
        for msg in conversation.messages:
            # Skip empty messages
            if not msg.content or not msg.content.strip():
                continue
            
            # Format sender role
            sender = msg.author_role.capitalize()
            
            messages.append((sender, msg.content, msg.create_time))
        
        return messages
    
    def _process_batch(self, batch: list):
        """Process a batch of conversations."""
        for conversation, messages in batch:
            try:
                msg_count = self.db.insert_conversation(
                    conversation.id,
                    conversation.title,
                    messages
                )
                self.stats['conversations'] += 1
                self.stats['messages'] += msg_count
            except Exception as e:
                logger.error(f"Error inserting conversation {conversation.id}: {e}")
                self.stats['errors'] += 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Import ChatGPT conversations from JSON export"
    )
    parser.add_argument(
        'json_file',
        type=Path,
        help='Path to ChatGPT JSON export file'
    )
    parser.add_argument(
        '--db-path',
        type=str,
        default=os.getenv('DATABASE_PATH', 'data/conversations.db'),
        help='Path to SQLite database (default: from .env or data/conversations.db)'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=int(os.getenv('BATCH_SIZE', '1000')),
        help='Batch size for database inserts (default: 1000)'
    )
    parser.add_argument(
        '--clean',
        action='store_true',
        help='Clean existing database before import'
    )
    
    args = parser.parse_args()
    
    # Validate input file
    if not args.json_file.exists():
        logger.error(f"File not found: {args.json_file}")
        sys.exit(1)
    
    # Clean database if requested
    if args.clean:
        db_path = Path(args.db_path)
        if db_path.exists():
            logger.warning(f"Removing existing database: {db_path}")
            db_path.unlink()
    
    # Run import
    importer = ChatGPTImporter(args.db_path, args.batch_size)
    
    try:
        stats = importer.import_file(args.json_file)
        
        # Print summary
        print("\nImport Summary:")
        print(f"  File: {args.json_file} ({stats['file_size_mb']:.1f} MB)")
        print(f"  Conversations: {stats['conversations']:,}")
        print(f"  Messages: {stats['messages']:,}")
        print(f"  Errors: {stats['errors']:,}")
        print(f"  Duration: {stats['duration']:.1f} seconds")
        print(f"  Database: {args.db_path}")
        
    except KeyboardInterrupt:
        logger.warning("Import interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Import failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()