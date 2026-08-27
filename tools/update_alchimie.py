#!/usr/bin/env python3
"""
CLI utility for the Alchimie Library Manager.
Provides commands: status, validate, preview, merge, rollback
"""

import argparse
import json
import os
import sys
from alchimie_library_manager import AlchimieLibraryManager

def cmd_status(args):
    """Show the current status of the Alchimie library."""
    manager = AlchimieLibraryManager(args.library_dir)
    status = manager.get_library_status()
    print(json.dumps(status, indent=2))

def cmd_validate(args):
    """Validate incoming data against the library schemas."""
    manager = AlchimieLibraryManager(args.library_dir)
    
    with open(args.data_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    schema_type = args.schema_type
    is_valid, error_msg = manager.validate_against_schema(data, schema_type)
    
    if is_valid:
        print(f"Validation passed for {schema_type}.")
    else:
        print(f"Validation failed for {schema_type}: {error_msg}")
        sys.exit(1)

def cmd_preview(args):
    """Preview the merge of incoming data without applying it."""
    manager = AlchimieLibraryManager(args.library_dir)
    
    with open(args.data_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    schema_type = args.schema_type
    
    merged_rules = None
    merged_transformations = None
    merged_domains = None
    
    if schema_type == "rules":
        success, message, merged_rules, conflicts = manager.merge_rules(data.get("rules", []))
    elif schema_type == "transformations":
        success, message, merged_transformations, conflicts = manager.merge_transformations(data.get("transformations", []))
    elif schema_type == "domains":
        success, message, merged_domains, conflicts = manager.merge_domains(data.get("domains", []))
    else:
        print(f"Unknown schema type: {schema_type}")
        sys.exit(1)
        
    if conflicts:
        print(f"Preview: Found {len(conflicts)} conflicts that would require human review.")
        print(json.dumps(conflicts, indent=2))
    else:
        print(f"Preview: No conflicts found. Merge would be successful.")
        if merged_rules is not None:
            print(f"Merged rules count: {len(merged_rules)}")
        elif merged_transformations is not None:
            print(f"Merged transformations count: {len(merged_transformations)}")
        elif merged_domains is not None:
            print(f"Merged domains count: {len(merged_domains)}")

def cmd_merge(args):
    """Merge incoming data into the canonical library."""
    manager = AlchimieLibraryManager(args.library_dir)
    
    with open(args.data_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    schema_type = args.schema_type
    
    merged_rules = None
    merged_transformations = None
    merged_domains = None
    
    if schema_type == "rules":
        success, message, merged_rules, conflicts = manager.merge_rules(data.get("rules", []))
    elif schema_type == "transformations":
        success, message, merged_transformations, conflicts = manager.merge_transformations(data.get("transformations", []))
    elif schema_type == "domains":
        success, message, merged_domains, conflicts = manager.merge_domains(data.get("domains", []))
    else:
        print(f"Unknown schema type: {schema_type}")
        sys.exit(1)
        
    if not success:
        print(f"Merge failed: {message}")
        if conflicts:
            print("Conflicts require human review. Use the GUI or CLI to resolve conflicts.")
            print(json.dumps(conflicts, indent=2))
        sys.exit(1)
        
    print(f"Merge successful: {message}")

def cmd_rollback(args):
    """Rollback the library to a specific backup state."""
    manager = AlchimieLibraryManager(args.library_dir)
    
    success = manager.rollback(args.backup_dir)
    if success:
        print(f"Rollback successful to backup: {args.backup_dir}")
    else:
        print(f"Rollback failed. Backup directory not found: {args.backup_dir}")
        sys.exit(1)

def cmd_archive(args):
    """Archive a rule or transformation to the archive directory."""
    manager = AlchimieLibraryManager(args.library_dir)
    
    try:
        success = manager.archive_item(args.item_type, args.item_id, args.reason)
        if success:
            print(f"Successfully archived {args.item_type} {args.item_id}. Reason: {args.reason}")
        else:
            print(f"Failed to archive {args.item_type} {args.item_id}.")
            sys.exit(1)
    except Exception as e:
        print(f"Archive failed: {str(e)}")
        sys.exit(1)

def cmd_lookup(args):
    """Lookup a rule with provenance information."""
    manager = AlchimieLibraryManager(args.library_dir)
    
    rule = manager.lookup_rule_with_provenance(args.rule_id)
    if rule:
        print(json.dumps(rule, indent=2))
    else:
        print(f"Rule {args.rule_id} not found.")
        sys.exit(1)

def cmd_experimental(args):
    """Get all experimental rules or hypotheses from the library."""
    manager = AlchimieLibraryManager(args.library_dir)
    
    experimental_rules = manager.get_experimental_rules()
    print(json.dumps(experimental_rules, indent=2))

def cmd_validated(args):
    """Get all validated rules from the library."""
    manager = AlchimieLibraryManager(args.library_dir)
    
    validated_rules = manager.get_validated_rules()
    print(json.dumps(validated_rules, indent=2))

def cmd_cache(args):
    """Invalidate the library cache."""
    manager = AlchimieLibraryManager(args.library_dir)
    
    manager.invalidate_cache()
    print("Cache invalidated successfully.")

def main():
    parser = argparse.ArgumentParser(description="Alchimie Library Manager CLI Utility")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # status command
    status_parser = subparsers.add_parser("status", help="Show library status")
    status_parser.add_argument("--library-dir", default="~/hermes/CaptN-BRAIN/alchimie", help="Path to the alchimie library directory")
    status_parser.set_defaults(func=cmd_status)

    # validate command
    validate_parser = subparsers.add_parser("validate", help="Validate incoming data against schemas")
    validate_parser.add_argument("--library-dir", default="~/hermes/CaptN-BRAIN/alchimie", help="Path to the alchimie library directory")
    validate_parser.add_argument("--data-file", required=True, help="Path to the JSON data file to validate")
    validate_parser.add_argument("--schema-type", required=True, choices=["rules", "transformations", "domains"], help="Type of schema to validate against")
    validate_parser.set_defaults(func=cmd_validate)

    # preview command
    preview_parser = subparsers.add_parser("preview", help="Preview merge without applying")
    preview_parser.add_argument("--library-dir", default="~/hermes/CaptN-BRAIN/alchimie", help="Path to the alchimie library directory")
    preview_parser.add_argument("--data-file", required=True, help="Path to the JSON data file to preview merge for")
    preview_parser.add_argument("--schema-type", required=True, choices=["rules", "transformations", "domains"], help="Type of schema to preview merge for")
    preview_parser.set_defaults(func=cmd_preview)

    # merge command
    merge_parser = subparsers.add_parser("merge", help="Merge incoming data into the canonical library")
    merge_parser.add_argument("--library-dir", default="~/hermes/CaptN-BRAIN/alchimie", help="Path to the alchimie library directory")
    merge_parser.add_argument("--data-file", required=True, help="Path to the JSON data file to merge")
    merge_parser.add_argument("--schema-type", required=True, choices=["rules", "transformations", "domains"], help="Type of schema to merge")
    merge_parser.set_defaults(func=cmd_merge)

    # rollback command
    rollback_parser = subparsers.add_parser("rollback", help="Rollback to a specific backup state")
    rollback_parser.add_argument("--library-dir", default="~/hermes/CaptN-BRAIN/alchimie", help="Path to the alchimie library directory")
    rollback_parser.add_argument("--backup-dir", required=True, help="Path to the backup directory to rollback to")
    rollback_parser.set_defaults(func=cmd_rollback)

    # archive command
    archive_parser = subparsers.add_parser("archive", help="Archive a rule or transformation")
    archive_parser.add_argument("--library-dir", default="~/hermes/CaptN-BRAIN/alchimie", help="Path to the alchimie library directory")
    archive_parser.add_argument("--item-type", required=True, choices=["rule", "transformation"], help="Type of item to archive")
    archive_parser.add_argument("--item-id", required=True, help="ID of the item to archive")
    archive_parser.add_argument("--reason", required=True, help="Reason for archiving")
    archive_parser.set_defaults(func=cmd_archive)

    # lookup command
    lookup_parser = subparsers.add_parser("lookup", help="Lookup a rule with provenance information")
    lookup_parser.add_argument("--library-dir", default="~/hermes/CaptN-BRAIN/alchimie", help="Path to the alchimie library directory")
    lookup_parser.add_argument("--rule-id", required=True, help="ID of the rule to lookup")
    lookup_parser.set_defaults(func=cmd_lookup)

    # experimental command
    experimental_parser = subparsers.add_parser("experimental", help="Get all experimental rules or hypotheses")
    experimental_parser.add_argument("--library-dir", default="~/hermes/CaptN-BRAIN/alchimie", help="Path to the alchimie library directory")
    experimental_parser.set_defaults(func=cmd_experimental)

    # validated command
    validated_parser = subparsers.add_parser("validated", help="Get all validated rules from the library")
    validated_parser.add_argument("--library-dir", default="~/hermes/CaptN-BRAIN/alchimie", help="Path to the alchimie library directory")
    validated_parser.set_defaults(func=cmd_validated)

    # cache command
    cache_parser = subparsers.add_parser("cache", help="Invalidate the library cache")
    cache_parser.add_argument("--library-dir", default="~/hermes/CaptN-BRAIN/alchimie", help="Path to the alchimie library directory")
    cache_parser.set_defaults(func=cmd_cache)

    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
        
    # Expand ~ to home directory for library-dir
    if hasattr(args, 'library_dir'):
        args.library_dir = os.path.expanduser(args.library_dir)
        
    # Execute the command
    args.func(args)

if __name__ == "__main__":
    main()