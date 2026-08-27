import json
import os
from pathlib import Path
from datetime import datetime
import re

def normalize_symbol(symbol):
    return str(symbol or "").strip().lower()

def normalize_equation(eq):
    return "".join(str(eq or "").strip().lower().split())

def union_list(list_a, list_b, limit=100):
    combined = list(list_a or []) + list(list_b or [])
    unique = []
    seen = set()
    for item in combined:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return unique[-limit:] if limit else unique

def merge_variable_payload(a, b):
    out = dict(a or {})
    fields = ["definition", "unit", "measure", "role", "validation_summary", "family", "micro_equation", "experiment", "source_agent", "status", "kind", "description", "confidence", "justification"]
    for field in fields:
        if not out.get(field) and b.get(field):
            out[field] = b[field]
    for field in ["links", "remarks", "required_next", "usages"]:
        out[field] = union_list(list(out.get(field, []) or []), list(b.get(field, []) or []))
    for field in ["approved"]:
        out[field] = bool(out.get(field, False) or b.get(field, False))
    for field in ["source_turn", "first_seen", "turn"]:
        out[field] = max(int(out.get(field, 0) or 0), int(b.get(field, 0) or 0))
    if not out.get("name") and b.get("name"):
        out["name"] = b["name"]
    if not out.get("variable_name") and b.get("variable_name"):
        out["variable_name"] = b["variable_name"]
    return out

def merge_equation_entry(winner, loser):
    out = dict(winner or {})
    fields = ["mechanism", "experiment", "remark", "validation_summary", "memory_decision", "object_calculated", "law_type", "architecture", "parent_equation", "parent_variable", "source_agent", "status"]
    for field in fields:
        if not out.get(field) and loser.get(field):
            out[field] = loser[field]
    out["approved"] = bool(out.get("approved", False) or loser.get("approved", False))
    out["stable_parent"] = bool(out.get("stable_parent", False) or loser.get("stable_parent", False))
    out["exploratory_parent"] = bool(out.get("exploratory_parent", False) or loser.get("exploratory_parent", False))
    out["links"] = union_list(list(out.get("links", []) or []), list(loser.get("links", []) or []), limit=24)
    out["required_next"] = union_list(list(out.get("required_next", []) or []), list(loser.get("required_next", []) or []), limit=24)
    defs = dict(loser.get("definitions", {}) or {})
    defs.update(dict(out.get("definitions", {}) or {}))
    out["definitions"] = defs
    repair_log = dict(loser.get("repair_log", {}) or {})
    repair_log.update(dict(out.get("repair_log", {}) or {}))
    out["repair_log"] = repair_log
    out["source_turn"] = max(int(out.get("source_turn", 0) or 0), int(loser.get("source_turn", 0) or 0))
    if not out.get("remark") and loser.get("remark"):
        out["remark"] = loser["remark"]
    return out

def run_master_merge():
    base_dir = Path(__file__).resolve().parent / "session"
    sessions = [d for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("alchemy_session_")]
    
    # Exclude the target if it already exists
    target_name = "alchemy_session_MASTER_MERGE"
    target_dir = base_dir / target_name
    if target_dir.exists():
        import shutil
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"Merging {len(sessions)} sessions into {target_name}...")

    # Shared Research Memory
    shared_data = {"approved_equations": [], "partial_equations": [], "approved_variables": {}, "candidate_variables": {}}
    mutation_history = []
    
    # Initialize scores
    eq_scores = {}
    var_scores = {}

    for s_path in sessions:
        print(f"Processing {s_path.name}...")
        shared_file = s_path / "shared_research_memory.json"
        if not shared_file.exists(): continue
        
        with open(shared_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Merge Equations
        for row in (data.get("approved_equations", []) + data.get("partial_equations", [])):
            eq = row.get("equation", "")
            eq_key = normalize_equation(eq)
            if not eq_key: continue
            
            current_winner = shared_data.get("approved_equations", []) + shared_data.get("partial_equations", [])
            # find if we already have this key
            existing = next((item for item in current_winner if normalize_equation(item.get("equation", "")) == eq_key), None)
            
            if existing is None:
                # Add new
                if row.get("approved"):
                    shared_data["approved_equations"].append(row)
                else:
                    shared_data["partial_equations"].append(row)
            else:
                # Merge existing
                new_score = 0
                if row.get("approved"): new_score += 40
                if row.get("stable_parent"): new_score += 20
                if row.get("repair_required"): new_score -= 20
                
                old_score = eq_scores.get(eq_key, 0)
                if new_score > old_score:
                    # Replace
                    idx = current_winner.index(existing)
                    current_winner[idx] = merge_equation_entry(row, existing)
                    # Update lists
                    new_approved = []
                    new_partial = []
                    for item in current_winner:
                        if item.get("approved"): new_approved.append(item)
                        else: new_partial.append(item)
                    shared_data["approved_equations"] = new_approved
                    shared_data["partial_equations"] = new_partial
                eq_scores[eq_key] = new_score

        # Merge Variables
        for store_name in ["approved_variables", "candidate_variables"]:
            for name, payload in data.get(store_name, {}).items():
                key = normalize_symbol(name)
                clean = dict(payload or {})
                clean.setdefault("name", name)
                
                if key in shared_data[store_name]:
                    shared_data[store_name][key] = merge_variable_payload(shared_data[store_name][key], clean)
                else:
                    shared_data[store_name][key] = clean

        # Mutation History
        mutation_history.extend(data.get("mutation_history", []))
        mutation_history = mutation_history[-120:]

    # Save Shared Research Memory
    shared_data.update({
        "mutation_history": mutation_history,
        "last_merge_summary": {
            "timestamp": datetime.now().isoformat(),
            "variables_final": len(shared_data["approved_variables"]) + len(shared_data["candidate_variables"]),
            "equations_final": len(shared_data["approved_equations"]) + len(shared_data["partial_equations"]),
            "source_sessions": [s.name for s in sessions]
        }
    })
    
    with open(target_dir / "shared_research_memory.json", 'w', encoding='utf-8') as f:
        json.dump(shared_data, f, ensure_ascii=False, indent=2)

    # Merge Secondary Memories
    for mem_type in ["variables_memory", "validations_memory", "roles_memory", "symbolic_memory"]:
        merged_content = []
        # Logic for symbolic/validations (lists)
        if mem_type in ["validations_memory", "symbolic_memory"]:
            all_items = []
            for s_path in sessions:
                p = s_path / f"{mem_type}.json"
                if p.exists():
                    with open(p, 'r', encoding='utf-8') as f:
                        all_items.extend(json.load(f))
            # Simple de-duplication for these lists
            unique_items = []
            seen = set()
            for item in all_items:
                # Use a simple string representation as a key
                key = json.dumps(item, sort_keys=True)
                if key not in seen:
                    unique_items.append(item)
                    seen.add(key)
            with open(target_dir / f"{mem_type}.json", 'w', encoding='utf-8') as f:
                json.dump(unique_items, f, ensure_ascii=False, indent=2)
        
        # Logic for variables/roles (dicts)
        else:
            merged_dict = {}
            for s_path in sessions:
                p = s_path / f"{mem_type}.json"
                if p.exists():
                    with open(p, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        for k, v in data.items():
                            if k not in merged_dict:
                                merged_dict[k] = v
                            else:
                                merged_dict[k] = merge_variable_payload(merged_dict[k], v)
            with open(target_dir / f"{mem_type}.json", 'w', encoding='utf-8') as f:
                json.dump(merged_dict, f, ensure_ascii=False, indent=2)

    print(f"Successfully merged into {target_dir}")

if __name__ == "__main__":
    run_master_merge()
