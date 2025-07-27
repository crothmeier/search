"""Streaming JSON parser for large ChatGPT export files."""
import ijson
import logging
from pathlib import Path
from typing import Iterator, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Represents a single message in a conversation."""
    id: str
    author_role: str
    content: str
    create_time: datetime
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Message':
        """Create Message from ChatGPT export format."""
        content_parts = data.get('content', {}).get('parts', [])
        content = ' '.join(str(part) for part in content_parts if part)
        
        return cls(
            id=data.get('id', ''),
            author_role=data.get('author', {}).get('role', 'unknown'),
            content=content,
            create_time=datetime.fromtimestamp(data.get('create_time', 0))
        )


@dataclass
class Conversation:
    """Represents a ChatGPT conversation."""
    id: str
    title: str
    create_time: datetime
    update_time: datetime
    messages: list[Message]
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Conversation':
        """Create Conversation from ChatGPT export format."""
        messages = [
            Message.from_dict(msg) 
            for msg in data.get('messages', [])
            if msg and isinstance(msg, dict)
        ]
        
        return cls(
            id=data.get('id', ''),
            title=data.get('title', 'Untitled'),
            create_time=datetime.fromtimestamp(data.get('create_time', 0)),
            update_time=datetime.fromtimestamp(data.get('update_time', 0)),
            messages=messages
        )


class StreamingJSONParser:
    """Parse large ChatGPT JSON exports without loading entire file into memory."""
    
    def __init__(self, file_path: Path):
        """Initialize parser with file path."""
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
    
    def parse_conversations(self) -> Iterator[Conversation]:
        """
        Stream conversations from ChatGPT export file.
        
        Yields:
            Conversation objects parsed from the JSON file.
        """
        try:
            with open(self.file_path, 'rb') as file:
                parser = ijson.items(file, 'item')
                
                for conversation_data in parser:
                    if not isinstance(conversation_data, dict):
                        logger.warning("Skipping non-dict conversation data")
                        continue
                    
                    try:
                        conversation = Conversation.from_dict(conversation_data)
                        if conversation.messages:  # Only yield conversations with messages
                            yield conversation
                    except Exception as e:
                        logger.error(f"Error parsing conversation: {e}")
                        continue
                        
        except Exception as e:
            logger.error(f"Error reading file {self.file_path}: {e}")
            raise
    
    def count_conversations(self) -> int:
        """Count total conversations in the file."""
        count = 0
        try:
            with open(self.file_path, 'rb') as file:
                parser = ijson.items(file, 'item')
                for _ in parser:
                    count += 1
        except Exception as e:
            logger.error(f"Error counting conversations: {e}")
            raise
        return count
    
    def get_file_info(self) -> Dict[str, Any]:
        """Get basic information about the export file."""
        stat = self.file_path.stat()
        return {
            'path': str(self.file_path),
            'size_mb': stat.st_size / (1024 * 1024),
            'modified': datetime.fromtimestamp(stat.st_mtime),
            'exists': self.file_path.exists()
        }