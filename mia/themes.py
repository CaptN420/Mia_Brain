import random

import random

THEMES = [
    "transport",
    "diffusion",
    "energy transfer",
    "fluid dynamics",
    "thermodynamics",
    "signal propagation",
    "entropy flow",
    "mass conservation",
]

def random_theme():
    return random.choice(THEMES)

QUESTIONS = [
    "Quelle relation causale testable peut relier transport, transformation et mesure ?",
    "Comment traduire une évolution chimique en équation physique puis en lecture hermétique ?",
    "Quelle variable commune peut stabiliser une loi hybride entre diffusion et réaction ?",
    "Comment produire une équation courte, mesurable et expérimentalement vérifiable ?",
]


def random_theme():
    return random.choice(THEMES), random.choice(QUESTIONS)
