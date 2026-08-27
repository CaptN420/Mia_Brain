"""
Unit tests for Mirror transformation functions and experimental rules.

Tests include:
- mirror(x, center) functionality
- mirror(mirror(x)) == x where mathematically applicable
- Distinction between standard mathematical behavior and experimental behavior
- Testing that disabling experimental rules restores standard behavior
"""

import unittest
import sys
import os

# Add the captn directory to the path to import modules
project_root = os.path.dirname(os.path.abspath(__file__))
captn_path = os.path.join(project_root, '..', 'captn')
sys.path.insert(0, captn_path)

from runtime.mirror_rules import MirrorRules
from runtime.math_validator import MathValidator


class TestMirrorTransformation(unittest.TestCase):
    """Test cases for mirror transformation functions."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mirror_rules = MirrorRules()
        self.math_validator = MathValidator()
        
    def test_mirror_basic_functionality(self):
        """Test basic mirror functionality: mirror(x, c) = 2c - x."""
        # Test with center = 5.5
        self.mirror_rules.set_mirror_center(5.5)
        
        # mirror(1, 5.5) = 2*5.5 - 1 = 11 - 1 = 10
        self.assertEqual(self.mirror_rules.mirror(1, 5.5), 10.0)
        
        # mirror(2, 5.5) = 2*5.5 - 2 = 11 - 2 = 9
        self.assertEqual(self.mirror_rules.mirror(2, 5.5), 9.0)
        
        # mirror(6, 5.5) = 2*5.5 - 6 = 11 - 6 = 5
        self.assertEqual(self.mirror_rules.mirror(6, 5.5), 5.0)
        
        # mirror(8, 5.5) = 2*5.5 - 8 = 11 - 8 = 3
        self.assertEqual(self.mirror_rules.mirror(8, 5.5), 3.0)
        
        # mirror(10, 5.5) = 2*5.5 - 10 = 11 - 10 = 1
        self.assertEqual(self.mirror_rules.mirror(10, 5.5), 1.0)
        
    def test_mirror_pairwise_functionality(self):
        """Test mirror using the default center."""
        self.mirror_rules.set_mirror_center(5.5)
        
        # Test mirror_pairwise
        self.assertEqual(self.mirror_rules.mirror_pairwise(1), 10.0)
        self.assertEqual(self.mirror_rules.mirror_pairwise(2), 9.0)
        self.assertEqual(self.mirror_rules.mirror_pairwise(6), 5.0)
        self.assertEqual(self.mirror_rules.mirror_pairwise(8), 3.0)
        self.assertEqual(self.mirror_rules.mirror_pairwise(10), 1.0)
        
    def test_mirror_mirror_identity(self):
        """Test that mirror(mirror(x)) == x where mathematically applicable."""
        self.mirror_rules.set_mirror_center(5.5)
        
        # For any x, mirror(mirror(x)) should equal x
        test_values = [1, 2, 6, 8, 10, 3.5, 7.2, -5.0, 100.0]
        
        for x in test_values:
            mirrored_once = self.mirror_rules.mirror(x, 5.5)
            mirrored_twice = self.mirror_rules.mirror(mirrored_once, 5.5)
            
            # mirror(mirror(x)) should equal x
            self.assertAlmostEqual(mirrored_twice, x, places=7, 
                                  msg=f"mirror(mirror({x})) should equal {x}")
                              
    def test_infinity_division_standard_behavior(self):
        """Test standard mathematical behavior for infinity division."""
        # By default, infinity division rule should be disabled
        self.assertFalse(self.mirror_rules.is_infinity_division_enabled())
        
        # Process infinity division with rule disabled
        result = self.mirror_rules.process_infinity_division("INF", 12.0)
        
        self.assertEqual(result["expression"], "INF / 12.0")
        self.assertEqual(result["standard_interpretation"], "infinity")
        self.assertIsNone(result["experimental_interpretation"])
        self.assertEqual(result["rule_used"], "STANDARD_MATHEMATICS")
        self.assertEqual(result["confidence"], 0.0)
        
    def test_infinity_division_experimental_behavior(self):
        """Test experimental behavior for infinity division when enabled."""
        # Enable the experimental infinity division rule
        self.mirror_rules.enable_infinity_division_rule(True)
        self.assertTrue(self.mirror_rules.is_infinity_division_enabled())
        
        # Process infinity division with rule enabled
        result = self.mirror_rules.process_infinity_division("INF", 12.0)
        
        self.assertEqual(result["expression"], "INF / 12.0")
        self.assertEqual(result["standard_interpretation"], "infinity")
        self.assertEqual(result["experimental_interpretation"], "INF")
        self.assertEqual(result["rule_used"], "EXPERIMENTAL_INF_DIVISION")
        self.assertEqual(result["confidence"], 0.5)
        
    def test_infinity_division_disabled_restores_standard(self):
        """Test that disabling experimental rules restores standard behavior."""
        # Enable the rule first
        self.mirror_rules.enable_infinity_division_rule(True)
        
        # Now disable it
        self.mirror_rules.enable_infinity_division_rule(False)
        self.assertFalse(self.mirror_rules.is_infinity_division_enabled())
        
        # Process infinity division with rule disabled
        result = self.mirror_rules.process_infinity_division("INF", 12.0)
        
        self.assertEqual(result["expression"], "INF / 12.0")
        self.assertEqual(result["standard_interpretation"], "infinity")
        self.assertIsNone(result["experimental_interpretation"])
        self.assertEqual(result["rule_used"], "STANDARD_MATHEMATICS")
        self.assertEqual(result["confidence"], 0.0)
        
    def test_math_validator_classification(self):
        """Test MathValidator classification of transformations."""
        # Test standard mathematics classification
        std_transformation = {
            "expression": "2 + 2 = 4",
            "rule_used": "STANDARD_MATHEMATICS"
        }
        std_result = self.math_validator.validate_transformation(std_transformation)
        self.assertEqual(std_result["classification"], "VALID_STANDARD_MATH")
        self.assertTrue(std_result["is_valid"])
        
        # Test experimental rule classification
        exp_transformation = {
            "expression": "INF / 12",
            "rule_used": "EXPERIMENTAL_INF_DIVISION"
        }
        exp_result = self.math_validator.validate_transformation(exp_transformation)
        self.assertEqual(exp_result["classification"], "EXPERIMENTAL_RULE")
        self.assertTrue(exp_result["is_valid"])
        
    def test_math_validator_mirror_operation_validation(self):
        """Test MathValidator validation of mirror operations."""
        # Test valid mirror operation
        valid_validation = self.math_validator.validate_mirror_operation(
            x=1, center=5.5, mirrored_x=10.0
        )
        self.assertTrue(valid_validation["is_valid"])
        self.assertEqual(valid_validation["classification"], "VALID_STANDARD_MATH")
        self.assertEqual(valid_validation["expected_mirrored_x"], 10.0)
        self.assertEqual(valid_validation["actual_mirrored_x"], 10.0)
        
        # Test invalid mirror operation (contradictory)
        invalid_validation = self.math_validator.validate_mirror_operation(
            x=1, center=5.5, mirrored_x=9.0  # Should be 10.0
        )
        self.assertFalse(invalid_validation["is_valid"])
        self.assertEqual(invalid_validation["classification"], "CONTRADICTORY")
        self.assertEqual(invalid_validation["expected_mirrored_x"], 10.0)
        self.assertEqual(invalid_validation["actual_mirrored_x"], 9.0)


if __name__ == '__main__':
    unittest.main()