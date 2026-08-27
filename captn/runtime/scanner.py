from dataclasses import dataclass, field
from typing import List, Optional
import os
import json

@dataclass
class ProjectManifest:
    root: str
    python_files: List[str] = field(default_factory=list)
    entry_points: List[str] = field(default_factory=list)
    excluded_paths: List[str] = field(default_factory=lambda: [
        ".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build"
    ])
    venv: Optional[str] = None
    git_repo: bool = False
    total_files: int = 0

    def to_json(self) -> str:
        return json.dumps({
            "root": self.root,
            "python_files": self.python_files,
            "entry_points": self.entry_points,
            "excluded_paths": self.excluded_paths,
            "venv": self.venv,
            "git": self.git_repo
        }, indent=4)

class ProjectScanner:
    def __init__(self, root_path: str):
        self.root_path = os.path.abspath(root_path)
        self.manifest = ProjectManifest(root=self.root_path)

    def scan(self) -> ProjectManifest:
        if not os.path.isdir(self.root_path):
            raise ValueError(f"Path {self.root_path} is not a directory.")

        # 1. Count total files and find Python files
        python_count = 0
        file_count = 0
        
        for root, dirs, files in os.walk(self.root_path):
            # Filter out excluded directories in-place
            dirs[:] = [d for d in dirs if d not in self.manifest.excluded_paths]
            
            for file in files:
                file_count += 1
                if file.endswith(".py"):
                    python_count += 1
                    rel_path = os.path.relpath(os.path.join(root, file), self.root_path)
                    self.manifest.python_files.append(rel_path)

        self.manifest.total_files = file_count

        # 2. Detect Entry Points
        entry_points = ["main.py", "app.py", "run.py"]
        for ep in entry_points:
            if os.path.exists(os.path.join(self.root_path, ep)):
                self.manifest.entry_points.append(ep)

        # 3. Detect Venv
        venv_names = [".venv", "venv", "env"]
        for v in venv_names:
            v_path = os.path.join(self.root_path, v)
            if os.path.isdir(v_path):
                self.manifest.venv = v_path
                break

        # 4. Detect Git
        try:
            git_dir = os.path.join(self.root_path, ".git")
            self.manifest.git_repo = os.path.isdir(git_dir)
        except Exception:
            self.manifest.git_repo = False

        return self.manifest
