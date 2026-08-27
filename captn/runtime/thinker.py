from typing import List, Dict, Any, Optional
import logging
import os
import sys

logger = logging.getLogger("Thinker")

class Thinker:
    """
    The Reasoner. Takes raw findings from workers and synthesizes them into a 
    coherent report, identifies contradictions, and assigns confidence scores.
    """
    def __init__(self, alchimie_manager=None):
        self.alchimie_manager = alchimie_manager

    def find_rules(self, domain: Optional[str] = None, rule_type: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Query Alchimie library for available transformations/rules.
        Used by the Orchestrator to decide whether a proposed rule is relevant before application.
        """
        if self.alchimie_manager and hasattr(self.alchimie_manager, 'find_rules'):
            return self.alchimie_manager.find_rules(domain=domain, rule_type=rule_type, status=status)
        return []

    def synthesize(self, worker_results: List[Dict[str, Any]], alchimie_version: Optional[str] = None) -> Dict[str, Any]:
        logger.info(f"Thinker: Synthesizing {len(worker_results)} worker results.")
        
        all_findings = []
        warnings = 0
        errors = 0
        
        for res in worker_results:
            result_payload = res.get("payload", {}).get("result", {})
            status = result_payload.get("status", "success")
            findings = result_payload.get("findings", [])
            all_findings.extend(findings)
            
            if status == "warning":
                warnings += 1
            elif status == "error":
                errors += 1

        # Basic reasoning logic
        confidence = 100
        if warnings > 0:
            confidence -= (warnings * 10)
        if errors > 0:
            confidence -= (errors * 20)
        
        confidence = max(0, min(100, confidence))

        # Synthesize summary
        summary = "No critical issues found."
        if all_findings:
            summary = f"Found {len(all_findings)} issues/warnings across the codebase."
            if errors > 0:
                summary = f"CRITICAL: {errors} errors detected. {summary}"

        # Include Alchimie version in the report if provided
        report_data = {
            "summary": summary,
            "total_findings": len(all_findings),
            "confidence_score": confidence,
            "detailed_findings": all_findings,
            "worker_breakdown": {
                res.get("payload", {}).get("result", {}).get("worker_name", res.get("plugin", "unknown")): res for res in worker_results
            }
        }
        
        if alchimie_version:
            report_data["alchimie_version"] = alchimie_version
            report_data["rule_versions"] = self._get_rule_versions_from_findings(all_findings)

        return report_data

    def _get_rule_versions_from_findings(self, findings: List[Dict[str, Any]]) -> List[str]:
        """Extract rule versions from findings if available."""
        rule_versions = []
        for finding in findings:
            if isinstance(finding, dict) and 'rule_version' in finding:
                rule_versions.append(finding['rule_version'])
        return rule_versions
