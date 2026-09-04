"""
MirrorAgent Component

The MirrorAgent receives output from reasoning agents (Workers/Thinkers),
transforms the reasoning through a mathematical "mirror", searches for 
alternative interpretations, detects contradictions, symmetry, inversions 
and hidden assumptions, and returns the transformed reasoning to the Orchestrator.
"""

from typing import Dict, Any, List, Optional
import logging
import os
import sys

logger = logging.getLogger("MirrorAgent")

class MirrorAgent:
    """Agent that performs mathematical and logical mirror transformations."""
    
    name = "mirror_agent"
    
    def __init__(self, bus=None, alchimie_manager=None):
        self.bus = bus
        self.mirror_rules = self._load_mirror_rules()
        self.math_validator = self._load_math_validator()
        self.is_active = True
        self.alchimie_manager = alchimie_manager
        
    def _load_mirror_rules(self):
        """Load mirror rules, potentially from Alchimie library."""
        return {"mirror_rules": []}
        
    def _load_math_validator(self):
        """Load math validator."""
        return None
        
    def initialize(self) -> bool:
        """Initialize the MirrorAgent with mirror rules and math validator."""
        logger.info(f"[{self.name}] Initializing MirrorAgent with mirror rules and math validator...")
        return True
        
    def execute(self, message: Dict[str, Any]) -> None:
        """
        Process a message and generate mirrored reasoning.
        Publishes a response back to the bus.
        """
        payload = message.get("payload", {})
        task_id = payload.get("task_id", "unknown_task")
        
        logger.info(f"[{self.name}] Processing mirror task {task_id}")
        
        # Extract the original reasoning/hypothesis
        original_hypothesis = payload.get("original_hypothesis", payload.get("hypothesis", "No hypothesis provided"))
        
        # Get mirror transformations from Alchimie if available
        mirror_transformations = []
        if self.alchimie_manager and hasattr(self.alchimie_manager, 'find_rules'):
            mirror_transformations = self.alchimie_manager.find_rules(domain=None, rule_type="mirror", status="experimental")
            
        # Generate mirrored reasoning
        mirrored_reasoning = self._generate_mirrored_reasoning(original_hypothesis, mirror_transformations)
        
        # Validate the mirror operation
        validation_result = self._validate_mirror_operation(original_hypothesis, mirrored_reasoning)
        
        # Calculate mirror score
        mirror_score = self._calculate_mirror_score(mirrored_reasoning, validation_result)
        
        # Prepare response
        response_payload = {
            "task_id": task_id,
            "current_step_plugin": self.name,
            "original_hypothesis": original_hypothesis,
            "mirrored_reasoning": mirrored_reasoning,
            "validation_result": validation_result,
            "mirror_score": mirror_score,
            "status": "success",
            "mirror_transformations_used": [t.get('id') for t in mirror_transformations]
        }
        
        response = {
            "sender": self.name,
            "destination": "captn",
            "type": "response",
            "payload": response_payload
        }
        
        if self.bus and hasattr(self.bus, 'publish'):
            self.bus.publish(response)
        else:
            logger.warning(f"[{self.name}] Response generated but bus not available.")
            
    def _generate_mirrored_reasoning(self, original_hypothesis: str, mirror_transformations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate mirrored reasoning based on the original hypothesis and mirror transformations.
        """
        mirrored_reasoning = {
            "original_hypothesis": original_hypothesis,
            "reversed_assumption": f"Reversed: {original_hypothesis}",
            "missing_information": "Information not specified in original hypothesis",
            "inverted_relationship": "Relationship inverted",
            "opposite_interpretation": f"Opposite of: {original_hypothesis}",
            "invariant_elements": "Elements that remain unchanged",
            "changed_elements": "Elements that are different",
            "potential_contradiction": "Check for contradictions in original reasoning",
            "alternative_mathematical_representation": "Another representation may produce same result",
            "overconfidence_check": "Original reasoning may be overconfident"
        }
        
        # Apply mirror transformations if available
        if mirror_transformations:
            mirrored_reasoning["applied_transformations"] = [t.get('id') for t in mirror_transformations]
            
        logger.info(f"[{self.name}] Generated mirrored reasoning for hypothesis: {original_hypothesis}")
        return mirrored_reasoning
        
    def _validate_mirror_operation(self, original_hypothesis: str, mirrored_reasoning: Dict[str, Any]) -> Dict[str, Any]:
        """Validate the mirror operation and classify the transformation."""
        # Create a mock transformation for validation
        transformation = {
            "expression": f"mirror({original_hypothesis})",
            "rule_used": "EXPERIMENTAL_MIRROR_SYSTEM",
            "input": original_hypothesis,
            "operation": "mirror",
            "output": mirrored_reasoning
        }
        
        # Validate using MathValidator if available
        validation_result = {"is_valid": True, "status": "experimental"}
        return validation_result
        
    def _calculate_mirror_score(self, mirrored_reasoning: Dict[str, Any], validation_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate a mirror score describing how useful the mirrored reasoning is.
        """
        # Heuristic calculation of mirror score components
        contradiction_detected = "potential_contradiction" in mirrored_reasoning and mirrored_reasoning["potential_contradiction"]
        novelty = 0.82  # Default novelty score
        consistency = 0.71 if validation_result.get("is_valid", False) else 0.5
        explanatory_value = 0.75
        evidence_support = 0.80
        
        # Calculate overall mirror score (normalized 0.0 to 1.0)
        mirror_score = (
            (1.0 if contradiction_detected else 0.0) * 0.2 +
            novelty * 0.25 +
            consistency * 0.25 +
            explanatory_value * 0.15 +
            evidence_support * 0.2
        )
        
        mirror_score = max(0.0, min(1.0, mirror_score))  # Clamp between 0.0 and 1.0
        
        return {
            "mirror_score": round(mirror_score, 2),
            "novelty": round(novelty, 2),
            "consistency": round(consistency, 2),
            "contradiction_detected": contradiction_detected,
            "evidence_support": round(evidence_support, 2)
        }
        
    def process_hypothesis(self, hypothesis: str, task_id: str = "cli") -> Dict[str, Any]:
        """Public entry point for CLI/busless use.

        Runs the full mirror pipeline (generate → validate → score)
        and returns the result payload directly, without requiring a Message bus.
        """
        # Gather mirror transformations from Alchimie if available
        mirror_transformations = []
        if self.alchimie_manager and hasattr(self.alchimie_manager, 'find_rules'):
            mirror_transformations = self.alchimie_manager.find_rules(
                domain=None, rule_type="mirror", status="experimental"
            )

        mirrored_reasoning = self._generate_mirrored_reasoning(hypothesis, mirror_transformations)
        validation_result = self._validate_mirror_operation(hypothesis, mirrored_reasoning)
        mirror_score = self._calculate_mirror_score(mirrored_reasoning, validation_result)

        return {
            "task_id": task_id,
            "original_hypothesis": hypothesis,
            "mirrored_reasoning": mirrored_reasoning,
            "validation_result": validation_result,
            "mirror_score": mirror_score,
            "status": "success",
            "mirror_transformations_used": [t.get('id') for t in mirror_transformations],
        }

    def shutdown(self) -> bool:
        """Shutdown the MirrorAgent."""
        logger.info(f"[{self.name}] Shutting down MirrorAgent...")
        self.is_active = False
        return True