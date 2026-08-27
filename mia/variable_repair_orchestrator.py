from __future__ import annotations

from typing import Any, Dict, List, Tuple
import inspect
import re

from shared_memory import SharedResearchMemory, SharedVariable


EXPECTED_UNITS = {
    'flux': ['mol/m2/s', 'mol·m⁻²·s⁻¹', 'mol*m^-2*s^-1'],
    'concentration': ['mol/m3', 'mol·m⁻3', 'mol*m^-3'],
    'gradient': ['mol/m3', 'mol·m⁻3', 'mol*m^-3'],
    'diffusivite': ['m2/s', 'm²/s', 'm^2/s'],
    'transport': ['m2/s', 'm²/s', 'm^2/s'],
    'surface': ['m2', 'm²', 'm^2'],
    'structure': ['m2', 'm²', 'm^2', 'm'],
    'geometry': ['m2', 'm²', 'm^2', 'm'],
    'temps': ['s', 'sec', 'seconde'],
    'temps caracteristique': ['s', 'sec', 'seconde'],
    'resistance': ['1', 's/m', 's·m⁻¹', 's.m^-1'],
    'coefficient': ['1/s', 's^-1', 's⁻¹'],
    'cinetique': ['1/s', 's^-1', 's⁻¹'],
}

SYMBOL_HINTS = {
    'j': {'family': 'flux', 'unit': 'mol/m2/s'},
    'q': {'family': 'flux', 'unit': 'mol/m2/s'},
    'q_eff': {'family': 'flux', 'unit': 'mol/m2/s'},
    'c': {'family': 'concentration', 'unit': 'mol/m3'},
    'c_eff': {'family': 'concentration', 'unit': 'mol/m3'},
    's_eff': {'family': 'concentration', 'unit': 'mol/m3'},
    'Δc': {'family': 'gradient', 'unit': 'mol/m3'},
    'δc': {'family': 'gradient', 'unit': 'mol/m3'},
    'd': {'family': 'diffusivite', 'unit': 'm2/s'},
    'd_eff': {'family': 'diffusivite', 'unit': 'm2/s'},
    'x_diff': {'family': 'diffusivite', 'unit': 'm2/s'},
    's': {'family': 'surface', 'unit': 'm2'},
    's_diff': {'family': 'surface', 'unit': 'm2'},
    't_surf': {'family': 'surface', 'unit': 'm2'},
    'a': {'family': 'surface', 'unit': 'm2'},
    'l': {'family': 'geometry', 'unit': 'm'},
    'tau': {'family': 'temps caracteristique', 'unit': 's'},
    'τ': {'family': 'temps caracteristique', 'unit': 's'},
    'r': {'family': 'resistance', 'unit': '1'},
    'r_limit': {'family': 'resistance', 'unit': '1'},
}


def _normalize_unit_text(value: str) -> str:
    unit = str(value or '').strip().lower()
    unit = unit.replace('²', '2').replace('³', '3')
    unit = unit.replace('⁻', '-')
    unit = unit.replace('·', '/').replace('*', '/')
    unit = unit.replace(' ', '')
    unit = unit.replace('sec', 's').replace('seconde', 's')
    unit = unit.replace('m^-2', '/m2').replace('m^-3', '/m3')
    unit = unit.replace('s^-1', '1/s')
    unit = unit.replace('//', '/')
    return unit


