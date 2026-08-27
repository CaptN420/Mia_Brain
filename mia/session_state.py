from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from equation_candidate import EquationCandidate

class SessionState:
    """
    État d'exécution canonique pour une session MIA.
    """
    
    def __init__(self, session_id: str, session_path: Path):
        self.session_id = session_id
        self.session_path = session_path
        
        # État courant
        self.current_generation: int = 0
        self.approved_variables: Dict[str, Any] = {}
        self.approved_equations: List[EquationCandidate] = []
        self.parent_equation_id: Optional[str] = None
        self.pending_repairs: List[str] = []
        self.evolution_status: str = "ACTIVE"

        # Charger l'état existant si présent
        self._load_state()

    def _load_state(self):
        state_file = self.session_path / "session_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                self.current_generation = data.get("current_generation", 0)
                self.approved_variables = data.get("approved_variables", {})
                
                # Reconstituer les équations approuvées
                eq_data_list = data.get("approved_equations", [])
                self.approved_equations = [EquationCandidate.from_dict(eq_data) for eq_data in eq_data_list]
                
                self.parent_equation_id = data.get("parent_equation_id")
                self.pending_repairs = data.get("pending_repairs", [])
                self.evolution_status = data.get("evolution_status", "ACTIVE")
            except Exception as e:
                print(f"[SESSION_STATE] Error loading state: {e}. Starting fresh.")

    def save_state(self):
        state_file = self.session_path / "session_state.json"
        
        # Convertir les équations en dictionnaires
        eq_dicts = [eq.to_dict() for eq in self.approved_equations]
        
        data = {
            "session_id": self.session_id,
            "current_generation": self.current_generation,
            "approved_variables": self.approved_variables,
            "approved_equations": eq_dicts,
            "parent_equation_id": self.parent_equation_id,
            "pending_repairs": self.pending_repairs,
            "evolution_status": self.evolution_status
        }
        
        state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_approved_equation(self, candidate: EquationCandidate):
        # Vérifier si l'équation existe déjà
        existing = next((eq for eq in self.approved_equations if eq.id == candidate.id), None)
        if not existing:
            self.approved_equations.append(candidate)
        
        # Mettre à jour le parent si c'est une équation approuvée et qu'aucun parent n'est défini ou si celle-ci est plus "stable"
        if candidate.is_approved() and not self.parent_equation_id:
            self.consolidate_parent(candidate)

    def consolidate_parent(self, candidate: EquationCandidate):
        """
        Règle de consolidation explicite : une équation ne devient parent que si elle satisfait les critères minimaux.
        """
        if (
            candidate.validation_status == "APPROVED"
            and candidate.equation
            and candidate.calculated_object
            and len(candidate.causal_links) >= 2
        ):
            self.parent_equation_id = candidate.id
            print(f"[CONSOLIDATE_PARENT] Parent established: {candidate.id} -> {candidate.equation}")
        else:
            print(f"[CONSOLIDATE_PARENT] Candidate {candidate.id} does not meet parent criteria.")

    def select_parent_equation(self) -> Optional[EquationCandidate]:
        if not self.parent_equation_id:
            return None
        
        # Chercher dans les équations approuvées
        for eq in self.approved_equations:
            if eq.id == self.parent_equation_id:
                return eq
        
        return None

    def increment_generation(self):
        self.current_generation += 1
