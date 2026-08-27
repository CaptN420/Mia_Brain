# MIA - Multi-Agent Intelligence Alchemy Makefile
# Use 'make <target>' to run the corresponding command.

PYTHON := /home/captn/hermes/MIA-LABS-main/mia/venv/bin/python3
REQUIREMENTS := requirement.txt
PROJECT_DIR := $(shell pwd)

.PHONY: help install run ui evolve merge clean

help:
	@echo "MIA Makefile Commands:"
	@echo "  make install   - Install dependencies into the virtual environment"
	@echo "  make run        - Run the standard launcher"
	@echo "  make ui         - Run the graphical UI launcher"
	@echo "  make loop       - Run the evolution loop (default args)"
	@echo "  make evolve     - Run the full evolution mode"
	@echo "  make merge      - Run the session merger"
	@echo "  make clean      - Remove python cache files"

install:
	@echo "Installing dependencies..."
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r $(REQUIREMENTS)

run:
	$(PYTHON) launcher.py

ui:
	$(PYTHON) ui_launcher.py

loop:
	$(PYTHON) launcher.py loop 1 1 1 1 --create-new-session

evolve:
	$(PYTHON) launcher.py full-evolve 50 2 2 6 2 --create-new-session

merge:
	$(PYTHON) master_merge.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	@echo "Cache cleared."