class VariableRepairOrchestrator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.shared = SharedResearchMemory(cfg.session_dir)

    def run(self, turns: int = 1, target_variable: str | None = None) -> None:
        turns = max(1, int(turns or 1))
        for index in range(1, turns + 1):
            print(f"--- variable repair tour {index} ---")
            target = str(target_variable or getattr(self.cfg, 'target_variable', '') or '').strip()
            if not target:
                decision = self.shared.choose_next_repair_action()
                if str(decision.get('kind', '')) != 'variable':
                    print('[V-REPAIR] No variable target available')
                    return
                target = str(decision.get('target', '') or '').strip()
            if not target:
                print('[V-REPAIR] Empty target variable')
                return
            self._repair_variable(target)

    def _repair_variable(self, symbol: str) -> None:
        pending = self.shared.get_pending_variable(symbol) or {}
        approved_map = self.shared.get_approved_variables() or {}
        approved_names = {str(k).strip().lower(): str(k) for k in approved_map.keys()}
        norm = str(symbol or '').strip().lower()
        if norm in approved_names:
            base = dict(approved_map.get(approved_names[norm], {}) or {})
            pending = {**base, **pending}

        family = self._infer_family(symbol, pending)
        definition = self._infer_definition(symbol, pending, family)
        unit = self._infer_unit(symbol, pending, family)
        measure = self._infer_measure(symbol, pending, family)
        role = self._infer_role(symbol, pending, family)
        links = self._infer_links(symbol, pending, family)
        remarks: List[str] = []
        if pending:
            remarks.append('Variable repair synthesised from pending memory.')
        if not pending:
            remarks.append('Variable repair synthesised from symbol heuristics.')

        quality_score, quality_notes = self._evaluate_quality(
            symbol=symbol,
            family=family,
            definition=definition,
            unit=unit,
            measure=measure,
            role=role,
            links=links,
        )
        approved = quality_score >= 80
        needs_refinement = (quality_score >= 45) and not approved
        turn = self._next_turn()
        status = 'approved' if approved else ('needs_refinement' if needs_refinement else 'candidate')
        validation = (
            'Variable Repair V2 | ' +
            ('variable scientifiquement approuvée' if approved else ('variable à affiner avant approbation' if needs_refinement else 'variable consolidée partiellement')) +
            f' | symbol={symbol} | family={family or "unknown"} | quality={quality_score}/100'
        )

        required_next = []
        if not approved:
            required_next = list(quality_notes) or ['compléter définition/unité/mesure/rôle']

        payload_kwargs = {
            'name': str(symbol).strip(),
            'definition': definition,
            'unit': unit,
            'measure': measure,
            'role': role,
            'links': links,
            'remarks': remarks,
            'approved': approved,
            'source_turn': turn,
            'source_agent': 'VariableRepairCore',
            'validation_summary': validation,
            'required_next': required_next,
            'family': family,
            'micro_equation': self._infer_micro_equation(symbol, family),
            'experiment': self._infer_experiment(symbol, family),
            'status': status,
            'quality_score': quality_score,
            'quality_notes': quality_notes,
        }
        payload = self._build_shared_variable(payload_kwargs)
        self.shared.add_variable(payload)
        decision = 'approved' if approved else ('needs_refinement' if needs_refinement else 'needs_repair')
        self.shared.add_final_validation('variable', turn, validation, decision)
        self.shared.add_debate_summary('variable_repair', turn, f'{symbol} | approved={approved} | family={family} | quality={quality_score}/100')
        self.shared.add_repair_log({
            'turn': turn,
            'kind': 'variable',
            'symbol': str(symbol).strip(),
            'status': status,
            'main_issue': 'pending variable consolidation' if pending else 'symbol-only variable consolidation',
            'fix_applied': f'consolidated variable metadata for {symbol}',
            'pattern': 'variable_metadata_consolidation',
            'confidence': f'{quality_score}/100',
            'quality_notes': list(quality_notes),
        })
        self.shared.remove_pending_variable(symbol)
        print(f'[V-REPAIR] Variable: {symbol}')
        print(f'[V-REPAIR] Family: {family}')
        print(f'[V-REPAIR] Definition: {definition}')
        print(f'[V-REPAIR] Unit: {unit}')
        print(f'[V-REPAIR] Measure: {measure}')
        print(f'[V-REPAIR] Role: {role}')
        print(f'[V-REPAIR] Quality: {quality_score}/100')
        if quality_notes:
            print(f"[V-REPAIR] Quality notes: {', '.join(quality_notes)}")
        print(f'[V-REPAIR] Verdict: {status}')


    def _build_shared_variable(self, payload_kwargs: Dict[str, Any]) -> SharedVariable:
        try:
            supported = set(inspect.signature(SharedVariable).parameters.keys())
        except Exception:
            supported = set(payload_kwargs.keys())
        filtered = {k: v for k, v in payload_kwargs.items() if k in supported}
        payload = SharedVariable(**filtered)

        # Backward compatibility for older SharedVariable dataclasses that do not yet
        # declare the new scientific quality fields.
        if 'quality_score' not in supported:
            try:
                setattr(payload, 'quality_score', int(payload_kwargs.get('quality_score', 0) or 0))
            except Exception:
                pass
        if 'quality_notes' not in supported:
            try:
                setattr(payload, 'quality_notes', list(payload_kwargs.get('quality_notes', []) or []))
            except Exception:
                pass
        return payload

    def _next_turn(self) -> int:
        latest_var = dict(self.shared.data.get('last_validated_variable', {}) or {})
        latest_eq = self.shared.get_latest_equation_entry() or {}
        return max(int(latest_var.get('source_turn', 0) or 0), int(latest_eq.get('source_turn', 0) or 0)) + 1

    def _evaluate_quality(
        self,
        symbol: str,
        family: str,
        definition: str,
        unit: str,
        measure: str,
        role: str,
        links: List[str],
    ) -> tuple[int, List[str]]:
        score = 0
        notes: List[str] = []

        symbol_clean = str(symbol or '').strip()
        symbol_key = symbol_clean.lower()
        family_clean = str(family or '').strip().lower()
        definition_clean = str(definition or '').strip()
        definition_low = definition_clean.lower()
        unit_clean = str(unit or '').strip()
        unit_norm = _normalize_unit_text(unit_clean)
        measure_clean = str(measure or '').strip().lower()
        role_clean = str(role or '').strip()
        role_low = role_clean.lower()
        links_clean = [str(x).strip() for x in (links or []) if str(x).strip()]

        # Base completeness gate
        if family_clean and family_clean != 'generic':
            score += 15
        else:
            notes.append('family too generic')

        if definition_clean and not definition_low.startswith('variable scientifique '):
            score += 15
        else:
            notes.append('definition too generic')

        if unit_clean and unit_norm not in {'adéfinir', 'adefinir', 'unknown', '?', ''}:
            score += 15
        else:
            notes.append('unit missing or undefined')

        if measure_clean and 'à préciser' not in measure_clean and 'a preciser' not in measure_clean:
            score += 10
        else:
            notes.append('measure too vague')

        if role_clean and not role_low.startswith('rôle scientifique de ') and not role_low.startswith('role scientifique de '):
            score += 10
        else:
            notes.append('role too generic')

        if links_clean:
            score += 5
        else:
            notes.append('missing scientific links')

        # Symbol-family coherence
        hint = SYMBOL_HINTS.get(symbol_key)
        if hint:
            expected_family = str(hint.get('family', '') or '').lower()
            expected_unit = _normalize_unit_text(str(hint.get('unit', '') or ''))
            if family_clean == expected_family:
                score += 10
            else:
                score -= 15
                notes.append(f'name/family mismatch: {symbol_clean} suggests {expected_family}')
            if expected_unit and unit_norm == expected_unit:
                score += 10
            elif expected_unit:
                score -= 15
                notes.append(f'name/unit mismatch: {symbol_clean} suggests {hint.get("unit")}')

        # Family-unit consistency
        allowed_units = EXPECTED_UNITS.get(family_clean, [])
        if allowed_units:
            normalized_allowed = {_normalize_unit_text(x) for x in allowed_units}
            if unit_norm in normalized_allowed:
                score += 10
            else:
                score -= 20
                notes.append(f'unit inconsistent with family {family_clean}')

        # Family-definition-role consistency
        if family_clean in {'flux'}:
            if any(k in definition_low for k in ['flux', 'surfac', 'interface', 'transfert']):
                score += 5
            else:
                score -= 10
                notes.append('definition inconsistent with flux')
            if 'transfert surfacique' in role_low or 'flux' in role_low:
                score += 5
            else:
                score -= 10
                notes.append('role inconsistent with flux')

        elif family_clean in {'concentration', 'gradient'}:
            if any(k in definition_low for k in ['concentration', 'gradient']):
                score += 5
            else:
                score -= 10
                notes.append(f'definition inconsistent with {family_clean}')
            if any(k in role_low for k in ['force motrice', 'gradient', 'concentration']):
                score += 5
            else:
                score -= 10
                notes.append(f'role inconsistent with {family_clean}')

        elif family_clean in {'diffusivite', 'transport'}:
            if any(k in definition_low for k in ['diffus', 'transport']):
                score += 5
            else:
                score -= 10
                notes.append(f'definition inconsistent with {family_clean}')
            if any(k in role_low for k in ['propagation', 'diffusion', 'transport']):
                score += 5
            else:
                score -= 10
                notes.append(f'role inconsistent with {family_clean}')

        elif family_clean in {'surface', 'structure', 'geometry'}:
            if any(k in definition_low for k in ['surface', 'aire', 'interface', 'longueur', 'epaisseur', 'épaisseur']):
                score += 5
            else:
                score -= 10
                notes.append(f'definition inconsistent with {family_clean}')
            if any(k in role_low for k in ['géométr', 'geometr', 'surface', 'contrainte']):
                score += 5
            else:
                score -= 10
                notes.append(f'role inconsistent with {family_clean}')

        elif family_clean in {'temps', 'temps caracteristique'}:
            if any(k in definition_low for k in ['temps', 'durée', 'duree', 'relaxation']):
                score += 5
            else:
                score -= 10
                notes.append('definition inconsistent with time variable')
            if any(k in role_low for k in ['temps', 'durée', 'duree', 'échelle temporelle', 'echelle temporelle']):
                score += 5
            else:
                score -= 10
                notes.append('role inconsistent with time variable')

        elif family_clean in {'resistance'}:
            if any(k in definition_low for k in ['résistance', 'resistance', 'limitation', 'frein']):
                score += 5
            else:
                score -= 10
                notes.append('definition inconsistent with resistance')
            if any(k in role_low for k in ['freine', 'borne', 'limite', 'résistance', 'resistance']):
                score += 5
            else:
                score -= 10
                notes.append('role inconsistent with resistance')

        # Penalize common placeholder symbols with weak semantics
        if symbol_key in {'x', 'y', 'z'}:
            score -= 10
            notes.append('symbol too generic without domain anchoring')

        if len(definition_clean) < 18:
            score -= 5
            notes.append('definition too short')

        score = max(0, min(100, score))
        notes = list(dict.fromkeys([n for n in notes if n]))
        return score, notes

    def _infer_family(self, symbol: str, payload: Dict[str, Any]) -> str:
        family = str(payload.get('family', '') or '').strip()
        if family:
            return family
        sym = str(symbol or '').strip()
        hint = SYMBOL_HINTS.get(sym.lower())
        if hint:
            return str(hint.get('family', '') or '').strip() or 'generic'
        if sym in {'ΔC', 'C', 'Cin', 'Cout'}:
            return 'gradient'
        if sym.lower().startswith('k'):
            return 'coefficient'
        return 'generic'

    def _infer_definition(self, symbol: str, payload: Dict[str, Any], family: str) -> str:
        definition = str(payload.get('definition', '') or '').strip()
        if definition:
            return definition
        sym = str(symbol or '').strip()
        defaults = {
            'D': 'coefficient de diffusion effectif',
            'ΔC': 'gradient de concentration',
            'L': 'longueur caractéristique de diffusion',
            'A': 'surface active de transfert',
            'J': 'flux surfacique de transfert',
            'R': 'résistance ou ratio limitant du transfert',
            'R_limit': 'borne limite de résistance du système',
            'tau': 'temps caractéristique du processus de transfert',
            'τ': 'temps caractéristique du processus de transfert',
            'q_eff': 'flux effectif de matière à travers une surface active',
            's_eff': 'concentration effective de matière dans le milieu',
            's_diff': 'surface effective de diffusion',
            't_surf': 'surface active de transfert du système',
            'x_diff': 'diffusivité effective du milieu',
        }
        if sym in defaults:
            return defaults[sym]
        if family == 'coefficient':
            return f'coefficient effectif {sym}'
        return f'variable scientifique {sym}'

    def _infer_unit(self, symbol: str, payload: Dict[str, Any], family: str) -> str:
        unit = str(payload.get('unit', '') or '').strip()
        if unit:
            return unit
        sym = str(symbol or '').strip()
        defaults = {
            'D': 'm2/s', 'ΔC': 'mol/m3', 'L': 'm', 'A': 'm2', 'J': 'mol/m2/s', 'R': '1', 'R_limit': '1',
            'tau': 's', 'τ': 's', 'q_eff': 'mol/m2/s', 's_eff': 'mol/m3', 's_diff': 'm2', 't_surf': 'm2', 'x_diff': 'm2/s'
        }
        if sym in defaults:
            return defaults[sym]
        if family == 'coefficient':
            return '1/s'
        return 'a définir'

    def _infer_measure(self, symbol: str, payload: Dict[str, Any], family: str) -> str:
        measure = str(payload.get('measure', '') or '').strip()
        if measure:
            return measure
        if family in {'geometry', 'surface', 'structure'}:
            return 'mesure géométrique contrôlée'
        if family in {'transport', 'diffusivite', 'flux', 'gradient', 'coefficient', 'resistance', 'concentration', 'temps caracteristique'}:
            return 'mesure instrumentale ou estimation calibrée'
        return 'mesure expérimentale à préciser'

    def _infer_role(self, symbol: str, payload: Dict[str, Any], family: str) -> str:
        role = str(payload.get('role', '') or '').strip()
        if role:
            return role
        if family in {'geometry', 'surface', 'structure'}:
            return 'contrainte géométrique du mécanisme de transfert'
        if family in {'gradient', 'concentration'}:
            return 'force motrice ou état local du transport'
        if family == 'flux':
            return 'quantifie le transfert surfacique'
        if family in {'transport', 'diffusivite'}:
            return 'règle la vitesse de diffusion ou propagation'
        if family == 'resistance':
            return 'freine ou borne le transfert'
        if family in {'coefficient', 'cinetique'}:
            return 'paramètre effectif de calibration ou de vitesse'
        if family == 'temps caracteristique':
            return 'décrit l’échelle temporelle du processus'
        return f'rôle scientifique de {symbol}'

    def _infer_links(self, symbol: str, payload: Dict[str, Any], family: str) -> List[str]:
        links = list(payload.get('links', []) or [])
        if links:
            return list(dict.fromkeys([str(x).strip() for x in links if str(x).strip()]))[:8]
        sym = str(symbol or '').strip()
        out = []
        if family in {'gradient', 'transport', 'diffusivite', 'flux', 'coefficient', 'concentration'}:
            out.append(f'{sym} peut modifier Ndot selon le contexte expérimental')
        if family in {'geometry', 'surface', 'structure', 'resistance', 'temps caracteristique'}:
            out.append(f'{sym} peut contraindre ou ralentir Ndot selon le contexte')
        return out[:4]

    def _infer_micro_equation(self, symbol: str, family: str) -> str:
        sym = str(symbol or '').strip()
        if sym == 'J':
            return 'Ndot = J * A'
        if sym == 'D':
            return 'J = (D * ΔC) / L'
        if family == 'resistance':
            return 'Ndot = k_eff / (1 + R)'
        return ''

    def _infer_experiment(self, symbol: str, family: str) -> str:
        sym = str(symbol or '').strip()
        if sym in {'ΔC', 'D', 'L'}:
            return f'faire varier {sym} dans un protocole contrôlé et mesurer la réponse de flux ou de débit'
        if sym in {'A', 'J'}:
            return f'corréler {sym} avec Ndot sur plusieurs conditions expérimentales'
        return f'valider expérimentalement le rôle de {sym} dans la loi cible'
