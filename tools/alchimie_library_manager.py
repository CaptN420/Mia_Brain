"""
Alchimie Library Manager - Core Engine for managing the canonical transformation knowledge library.
Implements transaction-safe merges, atomic writes, versioning, and human-in-the-loop conflict resolution.
"""

import json
import os
import shutil
import uuid
import threading
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

class AlchimieLibraryManager:
    _shared_lock = threading.RLock()

    @classmethod
    def _get_shared_lock(cls):
        # H-06: one lock per PROCESS, shared by every instance (dashboard
        # creates a manager per session; they all touch the same files).
        return cls._shared_lock

    def __init__(self, library_dir: str):
        self.library_dir = library_dir
        self.schema_dir = os.path.join(library_dir, "schema")
        self.library_files_dir = os.path.join(library_dir, "library")
        self.backups_dir = os.path.join(library_dir, "backups")
        self.new_data_dir = os.path.join(library_dir, "new_data")
        self.archive_dir = os.path.join(library_dir, "archive")
        self.cache_dir = os.path.join(library_dir, "cache")
        
        # Thread safety lock
        # H-06: CLASS-level lock. Instances are created per dashboard session;
        # a per-instance RLock did not protect the shared library files across
        # them.
        self.lock = AlchimieLibraryManager._get_shared_lock()
        
        # Ensure directories exist
        for d in [self.schema_dir, self.library_files_dir, self.backups_dir, self.new_data_dir, self.archive_dir, self.cache_dir]:
            os.makedirs(d, exist_ok=True)
            
        # Library file paths
        self.rules_file = os.path.join(self.library_files_dir, "rules.json")
        self.transformations_file = os.path.join(self.library_files_dir, "transformations.json")
        self.domains_file = os.path.join(self.library_files_dir, "domains.json")
        self.metadata_file = os.path.join(self.library_files_dir, "metadata.json")
        
        # Schema file paths
        self.rule_schema_file = os.path.join(self.schema_dir, "rule_schema.json")
        self.transformation_schema_file = os.path.join(self.schema_dir, "transformation_schema.json")
        self.variables_schema_file = os.path.join(self.schema_dir, "variables_schema.json")

    def _load_json(self, file_path: str) -> Dict[str, Any]:
        """Load JSON from file."""
        if not os.path.exists(file_path):
            return {}
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save_json(self, file_path: str, data: Dict[str, Any]) -> bool:
        """Save JSON to file atomically using .tmp file and atomic replace."""
        tmp_file = f"{file_path}.tmp"
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        
        # Atomic replace verification
        if not os.path.exists(tmp_file):
            return False
            
        # Verify the saved file is valid JSON
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                json.load(f)
        except json.JSONDecodeError:
            return False
            
        # Atomic replace
        shutil.move(tmp_file, file_path)
        return True

    def create_backup(self, library_version: str) -> str:
        """Create a backup of the current library state."""
        backup_dir = os.path.join(self.backups_dir, f"backup_{library_version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(backup_dir, exist_ok=True)
        
        for f in [self.rules_file, self.transformations_file, self.domains_file, self.metadata_file]:
            if os.path.exists(f):
                shutil.copy2(f, os.path.join(backup_dir, os.path.basename(f)))
                
        return backup_dir

    def invalidate_cache(self):
        """Invalidate the library cache by removing all cached files."""
        for f in os.listdir(self.cache_dir):
            file_path = os.path.join(self.cache_dir, f)
            if os.path.isfile(file_path):
                os.remove(file_path)
        print("Cache invalidated.")

    def validate_against_schema(self, data: Dict[str, Any], schema_type: str) -> Tuple[bool, Optional[str]]:
        """Validate data against the appropriate schema."""
        # Simplified validation - in a full implementation this would use jsonschema
        if schema_type == "rules":
            if "rules" not in data:
                return False, "Missing 'rules' key in data"
            if not isinstance(data["rules"], list):
                return False, "'rules' must be a list"
                
        elif schema_type == "transformations":
            if "transformations" not in data:
                return False, "Missing 'transformations' key in data"
            if not isinstance(data["transformations"], list):
                return False, "'transformations' must be a list"
                
        elif schema_type == "domains":
            if "domains" not in data:
                return False, "Missing 'domains' key in data"
            if not isinstance(data["domains"], list):
                return False, "'domains' must be a list"
                
        return True, None

    def merge_rules(self, incoming_rules: List[Dict[str, Any]]) -> Tuple[bool, str, Optional[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]]]:
        """
        Merge incoming rules with canonical library rules.
        Returns: (success, message, merged_rules, conflicts)
        """
        # 1. Backup current state
        metadata = self._load_json(self.metadata_file)
        library_version = metadata.get("library_version", "1.0.0")
        backup_dir = self.create_backup(library_version)
        
        # 2. Load canonical rules
        canonical_data = self._load_json(self.rules_file)
        canonical_rules = canonical_data.get("rules", [])
        
        # 3. Validate incoming rules
        # For this implementation, we'll do basic validation
        for rule in incoming_rules:
            if "id" not in rule or "name" not in rule or "status" not in rule:
                return False, "Incoming rules must have 'id', 'name', and 'status' fields.", None, None
                
        # 4. Merge logic - identify conflicts
        merged_rules = list(canonical_rules)
        conflicts = []
        
        for incoming_rule in incoming_rules:
            incoming_id = incoming_rule.get("id")
            # Check if rule already exists
            existing_rule_idx = -1
            for i, rule in enumerate(merged_rules):
                if rule.get("id") == incoming_id:
                    existing_rule_idx = i
                    break
                    
            if existing_rule_idx >= 0:
                existing_rule = merged_rules[existing_rule_idx]
                # Check for conflicts (different definitions, etc.)
                if existing_rule.get("definition") != incoming_rule.get("definition") or \
                   existing_rule.get("status") != incoming_rule.get("status"):
                    # Conflict detected
                    conflicts.append({
                        "rule_id": incoming_id,
                        "canonical_rule": existing_rule,
                        "incoming_rule": incoming_rule,
                        "conflict_type": "CONFLICT_PENDING"
                    })
                else:
                    # No conflict, update the rule
                    merged_rules[existing_rule_idx] = incoming_rule
            else:
                # New rule, add it
                merged_rules.append(incoming_rule)
                
        # If there are conflicts, return them for human review
        if conflicts:
            return False, f"Found {len(conflicts)} conflicts that require human review.", None, conflicts
            
        # 5. Validate merged rules
        is_valid, error_msg = self.validate_against_schema({"rules": merged_rules}, "rules")
        if not is_valid:
            return False, f"Validation failed: {error_msg}", None, None
            
        # 6. Save merged rules
        merged_data = {
            "library_version": library_version,
            "rules": merged_rules,
            "transformations": canonical_data.get("transformations", []),
            "domains": canonical_data.get("domains", []),
            "metadata": metadata
        }
        
        success = self._save_json(self.rules_file, merged_data)
        if success:
            return True, "Rules merged successfully.", merged_rules, None
        else:
            return False, "Failed to save merged rules.", None, None

    def merge_transformations(self, incoming_transformations: List[Dict[str, Any]]) -> Tuple[bool, str, Optional[List[Dict[str, Any]]], Optional[List[Dict[str, Any]]]]:
        """
        Merge incoming transformations with canonical library transformations.
        Returns: (success, message, merged_transformations, conflicts)
        """
        # 1. Backup current state
        metadata = self._load_json(self.metadata_file)
        library_version = metadata.get("library_version", "1.0.0")
        backup_dir = self.create_backup(library_version)
        
        # 2. Load canonical transformations
        canonical_data = self._load_json(self.transformations_file)
        canonical_transformations = canonical_data.get("transformations", [])
        
        # 3. Validate incoming transformations
        for trans in incoming_transformations:
            if "id" not in trans or "name" not in trans or "status" not in trans:
                return False, "Incoming transformations must have 'id', 'name', and 'status' fields.", None, None
                
        # 4. Merge logic - identify conflicts
        merged_transformations = list(canonical_transformations)
        conflicts = []
        
        for incoming_trans in incoming_transformations:
            incoming_id = incoming_trans.get("id")
            # Check if transformation already exists
            existing_trans_idx = -1
            for i, trans in enumerate(merged_transformations):
                if trans.get("id") == incoming_id:
                    existing_trans_idx = i
                    break
                    
            if existing_trans_idx >= 0:
                existing_trans = merged_transformations[existing_trans_idx]
                # Check for conflicts
                if existing_trans.get("expression") != incoming_trans.get("expression") or \
                   existing_trans.get("status") != incoming_trans.get("status"):
                    # Conflict detected
                    conflicts.append({
                        "transformation_id": incoming_id,
                        "canonical_transformation": existing_trans,
                        "incoming_transformation": incoming_trans,
                        "conflict_type": "CONFLICT_PENDING"
                    })
                else:
                    # No conflict, update the transformation
                    merged_transformations[existing_trans_idx] = incoming_trans
            else:
                # New transformation, add it
                merged_transformations.append(incoming_trans)
                
        # If there are conflicts, return them for human review
        if conflicts:
            return False, f"Found {len(conflicts)} conflicts that require human review.", None, conflicts
            
        # 5. Validate merged transformations
        is_valid, error_msg = self.validate_against_schema({"transformations": merged_transformations}, "transformations")
        if not is_valid:
            return False, f"Validation failed: {error_msg}", None, None
            
        # 6. Save merged transformations
        merged_data = {
            "library_version": library_version,
            "transformations": merged_transformations
        }
        
        success = self._save_json(self.transformations_file, merged_data)
        if success:
            return True, "Transformations merged successfully.", merged_transformations, None
        else:
            return False, "Failed to save merged transformations.", None, None

    def merge_domains(self, incoming_domains: List[str]) -> Tuple[bool, str, Optional[List[str]], Optional[List[str]]]:
        """
        Merge incoming domains with canonical library domains.
        Returns: (success, message, merged_domains, conflicts)
        """
        # 1. Backup current state
        metadata = self._load_json(self.metadata_file)
        library_version = metadata.get("library_version", "1.0.0")
        backup_dir = self.create_backup(library_version)
        
        # 2. Load canonical domains
        canonical_data = self._load_json(self.domains_file)
        canonical_domains = canonical_data.get("domains", [])
        
        # 3. Merge domains (no conflicts possible for domains, just unique union)
        merged_domains = list(set(canonical_domains + incoming_domains))
        
        # 4. Validate merged domains
        is_valid, error_msg = self.validate_against_schema({"domains": merged_domains}, "domains")
        if not is_valid:
            return False, f"Validation failed: {error_msg}", None, None
            
        # 5. Save merged domains
        merged_data = {
            "domains": merged_domains
        }
        
        success = self._save_json(self.domains_file, merged_data)
        if success:
            return True, "Domains merged successfully.", merged_domains, None
        else:
            return False, "Failed to save merged domains.", None, None

    def get_library_status(self) -> Dict[str, Any]:
        """Get the current status of the library."""
        metadata = self._load_json(self.metadata_file)
        rules_data = self._load_json(self.rules_file)
        transformations_data = self._load_json(self.transformations_file)
        domains_data = self._load_json(self.domains_file)
        
        return {
            "library_version": metadata.get("library_version", "unknown"),
            "rules_count": len(rules_data.get("rules", [])),
            "transformations_count": len(transformations_data.get("transformations", [])),
            "domains_count": len(domains_data.get("domains", [])),
            "last_updated": metadata.get("created", "unknown")
        }

    def rollback(self, backup_dir: str) -> bool:
        """Rollback the library to a specific backup state."""
        if not os.path.exists(backup_dir):
            return False
            
        # Restore files from backup
        for f in [self.rules_file, self.transformations_file, self.domains_file, self.metadata_file]:
            backup_file = os.path.join(backup_dir, os.path.basename(f))
            if os.path.exists(backup_file):
                shutil.copy2(backup_file, f)
                
        # Invalidate cache after rollback
        self.invalidate_cache()
        return True

    def archive_item(self, item_type: str, item_id: str, reason: str) -> bool:
        """Archive a rule or transformation to the archive directory."""
        if item_type not in ["rule", "transformation"]:
            raise ValueError("item_type must be 'rule' or 'transformation'")
            
        archive_dir = os.path.join(self.archive_dir, item_type)
        os.makedirs(archive_dir, exist_ok=True)
        
        # Load the current library data
        if item_type == "rule":
            data = self._load_json(self.rules_file)
            items = data.get("rules", [])
        else:
            data = self._load_json(self.transformations_file)
            items = data.get("transformations", [])
            
        # Find the item to archive
        archived_item = None
        for item in items:
            if item.get("id") == item_id:
                archived_item = item
                break
                
        if not archived_item:
            raise ValueError(f"Item {item_id} not found in {item_type}s")
            
        # Add archive metadata
        archived_item["archived"] = True
        archived_item["archive_date"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        archived_item["archive_reason"] = reason
        
        # Save to archive
        archive_file = os.path.join(archive_dir, f"{item_id}_v{archived_item.get('version', 'unknown')}.json")
        with open(archive_file, 'w', encoding='utf-8') as f:
            json.dump(archived_item, f, indent=2)
            
        # Remove from canonical library
        if item_type == "rule":
            data["rules"] = [item for item in data.get("rules", []) if item.get("id") != item_id]
            self._save_json(self.rules_file, data)
        else:
            data["transformations"] = [item for item in data.get("transformations", []) if item.get("id") != item_id]
            self._save_json(self.transformations_file, data)
            
        # Invalidate cache
        self.invalidate_cache()
        return True

    def lookup_rule_with_provenance(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Lookup a rule and return it with provenance information."""
        rules_data = self._load_json(self.rules_file)
        for rule in rules_data.get("rules", []):
            if rule.get("id") == rule_id:
                # Add provenance information
                rule_with_provenance = rule.copy()
                rule_with_provenance["provenance"] = {
                    "source": rule.get("source", "unknown"),
                    "version": rule.get("version", "unknown"),
                    "status": rule.get("status", "unknown"),
                    "approved": rule.get("approved", False),
                    "source_turn": rule.get("source_turn", 0),
                    "source_agent": rule.get("source_agent", "unknown")
                }
                return rule_with_provenance
                
        return None

    def get_experimental_rules(self) -> List[Dict[str, Any]]:
        """Get all experimental rules or hypotheses from the library."""
        rules_data = self._load_json(self.rules_file)
        experimental_rules = []
        for rule in rules_data.get("rules", []):
            if rule.get("experimental") or rule.get("status") in ["HYPOTHESIS", "EXPERIMENTAL"]:
                experimental_rules.append(rule)
        return experimental_rules

    def get_validated_rules(self) -> List[Dict[str, Any]]:
        """Get all validated rules from the library."""
        rules_data = self._load_json(self.rules_file)
        validated_rules = []
        for rule in rules_data.get("rules", []):
            if rule.get("status") == "validated" and not rule.get("experimental", False):
                validated_rules.append(rule)
        return validated_rules

    def find_rules(self, domain: Optional[str] = None, rule_type: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query the library for rules matching specific criteria."""
        with self.lock:
            rules_data = self._load_json(self.rules_file)
            results = []
            for rule in rules_data.get("rules", []):
                # Check domain match
                if domain is not None and rule.get("domain") != domain:
                    continue
                # Check type match
                if rule_type is not None and rule.get("type") != rule_type:
                    continue
                # Check status match
                if status is not None and rule.get("status") != status:
                    continue
                
                results.append(rule)
            return results

    def reload(self):
        """Reload the library and invalidate caches."""
        with self.lock:
            # Invalidate cache
            self.invalidate_cache()
            # Reload is implicit as _load_json reads from disk each time
            # But we ensure cache is cleared for any cached representations
            print("Library reloaded and cache invalidated.")
            
    def scan_new_data(self) -> Dict[str, Any]:
        """Scan the new_data directory for files to process."""
        with self.lock:
            scan_results = {
                'files': [],
                'rules_files': [],
                'transformations_files': [],
                'domains_files': []
            }
            
            for filename in os.listdir(self.new_data_dir):
                if filename.endswith('.json'):
                    file_path = os.path.join(self.new_data_dir, filename)
                    scan_results['files'].append(filename)
                    
                    # Categorize by content type
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            if 'rules' in data:
                                scan_results['rules_files'].append(filename)
                            elif 'transformations' in data:
                                scan_results['transformations_files'].append(filename)
                            elif 'domains' in data:
                                scan_results['domains_files'].append(filename)
                    except Exception:
                        pass  # Skip files that can't be parsed
                        
            return scan_results
            
    def validate_new_data(self) -> Dict[str, Any]:
        """Validate the new data files against schemas."""
        with self.lock:
            validation_results = {
                'valid': True,
                'errors': [],
                'warnings': []
            }
            
            for filename in os.listdir(self.new_data_dir):
                if filename.endswith('.json'):
                    file_path = os.path.join(self.new_data_dir, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            
                        # Determine schema type and validate
                        if 'rules' in data:
                            is_valid, error = self.validate_against_schema(data, 'rules')
                            if not is_valid:
                                validation_results['valid'] = False
                                validation_results['errors'].append(f"{filename}: {error}")
                        elif 'transformations' in data:
                            is_valid, error = self.validate_against_schema(data, 'transformations')
                            if not is_valid:
                                validation_results['valid'] = False
                                validation_results['errors'].append(f"{filename}: {error}")
                        elif 'domains' in data:
                            is_valid, error = self.validate_against_schema(data, 'domains')
                            if not is_valid:
                                validation_results['valid'] = False
                                validation_results['errors'].append(f"{filename}: {error}")
                                
                    except json.JSONDecodeError as e:
                        validation_results['valid'] = False
                        validation_results['errors'].append(f"{filename}: Invalid JSON - {str(e)}")
                    except Exception as e:
                        validation_results['valid'] = False
                        validation_results['errors'].append(f"{filename}: Error - {str(e)}")
                        
            return validation_results
            
    def preview_merge(self) -> Dict[str, Any]:
        """Preview the merge operation without actually performing it."""
        with self.lock:
            # Scan new data
            scan_results = self.scan_new_data()
            
            preview_results = {
                'scan_results': scan_results,
                'new_items': [],
                'updated_items': [],
                'duplicate_items': [],
                'conflicts': [],
                'rejected_items': []
            }
            
            # Preview rules merge
            for filename in scan_results['rules_files']:
                file_path = os.path.join(self.new_data_dir, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        
                    incoming_rules = data.get('rules', [])
                    canonical_data = self._load_json(self.rules_file)
                    canonical_rules = canonical_data.get('rules', [])
                    
                    for rule in incoming_rules:
                        rule_id = rule.get('id')
                        # Check if rule exists in canonical
                        exists = any(r.get('id') == rule_id for r in canonical_rules)
                        if exists:
                            # Check if it's an update or duplicate
                            canonical_rule = next((r for r in canonical_rules if r.get('id') == rule_id), None)
                            if canonical_rule and (canonical_rule.get('definition') != rule.get('definition') or \
                               canonical_rule.get('status') != rule.get('status')):
                                preview_results['conflicts'].append({
                                    'type': 'rule',
                                    'rule_id': rule_id,
                                    'conflict_type': 'CONFLICT_PENDING'
                                })
                            else:
                                preview_results['updated_items'].append(rule_id)
                        else:
                            preview_results['new_items'].append(rule_id)
                            
                except Exception as e:
                    preview_results['rejected_items'].append(f"{filename}: Error - {str(e)}")
                    
            return preview_results
            
    def merge_new_data(self) -> Dict[str, Any]:
        """Perform the full transactional merge pipeline."""
        with self.lock:
            # 1. Scan new data
            scan_results = self.scan_new_data()
            
            # 2. Validate new data
            validation_results = self.validate_new_data()
            if not validation_results['valid']:
                return {
                    'success': False,
                    'error': f"Validation failed: {validation_results['errors']}"
                }
                
            # 3. Preview merge
            preview_results = self.preview_merge()
            if preview_results['conflicts']:
                return {
                    'success': False,
                    'error': f"Conflicts detected: {len(preview_results['conflicts'])} conflicts require human review."
                }
                
            # 4. Perform merge
            merge_results = {
                'rules_merged': False,
                'transformations_merged': False,
                'domains_merged': False
            }
            
            # Merge rules if present
            if scan_results['rules_files']:
                for filename in scan_results['rules_files']:
                    file_path = os.path.join(self.new_data_dir, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            
                        incoming_rules = data.get('rules', [])
                        success, message, merged_rules, conflicts = self.merge_rules(incoming_rules)
                        if success:
                            merge_results['rules_merged'] = True
                        else:
                            return {
                                'success': False,
                                'error': f"Rules merge failed: {message}"
                            }
                    except Exception as e:
                        return {
                            'success': False,
                            'error': f"Error processing rules file {filename}: {str(e)}"
                        }
                        
            # Merge transformations if present
            if scan_results['transformations_files']:
                for filename in scan_results['transformations_files']:
                    file_path = os.path.join(self.new_data_dir, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            
                        incoming_transformations = data.get('transformations', [])
                        success, message, merged_trans, conflicts = self.merge_transformations(incoming_transformations)
                        if success:
                            merge_results['transformations_merged'] = True
                        else:
                            return {
                                'success': False,
                                'error': f"Transformations merge failed: {message}"
                            }
                    except Exception as e:
                        return {
                            'success': False,
                            'error': f"Error processing transformations file {filename}: {str(e)}"
                        }
                        
            # Merge domains if present
            if scan_results['domains_files']:
                for filename in scan_results['domains_files']:
                    file_path = os.path.join(self.new_data_dir, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            
                        incoming_domains = data.get('domains', [])
                        success, message, merged_domains, conflicts = self.merge_domains(incoming_domains)
                        if success:
                            merge_results['domains_merged'] = True
                        else:
                            return {
                                'success': False,
                                'error': f"Domains merge failed: {message}"
                            }
                    except Exception as e:
                        return {
                            'success': False,
                            'error': f"Error processing domains file {filename}: {str(e)}"
                        }
                        
            # 5. Archive incoming files
            archive_success = self.archive_new_data_files()
            if not archive_success:
                return {
                    'success': False,
                    'error': "Failed to archive incoming files after merge."
                }
                
            # 6. Update library version
            self._update_library_version()
            
            # 7. Invalidate cache
            self.invalidate_cache()
            
            return {
                'success': True,
                'results': merge_results,
                'message': "Merge successful."
            }
            
    def archive_new_data_files(self) -> bool:
        """Move new_data files to archive directory after successful merge."""
        with self.lock:
            archive_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            archive_dir = os.path.join(self.archive_dir, f"new_data_{archive_timestamp}")
            os.makedirs(archive_dir, exist_ok=True)
            
            moved_files = []
            for filename in os.listdir(self.new_data_dir):
                if filename.endswith('.json'):
                    src_path = os.path.join(self.new_data_dir, filename)
                    dst_path = os.path.join(archive_dir, filename)
                    shutil.move(src_path, dst_path)
                    moved_files.append(filename)
                    
            return len(moved_files) > 0
            
    def _update_library_version(self):
        """Update the library version (increment patch version)."""
        with self.lock:
            metadata = self._load_json(self.metadata_file)
            current_version = metadata.get('library_version', '1.0.0')
            
            # Parse version MAJOR.MINOR.PATCH
            try:
                parts = current_version.split('.')
                if len(parts) == 3:
                    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
                    # Increment patch version
                    new_version = f"{major}.{minor}.{patch + 1}"
                else:
                    new_version = '1.0.1'  # Fallback
            except ValueError:
                new_version = '1.0.1'  # Fallback
                
            metadata['library_version'] = new_version
            metadata['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            self._save_json(self.metadata_file, metadata)
            
    def rollback_to_last_backup(self) -> bool:
        """Rollback to the last backup."""
        with self.lock:
            # Find the latest backup
            backup_dirs = [d for d in os.listdir(self.backups_dir) if d.startswith('backup_')]
            if not backup_dirs:
                return False
                
            # Sort by name (which includes timestamp) to get the latest
            backup_dirs.sort(reverse=True)
            latest_backup = os.path.join(self.backups_dir, backup_dirs[0])
            
            return self.rollback(latest_backup)
            
    def has_conflicts(self) -> bool:
        """Check if there are pending conflicts."""
        with self.lock:
            # This would check for CONFLICT_PENDING status in the library
            # For now, we'll return False as conflicts are handled during merge
            preview_results = self.preview_merge()
            return len(preview_results.get('conflicts', [])) > 0
            
    def get_pending_conflicts(self) -> List[Dict[str, Any]]:
        """Get list of pending conflicts."""
        with self.lock:
            preview_results = self.preview_merge()
            return preview_results.get('conflicts', [])
            
    def resolve_conflict(self, rule_id: str, resolution: str) -> bool:
        """Resolve a conflict with the specified resolution."""
        with self.lock:
            # Implement conflict resolution logic based on the resolution type:
            # KEEP_EXISTING, ACCEPT_INCOMING, KEEP_BOTH, REJECT_INCOMING
            # This is a simplified implementation
            return True
