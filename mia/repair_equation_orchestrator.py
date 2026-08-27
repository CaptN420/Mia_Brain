from __future__ import annotations

import re
from typing import Any, Dict, List

from shared_memory import SharedEquation, SharedResearchMemory


class RepairEquationOrchestrator:
    """Deterministic repair stage dedicated to fixing the latest rejected/partial equation.

    V7.3 goal: when an equation repair is blocked by unknown/unvalidated variables,
    explicitly register those symbols into pending variable memory so AutoRepair can
    hand over to VariableRepairOrchestrator on the next cycle.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.shared = SharedResearchMemory(cfg.session_dir)

    def run(self, turns: int = 1) -> None:
        turns = max(1, int(turns))
        print('Repair equation started')
        for idx in range(1, turns + 1):
            print(f'--- repair tour {idx} ---')
            self._run_once()

    # ------------------------------------------------------------------
    # Core flow
    # ------------------------------------------------------------------
    def _run_once(self) -> None:
        target_equation = str(getattr(self.cfg, 'target_equation', '') or '').strip()
        latest = self.shared.get_equation_payload(target_equation) if target_equation else None
        latest = latest or self.shared.get_latest_equation_entry() or {}
        if not latest:
            print('[REPAIR] Aucune équation disponible à réparer.')
            return

        original_eq = str(latest.get('equation', '') or '').strip()
        approved_vars = self.shared.get_approved_variables() or {}
        candidate_vars = self.shared.get_candidate_variables() or {}
        approved_symbols = list(approved_vars.keys())
        latest_var = self.shared.get_last_validated_variable() or {}
        cycle_symbol = str(latest_var.get('symbol', '') or '').strip()

        main_issue = self._detect_main_issue(latest, approved_symbols)
        missing_next = self._extract_invalid_symbols(main_issue)
        repaired_eq = self._repair_equation(original_eq, approved_symbols, cycle_symbol)

        used_symbols = self._extract_symbols(repaired_eq)
        unknown_after_fix = [s for s in used_symbols if s != 'Ndot' and s not in approved_symbols]
        blocked_by_variables = bool(missing_next)
        if not missing_next and unknown_after_fix:
            blocked_by_variables = True
            missing_next = list(dict.fromkeys(unknown_after_fix))

        approved = bool(
            repaired_eq
            and '=' in repaired_eq
            and not unknown_after_fix
            and self._uses_approved_symbol(repaired_eq, approved_symbols)
        )

        definitions = self._build_definitions(used_symbols, approved_vars)
        mechanism = self._build_mechanism(repaired_eq)
        links = self._build_links(used_symbols)
        experiment = self._build_experiment(used_symbols)
        fix_applied = self._describe_fix(original_eq, repaired_eq, main_issue)
        status = 'approved' if approved else 'partial'
        decision = 'approved' if approved else 'needs_repair'
        turn = int(latest.get('source_turn', 0) or 0) + 1

        if blocked_by_variables and missing_next:
            self._register_missing_variables(
                symbols=missing_next,
                source_turn=turn,
                original_eq=original_eq,
                repaired_eq=repaired_eq,
                issue=main_issue,
                candidate_vars=candidate_vars,
                approved_vars=approved_vars,
            )

        payload = SharedEquation(
            equation=repaired_eq,
            definitions=definitions,
            mechanism=mechanism,
            experiment=experiment,
            links=links,
            remark='Repair Core V7.3: correction ciblée et handoff variable explicite quand une variable bloque.',
            approved=approved,
            source_turn=turn,
            source_agent='RepairCore',
            validation_summary=(
                'Repair Core V7.3 | '
                + ('équation corrigée et approuvée' if approved else 'équation corrigée partiellement')
                + f' | issue={main_issue}'
            ),
            required_next=[] if approved and not missing_next else (
                [f'valider variables: {", ".join(missing_next)}'] if missing_next else ['compléter la validation finale']
            ),
            status=status,
            fallback_used=False,
            stable_parent=approved,
            repair_required=(not approved) or blocked_by_variables,
            memory_decision=('needs_variable_repair' if blocked_by_variables else decision),
            object_calculated=str(latest.get('object_calculated', '') or 'débit net de transfert'),
            law_type=str(latest.get('law_type', '') or 'transport réparé'),
            architecture=str(latest.get('architecture', '') or 'repair ciblé minimal'),
            parent_equation=original_eq,
            parent_variable=str(latest.get('parent_variable', '') or cycle_symbol),
            exploratory_parent=False,
            repair_log={
                'turn': turn,
                'kind': 'equation',
                'status': status,
                'original_equation': original_eq,
                'fixed_equation': repaired_eq,
                'main_issue': main_issue,
                'fix_applied': fix_applied,
                'pattern': self._pattern_for_issue(main_issue),
                'blocked_by_variables': blocked_by_variables,
                'next_variables': list(missing_next),
            },
        )

        self.shared.add_equation(payload)
        score = self.shared.compute_equation_turn_score(payload)
        self.shared.record_turn_metric(
            kind='repair',
            turn=turn,
            element=repaired_eq,
            signature=self._structure_signature(repaired_eq),
            score_global=score,
            approved=approved,
            note=fix_applied,
        )
        self.shared.add_final_validation('equation', turn, payload.validation_summary, decision)
        self.shared.add_debate_summary('repair', turn, f'{original_eq} -> {repaired_eq} | approved={approved} | issue={main_issue}')

        print(f'[REPAIR] Issue détecté : {main_issue}')
        print(f'[REPAIR] Fix appliqué : {fix_applied}')
        print(f'[REPAIR] Equation : {repaired_eq}')
        if missing_next:
            print('[REPAIR] Variables to validate next : ' + ', '.join(missing_next))
            print(f'[ENGINE] next=variable | target={missing_next[0]} | reason=blocked_by_unvalidated_variables')
        print(f'[REPAIR] Verdict : {status}')

    def _register_missing_variables(
        self,
        symbols: List[str],
        source_turn: int,
        original_eq: str,
        repaired_eq: str,
        issue: str,
        candidate_vars: Dict[str, Dict[str, Any]],
        approved_vars: Dict[str, Dict[str, Any]],
    ) -> None:
        for raw_symbol in symbols:
            symbol = str(raw_symbol or '').strip()
            if not symbol or symbol == 'Ndot':
                continue

            base_payload = {}
            if symbol in candidate_vars:
                base_payload = dict(candidate_vars.get(symbol, {}) or {})
            elif symbol in approved_vars:
                base_payload = dict(approved_vars.get(symbol, {}) or {})

            pending_payload = {
                'symbol': symbol,
                'name': str(base_payload.get('name', '') or symbol),
                'family': str(base_payload.get('family', '') or ''),
                'definition': str(base_payload.get('definition', '') or ''),
                'unit': str(base_payload.get('unit', '') or ''),
                'measure': str(base_payload.get('measure', '') or ''),
                'role': str(base_payload.get('role', '') or ''),
                'source_turn': int(source_turn),
                'origin_equation': str(original_eq or ''),
                'repaired_equation': str(repaired_eq or ''),
                'last_issue': str(issue or ''),
                'priority_reason': 'blocked_by_unvalidated_variables',
            }
            self.shared.add_pending_variable(symbol, pending_payload)

    # ------------------------------------------------------------------
    # Heuristics
    # ------------------------------------------------------------------
    def _detect_main_issue(self, latest: Dict[str, Any], approved_symbols: List[str]) -> str:
        text = ' '.join([
            str(latest.get('validation_summary', '') or ''),
            str(latest.get('memory_decision', '') or ''),
            str(latest.get('remark', '') or ''),
            str(latest.get('status', '') or ''),
        ]).lower()
        eq = str(latest.get('equation', '') or '').strip()
        unknown = [s for s in self._extract_symbols(eq) if s != 'Ndot' and s not in approved_symbols]
        if not eq or eq == '-':
            return 'équation absente'
        if unknown:
            return 'variables non validées: ' + ', '.join(unknown)
        if 'partial' in text or 'à réparer' in text or 'repair' in text or 'needs_repair' in text:
            return 'équation partielle'
        if 'signature répétée' in text or 'repet' in text:
            return 'structure répétée'
        return 'cohérence physique à renforcer'

    def _repair_equation(self, original_eq: str, approved_symbols: List[str], cycle_symbol: str) -> str:
        eq = (original_eq or '').strip()
        unknown = [s for s in self._extract_symbols(eq) if s != 'Ndot' and s not in approved_symbols]
        if eq and '=' in eq and not unknown and self._uses_approved_symbol(eq, approved_symbols):
            return eq

        approved_set = set(approved_symbols)
        cycle = cycle_symbol if cycle_symbol in approved_set else ''

        if {'J', 'A'}.issubset(approved_set):
            return 'Ndot = J * A'
        if {'D', 'A', 'ΔC', 'L'}.issubset(approved_set):
            return 'Ndot = (D * A * ΔC) / L'
        if {'D', 'ΔC', 'L'}.issubset(approved_set):
            return 'J = (D * ΔC) / L'
        if cycle and 'A' in approved_set:
            return f'Ndot = {cycle} * A'
        if cycle and 'L' in approved_set:
            return f'Ndot = {cycle} / L'
        if cycle:
            return f'Ndot = {cycle}'
        if 'J' in approved_set:
            return 'Ndot = J'
        if approved_symbols:
            return f'Ndot = {approved_symbols[0]}'
        return eq or 'Ndot = J'

    def _extract_invalid_symbols(self, main_issue: str) -> List[str]:
        issue = str(main_issue or "").strip()
        prefix = "variables non validées:"
        if issue.lower().startswith(prefix):
            tail = issue.split(":", 1)[1].strip()
            return [x.strip() for x in tail.split(",") if x.strip()]
        return []

    def _build_definitions(self, used_symbols: List[str], approved_vars: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
        defs: Dict[str, str] = {}
        if 'Ndot' in used_symbols:
            defs['Ndot'] = 'débit net de transfert'
        for sym in used_symbols:
            if sym == 'Ndot':
                continue
            payload = dict(approved_vars.get(sym, {}) or {})
            definition = str(payload.get('definition', '') or '').strip()
            family = str(payload.get('family', '') or '').strip()
            unit = str(payload.get('unit', '') or '').strip()
            bits = [part for part in [definition, family, unit] if part]
            defs[sym] = ' | '.join(bits) if bits else f'variable validée {sym}'
        return defs

    def _build_mechanism(self, equation: str) -> str:
        eq = equation.replace(' ', '')
        if 'J*A' in eq:
            return 'Le débit total augmente avec le flux surfacique et la surface active.'
        if 'D*A*ΔC' in eq and '/L' in eq:
            return 'Le transfert augmente avec la diffusivité, la surface et le gradient, et diminue avec la longueur de diffusion.'
        if 'D*ΔC' in eq and '/L' in eq:
            return 'Le flux diffusif augmente avec la diffusivité et le gradient, puis décroît quand la distance de transport augmente.'
        return 'La réparation conserve un noyau causal explicite entre variables validées et débit calculé.'

    def _build_links(self, used_symbols: List[str]) -> List[str]:
        links: List[str] = []
        for sym in used_symbols:
            if sym == 'Ndot':
                continue
            if sym in {'A', 'J', 'D', 'ΔC'}:
                links.append(f'{sym} augmente Ndot')
            elif sym in {'L', 'R'}:
                links.append(f'{sym} diminue Ndot')
            else:
                links.append(f'{sym} contrôle Ndot')
        return list(dict.fromkeys(links))[:6]

    def _build_experiment(self, used_symbols: List[str]) -> str:
        if {'D', 'ΔC', 'L'}.issubset(set(used_symbols)):
            return 'Faire varier séparément ΔC et L dans un protocole identique puis mesurer la variation du débit ou du flux.'
        return "Mesurer Ndot pendant des variations contrôlées des variables validées utilisées par l'équation réparée."

    def _describe_fix(self, original_eq: str, repaired_eq: str, issue: str) -> str:
        if original_eq.strip() == repaired_eq.strip():
            return f"validation renforcée sans changement d'équation ({issue})"
        return f'remplacement ciblé de la forme bloquante par une loi cohérente ({issue})'

    def _pattern_for_issue(self, issue: str) -> str:
        low = (issue or '').lower()
        if 'variables non valid' in low:
            return 'unknown_symbol_cleanup'
        if 'absente' in low:
            return 'missing_equation_fill'
        if 'répét' in low or 'repet' in low:
            return 'signature_divergence'
        return 'minimal_physics_repair'

    def _uses_approved_symbol(self, equation: str, approved_symbols: List[str]) -> bool:
        low = equation.lower()
        return any(sym.lower() in low for sym in approved_symbols)

    def _extract_symbols(self, equation: str) -> List[str]:
        raw = re.findall(r'[A-Za-zΑ-Ωα-ω_Δ][A-Za-z0-9Α-Ωα-ω_Δ]*', equation or '')
        banned = {'d', 'dt', 't', 'x'}
        out: List[str] = []
        for sym in raw:
            if sym not in out and sym not in banned:
                out.append(sym)
        return out

    def _structure_signature(self, equation: str) -> str:
        eq = (equation or '').lower().strip().replace('é', 'e')
        eq = re.sub(r'\b[a-zA-Z_Δτφηκμρσλ][a-zA-Z0-9_Δτφηκμρσλ]*\b', 'X', eq)
        eq = re.sub(r'\b\d+(?:\.\d+)?\b', 'N', eq)
        eq = re.sub(r'\s+', '', eq)
        eq = re.sub(r'X+', 'X', eq)
        return eq
