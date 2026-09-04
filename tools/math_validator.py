"""
MathValidator Component

This component classifies transformations as:
- VALID_STANDARD_MATH
- VALID_UNDER_SPECIFIC_FORMALISM
- EXPERIMENTAL_RULE
- UNDEFINED
- CONTRADICTORY
- REQUIRES_NUMERICAL_TEST
"""

from typing import Dict, Any, List
import logging

logger = logging.getLogger("MathValidator")

class MathValidator:
    """Validates mathematical transformations and classifies them appropriately."""
    
    def __init__(self):
        self.validation_categories = [
            "VALID_STANDARD_MATH",
            "VALID_UNDER_SPECIFIC_FORMALISM",
            "EXPERIMENTAL_RULE",
            "UNDEFINED",
            "CONTRADICTORY",
            "REQUIRES_NUMERICAL_TEST"
        ]
        self._seen_expressions: set[str] = set()
        
    def validate_transformation(self, transformation: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate a transformation and classify it.
        
        Args:
            transformation: Dictionary containing transformation details
            
        Returns:
            Dictionary with validation results and classification
        """
        rule_used = transformation.get("rule_used", "STANDARD_MATHEMATICS")
        expression = transformation.get("expression", "")
        
        # Classify the transformation
        classification = self._classify_transformation(rule_used, expression)
        
        # Generate validation result
        validation_result = {
            "expression": expression,
            "rule_used": rule_used,
            "classification": classification,
            "is_valid": classification in ["VALID_STANDARD_MATH", "VALID_UNDER_SPECIFIC_FORMALISM", "EXPERIMENTAL_RULE"],
            "validation_timestamp": self._get_timestamp()
        }
        
        # Log only once per unique expression to avoid spam in loops
        if expression not in self._seen_expressions:
            self._seen_expressions.add(expression)
            logger.info(f"MathValidator: Validated transformation '{expression}' as {classification}")
        return validation_result
        
    def _classify_transformation(self, rule_used: str, expression: str) -> str:
        """Classify the transformation based on the rule used."""
        if rule_used == "STANDARD_MATHEMATICS":
            return "VALID_STANDARD_MATH"
        elif rule_used == "EXPERIMENTAL_INF_DIVISION":
            return "EXPERIMENTAL_RULE"
        elif rule_used.startswith("SPECIFIC_FORMALISM_"):
            return "VALID_UNDER_SPECIFIC_FORMALISM"
        elif "undefined" in expression.lower() or "invalid" in expression.lower():
            return "UNDEFINED"
        elif "contradiction" in expression.lower() or "inconsistent" in expression.lower():
            return "CONTRADICTORY"
        else:
            return "REQUIRES_NUMERICAL_TEST"
            
    def _get_timestamp(self) -> str:
        """Get current timestamp as string."""
        import time
        return str(time.time())
        
    def validate_mirror_operation(self, x: float, center: float, mirrored_x: float) -> Dict[str, Any]:
        """
        Validate a mirror operation: mirror(x, center) = 2*center - x
        
        Args:
            x: Original value
            center: Mirror center
            mirrored_x: Expected mirrored value
            
        Returns:
            Validation result dictionary
        """
        expected_mirrored_x = 2 * center - x
        is_valid = abs(mirrored_x - expected_mirrored_x) < 1e-9  # Floating point comparison
        
        classification = "VALID_STANDARD_MATH" if is_valid else "CONTRADICTORY"
        
        return {
            "operation": "mirror",
            "input_x": x,
            "center": center,
            "expected_mirrored_x": expected_mirrored_x,
            "actual_mirrored_x": mirrored_x,
            "is_valid": is_valid,
            "classification": classification,
            "validation_timestamp": self._get_timestamp()
        }
