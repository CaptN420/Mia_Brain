from __future__ import annotations
import json
from typing import Any, Dict, List, Optional

class EquationCandidate:
    """
    Représentation interne unique et canonique pour une équation candidate.
    """
    
    VALID_STATUSES = [
        "DISCOVERED",
        "PENDING",
        "VALIDATED",
        "CONSOLIDATED",
        "APPROVED",
        "REJECTED",
        "REPAIR_PENDING",
        "MUTATED",
        "ARCHIVED"
    ]

    def __init__(
        self,
        id: str,
        equation: str,
        calculated_object: str = "",
        law_type: str = "",
        architecture: str = "",
        variables: Optional[List[str]] = None,
        causal_links: Optional[List[Dict[str, Any]]] = None,
        validation_status: str = "DISCOVERED",
        parent_id: Optional[str] = None,
        generation: int = 0
    ):
        if not id:
            raise ValueError("EquationCandidate requires an 'id'")
        if not equation:
            raise ValueError("EquationCandidate requires an 'equation'")
        if validation_status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid validation_status: {validation_status}. Must be one of {self.VALID_STATUSES}")

        self.id = id
        self.equation = equation
        self.calculated_object = calculated_object
        self.law_type = law_type
        self.architecture = architecture
        self.variables = variables or []
        self.causal_links = causal_links or []
        self.validation_status = validation_status
        self.parent_id = parent_id
        self.generation = generation

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EquationCandidate':
        """
        Crée une EquationCandidate à partir d'un dictionnaire.
        """
        return cls(
            id=data.get("id", f"eq_{hash(json.dumps(data, sort_keys=True)) % 10000:04d}"),
            equation=data.get("equation", ""),
            calculated_object=data.get("calculated_object", ""),
            law_type=data.get("law_type", ""),
            architecture=data.get("architecture", ""),
            variables=data.get("variables", []),
            causal_links=data.get("causal_links", []),
            validation_status=data.get("validation_status", "DISCOVERED"),
            parent_id=data.get("parent_id"),
            generation=data.get("generation", 0)
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convertit la candidate en dictionnaire pour la persistance JSON.
        """
        return {
            "id": self.id,
            "equation": self.equation,
            "calculated_object": self.calculated_object,
            "law_type": self.law_type,
            "architecture": self.architecture,
            "variables": self.variables,
            "causal_links": self.causal_links,
            "validation_status": self.validation_status,
            "parent_id": self.parent_id,
            "generation": self.generation
        }

    def is_approved(self) -> bool:
        return self.validation_status == "APPROVED"

    def is_mutated(self) -> bool:
        return self.validation_status == "MUTATED"

    def is_repair_pending(self) -> bool:
        return self.validation_status == "REPAIR_PENDING"
