#!/usr/bin/env python3
"""Database migration script for adding security enhancements."""
import sqlite3
import logging
import argparse
import sys
from pathlib import Path
from datetime import datetime
import shutil
from typing import Dict

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DatabaseMigration:
    """Handle database schema migrations safely."""
    
    def __init__(self, db_path: str):
        """Initialize migration with database path."""
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")
        
        self.backup_path = None
    
    def create_backup(self) -> Path:
        """Create database backup before migration."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = self.db_path.with_suffix(f'.backup_{timestamp}.db')
        
        logger.info(f"Creating backup: {backup_path}")
        shutil.copy2(self.db_path, backup_path)
        
        self.backup_path = backup_path
        return backup_path
    
    def check_migration_needed(self) -> Dict[str, bool]:
        """Check which migrations are needed."""
        needs = {
            'search_audit_table': False,
            'checksum_column': False,
            'file_checksum_column': False,
            'indexes': False
        }
        
        with sqlite3.connect(str(self.db_path)) as conn:
            # Check for search_audit table
            result = conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='search_audit'
            """).fetchone()
            needs['search_audit_table'] = result is None
            
            # Check for checksum column in metadata
            columns = conn.execute("PRAGMA table_info(metadata)").fetchall()
            column_names = [col[1] for col in columns]
            needs['checksum_column'] = 'checksum' not in column_names
            
            # Check for file_checksum in import_history
            if conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='import_history'
            """).fetchone():
                columns = conn.execute("PRAGMA table_info(import_history)").fetchall()
                column_names = [col[1] for col in columns]
                needs['file_checksum_column'] = 'file_checksum' not in column_names
            
            # Check for indexes
            indexes = conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='index'
            """).fetchall()
            index_names = [idx[0] for idx in indexes]
            
            required_indexes = [
                'idx_metadata_checksum',
                'idx_audit_searched_at',
                'idx_audit_query_hash',
                'idx_audit_user_id',
                'idx_import_checksum'
            ]
            
            missing_indexes = [idx for idx in required_indexes if idx not in index_names]
            needs['indexes'] = len(missing_indexes) > 0
        
        return needs
    
    def migrate(self, dry_run: bool = False):
        """Run database migration."""
        needs = self.check_migration_needed()
        
        if not any(needs.values()):
            logger.info("Database is already up to date, no migration needed")
            return
        
        logger.info("Migration needed for: " + ", ".join(k for k, v in needs.items() if v))
        
        if dry_run:
            logger.info("Dry run mode - no changes will be made")
            return
        
        # Create backup
        self.create_backup()
        
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                # Enable foreign keys
                conn.execute("PRAGMA foreign_keys=ON")
                
                # Add search_audit table
                if needs['search_audit_table']:
                    logger.info("Creating search_audit table...")
                    conn.execute("""
                        CREATE TABLE search_audit (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            query_hash TEXT NOT NULL,
                            user_id TEXT,
                            query_length INTEGER NOT NULL,
                            from_cache BOOLEAN DEFAULT 0,
                            duration_ms REAL,
                            error TEXT,
                            searched_at TEXT DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    logger.info("Created search_audit table")
                
                # Add checksum column to metadata
                if needs['checksum_column']:
                    logger.info("Adding checksum column to metadata...")
                    conn.execute("ALTER TABLE metadata ADD COLUMN checksum TEXT")
                    
                    # Add indexed_at column if missing
                    try:
                        conn.execute("ALTER TABLE metadata ADD COLUMN indexed_at TEXT DEFAULT CURRENT_TIMESTAMP")
                    except sqlite3.OperationalError:
                        pass  # Column might already exist
                    
                    logger.info("Added checksum column to metadata")
                
                # Add file_checksum to import_history
                if needs['file_checksum_column']:
                    logger.info("Adding file_checksum column to import_history...")
                    conn.execute("ALTER TABLE import_history ADD COLUMN file_checksum TEXT")
                    logger.info("Added file_checksum column to import_history")
                
                # Create missing indexes
                if needs['indexes']:
                    logger.info("Creating missing indexes...")
                    
                    index_definitions = [
                        # Metadata indexes
                        ("idx_metadata_checksum", "CREATE INDEX IF NOT EXISTS idx_metadata_checksum ON metadata(checksum)"),
                        
                        # Search audit indexes
                        ("idx_audit_searched_at", "CREATE INDEX IF NOT EXISTS idx_audit_searched_at ON search_audit(searched_at DESC)"),
                        ("idx_audit_query_hash", "CREATE INDEX IF NOT EXISTS idx_audit_query_hash ON search_audit(query_hash)"),
                        ("idx_audit_user_id", "CREATE INDEX IF NOT EXISTS idx_audit_user_id ON search_audit(user_id)"),
                        
                        # Import history indexes
                        ("idx_import_checksum", "CREATE INDEX IF NOT EXISTS idx_import_checksum ON import_history(file_checksum)"),
                        ("idx_import_date", "CREATE INDEX IF NOT EXISTS idx_import_date ON import_history(imported_at DESC)")
                    ]
                    
                    for idx_name, idx_sql in index_definitions:
                        try:
                            conn.execute(idx_sql)
                            logger.info(f"Created index: {idx_name}")
                        except sqlite3.OperationalError as e:
                            if "already exists" not in str(e):
                                raise
                
                # Update existing data
                logger.info("Updating existing data...")
                
                # Calculate checksums for existing conversations
                if needs['checksum_column']:
                    logger.info("Calculating checksums for existing conversations...")
                    
                    conversations = conn.execute("""
                        SELECT DISTINCT conversation_id 
                        FROM metadata 
                        WHERE checksum IS NULL
                    """).fetchall()
                    
                    for conv in conversations:
                        conv_id = conv[0]
                        
                        # Get all messages for checksum calculation
                        messages = conn.execute("""
                            SELECT sender, content, timestamp
                            FROM conversations
                            WHERE conversation_id = ?
                            ORDER BY timestamp
                        """, (conv_id,)).fetchall()
                        
                        if messages:
                            # Simple checksum based on message count and content length
                            import hashlib
                            hasher = hashlib.sha256()
                            for msg in messages:
                                hasher.update(f"{msg[0]}:{msg[1]}:{msg[2]}".encode('utf-8'))
                            checksum = hasher.hexdigest()
                            
                            conn.execute("""
                                UPDATE metadata 
                                SET checksum = ? 
                                WHERE conversation_id = ?
                            """, (checksum, conv_id))
                    
                    logger.info(f"Updated checksums for {len(conversations)} conversations")
                
                # Commit all changes
                conn.commit()
                
                # Optimize database
                logger.info("Optimizing database...")
                conn.execute("ANALYZE")
                conn.execute("VACUUM")
                
                logger.info("Migration completed successfully!")
                
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            if self.backup_path:
                logger.info(f"Database backup available at: {self.backup_path}")
            raise
    
    def rollback(self):
        """Rollback to backup if available."""
        if not self.backup_path or not self.backup_path.exists():
            logger.error("No backup available for rollback")
            return False
        
        logger.info(f"Rolling back to backup: {self.backup_path}")
        shutil.copy2(self.backup_path, self.db_path)
        logger.info("Rollback completed")
        return True


def main():
    """Main migration entry point."""
    parser = argparse.ArgumentParser(description='Migrate database schema for security enhancements')
    parser.add_argument('database', help='Path to SQLite database file')
    parser.add_argument('--dry-run', action='store_true', help='Check what would be migrated without making changes')
    parser.add_argument('--no-backup', action='store_true', help='Skip creating backup (not recommended)')
    
    args = parser.parse_args()
    
    try:
        migration = DatabaseMigration(args.database)
        
        # Check what needs migration
        needs = migration.check_migration_needed()
        
        if args.dry_run:
            logger.info("Dry run - checking migration requirements:")
            for item, needed in needs.items():
                status = "NEEDED" if needed else "OK"
                logger.info(f"  {item}: {status}")
            return
        
        # Run migration
        migration.migrate(dry_run=args.no_backup)
        
    except FileNotFoundError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()